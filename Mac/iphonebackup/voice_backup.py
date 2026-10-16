"""Owned temporary iPhone backups. Never restore or alter encryption settings."""
import base64
import json
import os
import plistlib
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse
from threading import Event, Lock

from .exporter import Cancelled, ExportError
from .models import Catalog, DeviceFile, MediaItem, format_size

GIB = 1024 ** 3
PREFIX = '.iphonebackup-voice-'
MARKER = '.iphonebackup-owned.json'
LEASE = '.active-lock'
_LEASES = {}
_CLEANUP_LOCK = Lock()
AUDIO = {'.m4a', '.wav', '.caf', '.aif', '.aiff', '.mp3', '.qta'}
DOMAINS = {'AppDomain-com.apple.VoiceMemos', 'AppDomainGroup-group.com.apple.VoiceMemos.shared'}


def tool(name):
    executable = name + ('.exe' if sys.platform == 'win32' else '')
    if getattr(sys, 'frozen', False):
        found = Path(sys._MEIPASS) / 'native' / executable
        if found.is_file():
            return str(found)
    else:
        found = shutil.which(executable)
        if found:
            return found
        for folder in ('/opt/homebrew/bin', '/usr/local/bin'):
            found = Path(folder) / executable
            if found.is_file():
                return str(found)
    raise ExportError(f'缺少 {executable}，請安裝 libimobiledevice 或重新建置包含備份工具的 App。')


def process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}


def check_encryption(udid):
    result = subprocess.run([tool('ideviceinfo'), '-u', udid, '-q', 'com.apple.mobile.backup', '-k', 'WillEncrypt'],
                            capture_output=True, text=True, timeout=30, **process_options())
    value = result.stdout.strip().lower()
    if result.returncode or value not in {'true', 'false'}:
        raise ExportError('無法確認手機備份加密狀態。請解鎖、信任電腦後再試。')
    if value == 'true':
        raise ExportError('此 iPhone 已開啟備份加密，目前版本尚不支援解密讀取，因此未開始備份。App 不會關閉加密或變更手機設定。')


def acquire_lease(folder):
    path = Path(folder) / LEASE
    if path.is_symlink():
        raise ExportError('暫存鎖定檔無效。')
    handle = path.open('a+b')
    try:
        if sys.platform == 'win32':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close()
        raise


def active_elsewhere(folder):
    if str(folder) in _LEASES:
        return True
    try:
        handle = acquire_lease(folder)
        handle.close()
        return False
    except OSError:
        return True


def usage(folder):
    """Sample a live tree while the backup writer moves Snapshot entries.

    A disappearing descendant is normal during this scan. Permission and I/O
    errors must still stop the job, and a missing job root must not look empty.
    One lstat per entry avoids a separate existence-check/stat race and excludes
    symlinks without following them outside the owned backup directory.
    """
    root = Path(folder)
    pending = [root]
    total = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        snapshot = entry.stat(follow_symlinks=False)
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                    if stat.S_ISDIR(snapshot.st_mode):
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(snapshot.st_mode):
                        total += snapshot.st_size
        except (FileNotFoundError, NotADirectoryError):
            if directory == root:
                raise
            # A queued Snapshot subdirectory may be moved before we open it.
            continue
    return total


def owned(folder):
    folder = Path(folder).absolute()
    if folder.is_symlink() or not folder.name.startswith(PREFIX):
        raise ExportError('拒絕清除非 App 專屬的暫存資料夾。')
    marker = folder / MARKER
    if marker.is_symlink():
        raise ExportError('暫存工作標記無效。')
    data = json.loads(marker.read_text(encoding='utf-8'))
    if data != {'version': 1, 'folder': str(folder)}:
        raise ExportError('暫存工作標記不符，未清除檔案。')
    return folder


def cleanup(folder):
    with _CLEANUP_LOCK:
        _cleanup(folder)


def _cleanup(folder):
    folder = Path(folder)
    if not folder.exists():
        return
    owned(folder)
    handle = _LEASES.pop(str(folder), None)
    if handle is None:
        try:
            handle = acquire_lease(folder)
        except OSError as error:
            raise ExportError(f'另一個 App 仍在使用此備份，未清除：{folder}') from error
    # Retain the marker until all other entries are gone, so failed cleanup is
    # discoverable and retryable. Never follow directory symlinks.
    errors = []
    for child in folder.iterdir():
        if child.name in (MARKER, LEASE):
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError as error:
            errors.append(str(error))
    handle.close()
    if not errors:
        try:
            (folder / LEASE).unlink(missing_ok=True)
            (folder / MARKER).unlink()
            folder.rmdir()
        except OSError as error:
            # An empty directory can still be left behind on a restricted host.
            if folder.exists():
                (folder / MARKER).write_text(json.dumps({'version': 1, 'folder': str(folder)}), encoding='utf-8')
            errors.append(str(error))
    if errors:
        raise ExportError(f'暫存清除未完成：{folder}\n剩餘 {format_size(usage(folder))}\n' + '\n'.join(errors[:3]))


