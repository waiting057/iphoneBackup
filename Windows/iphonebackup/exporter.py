"""Transactional ZIP64 export; independent of UI and USB transport."""
import hashlib
import ctypes
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Callable, Iterable

from .models import DeviceFile, MediaItem


class ExportError(Exception):
    pass


class Cancelled(ExportError):
    pass


@dataclass(frozen=True)
class Progress:
    phase: str
    completed_bytes: int
    total_bytes: int
    completed_files: int
    total_files: int


def safe_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .")
    if not name or name in (".", ".."):
        name = "file"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    # Leave room for suffixes and extraction on Windows.
    return name[:160]


def publish_no_replace(temporary: Path, destination: Path):
    """Commit atomically without replacing any concurrently created output."""
    if sys.platform == "win32":
        # On Windows os.rename fails if destination already exists.
        os.rename(temporary, destination)
    elif sys.platform == "darwin":
        # RENAME_EXCL is declared in the macOS SDK's sys/stdio.h. Unlike a
        # hardlink, this also works on supported external drive filesystems.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(os.fsencode(temporary), os.fsencode(destination), 4):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))
    else:
        os.link(temporary, destination)
        temporary.unlink()


def export_zip(
    items: Iterable[MediaItem],
    destination: Path,
    read: Callable[[DeviceFile], Iterable[bytes]],
    cancel: Event | None = None,
    progress: Callable[[Progress], None] | None = None,
) -> Path:
    """Freeze selection, stream files, reread ZIP to verify, publish without overwrite.

    The reader must verify the source snapshot before/after reading. Checksums verify
    the bytes received, not equivalence to a device-side cryptographic checksum.
    A manifest preserves paths/dates/name mappings; no incomplete ZIP is published.
    """
    selected = tuple(items)
    files = [(item, file) for item in selected for file in item.files]
    if not files:
        raise ExportError("尚未選取任何可匯出的檔案。")
    if len({file.path for _, file in files}) != len(files):
        raise ExportError("選取清單包含重複來源檔案。")
    if any(file.size < 0 for _, file in files):
        raise ExportError("檔案容量無效。")
    destination = Path(destination).absolute()
    if os.path.lexists(destination):
        raise ExportError("輸出檔案已存在，請選擇其他名稱。")
    total = sum(file.size for _, file in files)
    # Stored ZIP plus filenames, ZIP64 headers and manifest. Reserve generously.
    overhead = sum(len(json.dumps(item.recording_metadata, ensure_ascii=False).encode("utf-8")) for item in selected) + 1024 * 1024 + sum(4096 + len(file.path.encode("utf-8")) * 8 for _, file in files)
    if shutil.disk_usage(destination.parent).free < total + overhead:
        raise ExportError("輸出位置空間不足，需包含 ZIP 與清單的額外空間。")
    cancellation = cancel or Event()

    def check_cancel():
        if cancellation.is_set():
            raise Cancelled("已取消，整批匯出失敗。")

    def notify(phase, done_bytes, done_files):
        if progress:
            progress(Progress(phase, done_bytes, total, done_files, len(files)))

    fd, name = tempfile.mkstemp(prefix=".iphonebackup-", suffix=".partial", dir=destination.parent)
    temporary = Path(name)
    published = False
    try:
        manifest = []
        used = {"export-manifest.json"}
        written = 0
        with os.fdopen(fd, "w+b") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for position, (item, file) in enumerate(files):
                    check_cancel()
                    base = safe_name(PurePosixPath(file.path).name)
                    folder = safe_name(item.month)
                    archive_name = f"{folder}/{base}"
                    number = 2
                    stem, extension = os.path.splitext(base)
                    while archive_name.casefold() in used:
                        archive_name = f"{folder}/{stem}__{number}{extension}"
                        number += 1
                    used.add(archive_name.casefold())
                    digest = hashlib.sha256()
                    count = 0
                    info = zipfile.ZipInfo(archive_name)
                    info.file_size = file.size
                    info.compress_type = zipfile.ZIP_STORED
                    with archive.open(info, "w", force_zip64=True) as output:
                        for chunk in read(file):
                            check_cancel()
                            if not isinstance(chunk, bytes):
                                raise ExportError("讀取器回傳無效資料。")
                            count += len(chunk)
                            if count > file.size:
                                raise ExportError(f"來源檔案容量已改變：{file.path}")
                            output.write(chunk)
                            digest.update(chunk)
                            written += len(chunk)
                            notify("寫入", written, position)
                    if count != file.size:
                        raise ExportError(f"來源檔案讀取不完整：{file.path}")
                    manifest.append({"item_id": item.id, "source": file.path, "archive": archive_name,
                                     "size": count, "sha256": digest.hexdigest(),
                                     "captured": item.captured.isoformat() if item.captured else None,
                                     "date_source": item.date_source, "usb_source": file.source})
                    notify("寫入", written, position + 1)
                manifest_document = {"version": 1, "files": manifest,
                    "items": [{"id": item.id, "title": item.title, "duration_seconds": item.duration_seconds,
                               "recording_metadata": item.recording_metadata} for item in selected],
                    "coverage": "selected readable files; full device library coverage unverified"}
                archive.writestr("export-manifest.json", json.dumps(manifest_document, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        verified = 0
        with zipfile.ZipFile(temporary) as archive:
            expected_names = [entry["archive"] for entry in manifest] + ["export-manifest.json"]
            if archive.namelist() != expected_names:
                raise ExportError("ZIP 清單核對失敗。")
            for position, entry in enumerate(manifest):
                check_cancel()
                digest = hashlib.sha256()
                with archive.open(entry["archive"]) as source:
                    while chunk := source.read(1024 * 1024):
                        check_cancel()
                        digest.update(chunk)
                        verified += len(chunk)
                        notify("驗證", verified, position)
                if archive.getinfo(entry["archive"]).file_size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise ExportError(f"ZIP 內容驗證失敗：{entry['archive']}")
                notify("驗證", verified, position + 1)
            if json.loads(archive.read("export-manifest.json")) != manifest_document:
                raise ExportError("匯出清單驗證失敗。")
        check_cancel()
        publish_no_replace(temporary, destination)
        published = True
        return destination
    except BaseException as exc:
        cleanup_errors = []
        if published:
            try:
                destination.unlink()
            except OSError as cleanup:
                cleanup_errors.append(f"{destination}: {cleanup}")
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup:
            cleanup_errors.append(f"{temporary}: {cleanup}")
        if cleanup_errors:
            raise ExportError("整批匯出失敗；無法清除本次檔案：" + "；".join(cleanup_errors)) from exc
        raise


def unfinished_exports(folder: Path) -> tuple[Path, ...]:
    """Identify crash leftovers. Never treat them as completed or auto-delete others."""
    return tuple(folder.glob(".iphonebackup-*.partial"))
