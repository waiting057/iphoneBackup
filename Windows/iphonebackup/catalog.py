"""USB file inventory spike. Full Photos/Voice Memos asset coverage is unverified."""
from datetime import datetime
from pathlib import PurePosixPath
from threading import Event

from .models import Catalog, MediaItem
from .usb import AFCError, Session
from .photo_metadata import inspect_photo, IMAGE_EXTENSIONS
from .photo_library import library_dates
from .voice_usb import scan_voice_apps, VOICE_EXTENSIONS

PHOTOS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".tif", ".tiff", ".dng", ".mov", ".mp4", ".m4v"}
AUDIO = VOICE_EXTENSIONS
SIDECARS = {".aae", ".xmp"}


def scan(udid: str, category: str, cancel: Event, progress=lambda count: None) -> Catalog:
    root = "/DCIM" if category == "photos" else "/Recordings"
    extensions = PHOTOS if category == "photos" else AUDIO
    warning = "這是 USB 可讀取檔案清單，尚未核對手機全部內容。Live Photo 配對、編輯版本及 iCloud 內容仍需實機驗證。"
    with Session(udid) as session:
        if category == "voice":
            # Classify only a failure opening the root as unavailable. A later
            # failure reading one file must not be disguised as an empty list.
            try:
                session.listdir(root)
            except AFCError as exc:
                if exc.code not in (8, 10, 15):
                    raise
                reason = {8: "目前 USB 介面沒有提供 /Recordings 錄音目錄。",
                          10: "iPhone 拒絕目前 USB 介面存取錄音目錄。",
                          15: "iPhone 不支援目前使用的錄音目錄讀取方式。"}[exc.code]
                return scan_voice_apps(udid, cancel, progress, [reason, str(exc)])
        try:
            files = []
            for file in session.walk(root, cancel):
                files.append(file)
                if len(files) % 100 == 0:
                    progress(len(files))
        except AFCError as exc:
            if category == "voice":
                raise
            if exc.code in (8, 10, 15):
                return Catalog((), (f"無法透過目前的 USB 方式讀取{ '照片與影片' if category == 'photos' else '語音備忘錄' }，不能判定手機沒有內容。", str(exc)))
            raise
        # Do not infer Live Photos merely from matching filenames. The initial
        # inventory counts files, not Photos assets, until metadata is validated.
        companions = {}
        for file in files:
            path = PurePosixPath(file.path)
            if path.suffix.lower() in SIDECARS:
                companions.setdefault((str(path.parent), path.stem.casefold()), []).append(file)
        items = []
        dates, date_warning = library_dates(session, cancel) if category == "photos" else ({}, None)
        for file in files:
            if cancel.is_set():
                raise RuntimeError("已取消讀取清單。")
            path = PurePosixPath(file.path)
            if path.suffix.lower() not in extensions:
                continue
            captured, date_source, thumbnail, preview_error = None, None, None, None
            if category == "photos":
                captured, date_source, thumbnail, preview_error = inspect_photo(session, file, cancel)
                if captured is None and file.path in dates:
                    captured, date_source = dates[file.path]
            associated = tuple(companions.pop((str(path.parent), path.stem.casefold()), []))
            items.append(MediaItem(file.path, path.name, category, (file, *associated), captured, thumbnail, date_source, preview_error))
            progress(len(items))
        items.sort(key=lambda item: (item.captured is not None, item.captured or datetime.min, item.title), reverse=True)
        if category == "voice":
            warning = "這是 USB 可讀取錄音檔案清單，尚未與語音備忘錄核對；錄製日期、名稱、音層及編輯資訊仍需驗證。"
            if not items:
                return scan_voice_apps(udid, cancel, progress, [f"/Recordings 中讀到 {len(files)} 個檔案，沒有可辨識的錄音項目，無法確認手機錄音數量。"])
        unknown_files = sum(PurePosixPath(file.path).suffix.lower() not in extensions | SIDECARS for file in files)
        warnings = [warning, "缺少拍攝／錄製日期的項目放在「日期未知」，不使用檔案修改時間代替。"]
        if date_warning:
            warnings.append(date_warning)
        missing_dates = sum(item.captured is None for item in items)
        if category == "photos" and items:
            missing_previews = sum(PurePosixPath(item.title).suffix.lower() in IMAGE_EXTENSIONS and not item.thumbnail for item in items)
            warnings.append(f"拍攝日期：已取得 {len(items) - missing_dates}／{len(items)} 筆；照片預覽：{missing_previews} 張未能解碼。滑鼠停在卡片可查看日期來源與預覽狀態。")
            if any(item.date_source and "UTC" in item.date_source for item in items):
                warnings.append("部分資料庫日期只有 UTC 時間，跨月邊界可能與手機依拍攝時區顯示的月份不同。")
        if unknown_files:
            warnings.append(f"另有 {unknown_files} 個未辨識檔案；目前清單可能缺少相關素材。")
        orphan_sidecars = sum(len(group) for group in companions.values())
        if orphan_sidecars:
            warnings.append(f"另有 {orphan_sidecars} 個輔助檔尚未找到對應素材，未納入選取清單；編輯資訊保存尚不完整。")
        return Catalog(tuple(items), tuple(warnings))