def leftovers(parents):
    result = []
    for parent in parents:
        try:
            for path in Path(parent).glob(PREFIX + '*'):
                try:
                    candidate = owned(path)
                    if not active_elsewhere(candidate):
                        result.append(candidate)
                except (OSError, ValueError, ExportError):
                    pass
        except OSError:
            pass
    return tuple(result)


def create_job(parent):
    folder = Path(tempfile.mkdtemp(prefix=PREFIX, dir=parent)).absolute()
    (folder / MARKER).write_text(json.dumps({'version': 1, 'folder': str(folder)}), encoding='utf-8')
    _LEASES[str(folder)] = acquire_lease(folder)
    return folder


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def create_backup(folder, udid, limit, reserve, cancel, progress):
    """The watchdog is a soft limit: one write can exceed the sampled limit."""
    folder = owned(folder)
    if not re.fullmatch(r'[A-Za-z0-9-]{8,80}', udid):
        raise ExportError('裝置識別碼無效。')
    if limit <= 0 or reserve < GIB:
        raise ExportError('備份上限須大於零，保留空間至少 1 GB。')
    if shutil.disk_usage(folder).free < reserve + GIB:
        raise ExportError('暫存位置空間不足，尚未開始備份。')
    check_encryption(udid)
    with (folder / 'backup.log').open('wb') as log:
        process = subprocess.Popen([tool('idevicebackup2'), '-u', udid, 'backup', str(folder)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   **process_options())
        try:
            while True:
                if cancel.is_set():
                    raise Cancelled('已取消備份。')
                size = usage(folder)
                progress(f'建立手機備份中 · 暫存 {format_size(size)} · 請留意手機解鎖／密碼提示')
                if size > limit or shutil.disk_usage(folder).free < reserve:
                    raise ExportError('已達備份容量上限或可用空間過低，備份已停止。')
                code = process.poll()
                if code is not None:
                    if code:
                        raise ExportError(f'手機備份失敗（工具代碼 {code}）。請確認手機已解鎖、授權並保持 USB 連接。')
                    break
                cancel.wait(1)
        finally:
            # Cleanup must happen only after the external writer has exited.
            stop_process(process)
    root = folder / udid
    with (root / 'Status.plist').open('rb') as stream:
        if plistlib.load(stream).get('SnapshotState') != 'finished':
            raise ExportError('手機備份尚未完成，不能讀取為成功清單。')
    with (root / 'Manifest.plist').open('rb') as stream:
        if plistlib.load(stream).get('IsEncrypted'):
            raise ExportError('手機產生加密備份，目前不支援解密，將清除此暫存。')
    return BackupReader(root)


class BackupReader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files = {}

    def catalog(self, cancel, progress):
        database = self.root / 'Manifest.db'
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        entries = []
        try:
            for file_id, domain, relative, flags in connection.execute('SELECT fileID, domain, relativePath, flags FROM Files'):
                if cancel.is_set():
                    raise Cancelled('已取消讀取備份。')
                if flags != 1:
                    continue
                path = PurePosixPath(relative)
                voice = domain in DOMAINS or (domain == 'MediaDomain' and relative.startswith('Recordings/'))
                if not voice:
                    continue
                if not re.fullmatch('[0-9a-fA-F]{40}', file_id) or '..' in path.parts or path.is_absolute():
                    raise ExportError('備份索引包含無效路徑。')
                actual = self.root / file_id[:2] / file_id
                if not actual.is_file():
                    actual = self.root / file_id
                if not actual.is_file() or not actual.resolve().is_relative_to(self.root):
                    raise ExportError(f'備份缺少必要語音素材：{relative}')
                stat = actual.stat()
                key = domain + '/' + relative
                file = DeviceFile(key, stat.st_size, str(stat.st_mtime_ns), 'backup')
                self.files[key] = actual
                entries.append(file)
                progress(f'讀取備份語音資料 · {len(entries)} 個檔案')
        finally:
            connection.close()
        metadata, recognized, missing = self.recording_metadata(entries, cancel)
        audio = [file for file in entries if PurePosixPath(file.path).suffix.lower() in AUDIO]
        items, deleted, claimed = [], 0, set()
        for file in audio:
            detail = metadata.get(file.path)
            if detail and detail['deleted']:
                deleted += 1
                continue
            if recognized and detail is None:
                continue
            stem = str(PurePosixPath(file.path).with_suffix(''))
            components = [file]
            for auxiliary in entries:
                path = auxiliary.path
                if path.startswith(stem + '.composition/') or path == stem + '.composition' or re.fullmatch(re.escape(stem) + r'-track[0-9]+\.waveform', path):
                    components.append(auxiliary)
            # Composition manifests explicitly reference original fragments and
            # rendered outputs. Resolve URLs; never guess association by prefix.
            for auxiliary in tuple(components):
                if PurePosixPath(auxiliary.path).name == 'manifest.plist':
                    if auxiliary.size > 4 * 1024 * 1024:
                        raise ExportError('錄音組成資訊過大，無法安全辨識完整素材。')
                    try:
                        composition = plistlib.loads(self.files[auxiliary.path].read_bytes())
                    except (ValueError, plistlib.InvalidFileException) as error:
                        raise ExportError('無法解析錄音組成資訊，未省略素材。') from error
                    for reference in self.composition_urls(composition):
                        decoded = unquote(urlparse(reference).path)
                        matches = [entry for entry in entries if decoded.endswith('/' + entry.path.split('/', 1)[1])]
                        if not matches:
                            matches = [entry for entry in entries if PurePosixPath(entry.path).name == PurePosixPath(decoded).name]
                        if len(matches) != 1:
                            raise ExportError('錄音組成資訊指向缺失或無法唯一辨識的素材。')
                        if matches[0] not in components:
                            components.append(matches[0])
            for component in components:
                if component.path in claimed:
                    raise ExportError('多筆錄音共用素材，尚無法安全建立獨立匯出項目。')
                claimed.add(component.path)
            title = detail['title'] if detail else PurePosixPath(file.path).name
            items.append(MediaItem(file.path, title, 'voice', tuple(components),
                captured=detail['captured'] if detail else None,
                date_source='CloudRecordings.db ZDATE (UTC)' if detail and detail['captured'] else None,
                duration_seconds=detail['duration'] if detail else None,
                recording_metadata=detail['raw'] if detail else None))
        if missing:
            raise ExportError(f'備份中有 {missing} 筆未移除錄音，但找不到其唯一對應音訊，無法提供完整清單。')
        if not audio and not recognized:
            raise ExportError('備份中沒有辨識到可讀取的語音備忘錄音訊；不能判定手機沒有錄音。')
        warnings = [f'本機備份中辨識到 {len(items)} 筆未移除錄音；已排除 {deleted} 筆帶移除標記的音訊。',
            '每張卡片代表一筆錄音；對應波形、組成與編輯素材會隨錄音匯出，資料庫不另算錄音。',
            '名稱、錄製日期、時長與本筆資料庫記錄保存在 ZIP 匯出清單；不附帶其他錄音的整份資料庫。',
            '完整 iCloud、音層及編輯素材涵蓋範圍仍須實機驗證。',
            '未取得可信錄製日期時顯示日期未知，不以備份時間代替。']
        unmatched = {file.path for file in audio} - set(metadata) - claimed
        if recognized and unmatched:
            warnings.append(f'另有 {len(unmatched)} 個音訊檔無法對應錄音資料庫，未將它們猜測成目前錄音；完整性仍須核對。')
        if not recognized:
            warnings.append('資料庫格式未辨識，僅依音訊檔列出，無法判定是否已移除。')
        return Catalog(tuple(sorted(items, key=lambda item: (item.captured or datetime.min.replace(tzinfo=timezone.utc), item.title), reverse=True)), tuple(warnings))

    @staticmethod
    def composition_urls(value):
        if isinstance(value, str) and value.startswith('file://'):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from BackupReader.composition_urls(child)
        elif isinstance(value, list):
            for child in value:
                yield from BackupReader.composition_urls(child)

    def recording_metadata(self, entries, cancel):
        """Read known Core Data rows from a small local DB/WAL copy only.

        ZEVICTIONDATE is the removal marker in the observed Voice Memos schema.
        Never infer deletion from unrelated ZFLAGS bits or file timestamps.
        """
        result, recognized, missing = {}, False, 0
        audio = [file for file in entries if PurePosixPath(file.path).suffix.lower() in AUDIO]
        for database in entries:
            if PurePosixPath(database.path).name != 'CloudRecordings.db':
                continue
            if cancel.is_set():
                raise Cancelled('已取消讀取錄音日期。')
            with tempfile.TemporaryDirectory(prefix='iphonebackup-voice-db-', ignore_cleanup_errors=True) as scratch:
                staging = Path(scratch)
                target = staging / 'CloudRecordings.db'
                shutil.copyfile(self.files[database.path], target)
                for suffix in ('-wal', '-shm'):
                    source = self.files.get(database.path + suffix)
                    if source:
                        shutil.copyfile(source, staging / (target.name + suffix))
                connection = None
                try:
                    connection = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)
                    connection.row_factory = sqlite3.Row
                    columns = {row[1] for row in connection.execute('PRAGMA table_info(ZCLOUDRECORDING)')}
                    if not {'ZPATH', 'ZDATE'}.issubset(columns):
                        continue
                    recognized = True
                    for row in connection.execute('SELECT * FROM ZCLOUDRECORDING'):
                        if cancel.is_set():
                            raise Cancelled('已取消讀取錄音日期。')
                        raw = dict(row)
                        path, stamp = raw.get('ZPATH'), raw.get('ZDATE')
                        deleted = raw.get('ZEVICTIONDATE') is not None
                        if not isinstance(path, str) or not path:
                            if not deleted:
                                missing += 1
                            continue
                        relative = unquote(urlparse(path).path) if path.startswith('file://') else path
                        matches = [file for file in audio if relative.endswith('/' + file.path.split('/', 1)[1])]
                        if not matches:
                            matches = [file for file in audio if PurePosixPath(file.path).name == PurePosixPath(relative).name]
                        if len(matches) != 1:
                            if not deleted:
                                missing += 1
                            continue
                        title = next((raw.get(field) for field in ('ZENCRYPTEDTITLE', 'ZCUSTOMLABELFORSORTING', 'ZCUSTOMLABEL') if isinstance(raw.get(field), str) and raw.get(field).strip()), PurePosixPath(path).name)
                        captured = None
                        if isinstance(stamp, (int, float)) and stamp > 0:
                            try:
                                candidate = datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=stamp)
                                if candidate <= datetime.now(timezone.utc) + timedelta(days=1):
                                    captured = candidate
                            except (OverflowError, ValueError):
                                pass
                        duration = raw.get('ZDURATION')
                        if not isinstance(duration, (int, float)) or not 0 <= duration < 1e12:
                            duration = None
                        serialized = {key: {'encoding': 'base64', 'data': base64.b64encode(value).decode('ascii')} if isinstance(value, bytes) else value for key, value in raw.items()}
                        result[matches[0].path] = dict(title=title, captured=captured, duration=duration, deleted=deleted, raw=serialized)
                except sqlite3.Error as error:
                    raise ExportError('無法讀取錄音資料庫，未退回可能包含已移除項目的檔案清單。') from error
                finally:
                    if connection:
                        connection.close()
                    for file in staging.iterdir():
                        if file.is_file():
                            file.unlink()
        return result, recognized, missing

    def chunks(self, file):
        actual = self.files.get(file.path)
        if file.source != 'backup' or actual is None:
            raise ExportError('來源不是本次備份。')
        def check():
            stat = actual.stat()
            if stat.st_size != file.size or str(stat.st_mtime_ns) != file.modified_ns:
                raise ExportError('備份來源檔案已變更。')
        check()
        with actual.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        check()


def open_existing(folder):
    folder = Path(folder).resolve()
    if (folder / 'Manifest.db').is_file():
        root = folder
    else:
        choices = [path.parent for path in folder.glob('*/Manifest.db')]
        if not choices:
            choices = [path.parent for path in folder.glob('.iphonebackup-voice-*/*/Manifest.db')]
        if len(choices) != 1:
            raise ExportError('請選擇包含 Manifest.db 的已完成裝置備份資料夾；目前找不到唯一備份。')
        root = choices[0]
    with (root / 'Status.plist').open('rb') as stream:
        if plistlib.load(stream).get('SnapshotState') != 'finished':
            raise ExportError('此備份尚未完成，不能讀取。')
    with (root / 'Manifest.plist').open('rb') as stream:
        if plistlib.load(stream).get('IsEncrypted'):
            raise ExportError('目前不支援加密備份解密。')
    return BackupReader(root)
