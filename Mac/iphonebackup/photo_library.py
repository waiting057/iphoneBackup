"""Optional read-only capture dates from the phone's Photos database.

Schema varies by iOS; unsupported/inaccessible databases never become fabricated
dates. Copy a stable DB/WAL pair into private scratch storage, query locally.
"""
from datetime import datetime, timedelta
from contextlib import closing
from pathlib import Path, PurePosixPath
import math
import sqlite3
import tempfile
from threading import Event

from .models import DeviceFile
from .photo_metadata import parse_date
from .usb import AFCError

DATABASE = "/PhotoData/Photos.sqlite"


def _canonical(directory, filename):
    if not isinstance(directory, str) or not isinstance(filename, str):
        return None
    directory = directory.strip("/")
    path = PurePosixPath(directory) / filename
    if ".." in path.parts or "/" in filename or "\\" in filename:
        return None
    if path.parts and path.parts[0] == "DCIM":
        return "/" + str(path)
    if len(path.parts) == 2:
        return "/DCIM/" + str(path)
    return None


def read_dates(database: Path, cancel: Event):
    result, ambiguous = {}, set()
    with closing(sqlite3.connect(database.absolute().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        asset = next((name for name in ("ZASSET", "ZGENERICASSET") if name in tables), None)
        if asset is None:
            raise ValueError("相片資料庫結構尚未支援。")
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{asset}")')}
        if not {"ZDIRECTORY", "ZFILENAME", "ZDATECREATED"} <= columns:
            raise ValueError("相片資料庫缺少可識別的拍攝日期欄位。")
        join, exif, offset = "", "NULL", "NULL"
        if "ZADDITIONALASSETATTRIBUTES" in tables and "Z_PK" in columns:
            attributes = {row[1] for row in connection.execute('PRAGMA table_info("ZADDITIONALASSETATTRIBUTES")')}
            if "ZASSET" in attributes:
                join = ' LEFT JOIN ZADDITIONALASSETATTRIBUTES b ON b.ZASSET=a.Z_PK'
                exif = "b.ZEXIFTIMESTAMPSTRING" if "ZEXIFTIMESTAMPSTRING" in attributes else "NULL"
                offset = "b.ZTIMEZONEOFFSET" if "ZTIMEZONEOFFSET" in attributes else "NULL"
        sql = f'SELECT a.ZDIRECTORY,a.ZFILENAME,a.ZDATECREATED,{exif},{offset} FROM "{asset}" a{join}'
        for directory, filename, timestamp, exif_timestamp, timezone_offset in connection.execute(sql):
            if cancel.is_set():
                raise RuntimeError("已取消讀取相片日期。")
            path = _canonical(directory, filename)
            if path is None:
                continue
            captured = parse_date(exif_timestamp)
            source = "手機相片資料庫 EXIF 拍攝日期"
            if captured is None and isinstance(timestamp, (int, float)) and math.isfinite(timestamp):
                try:
                    captured = datetime(2001, 1, 1) + timedelta(seconds=timestamp)
                    source = "手機相片資料庫建立日期（UTC，未提供拍攝時區）"
                    if isinstance(timezone_offset, (int, float)) and math.isfinite(timezone_offset) and abs(timezone_offset) <= 14 * 3600:
                        captured += timedelta(seconds=timezone_offset)
                        source = "手機相片資料庫建立日期（保留拍攝時區）"
                except (ValueError, OverflowError):
                    captured = None
            if captured is None:
                continue
            value = (captured, source)
            if path in result and result[path][0] != captured:
                ambiguous.add(path)
            result[path] = value
    for path in ambiguous:
        result.pop(path, None)
    return result


def library_dates(session, cancel: Event):
    def snapshot(path):
        try:
            info = session.stat(path)
            return DeviceFile(path, int(info["st_size"]), info.get("st_mtime"))
        except AFCError as error:
            if error.code == 8:
                return None
            if error.code in (10, 15):
                if path == DATABASE:
                    return None
                raise ValueError("無法確認相片資料庫日誌的存取狀態。") from error
            raise

    main = snapshot(DATABASE)
    if main is None:
        return {}, "目前 USB 權限無法取得相片資料庫；沒有內嵌拍攝日期的照片仍可能顯示「日期未知」。"
    try:
        wal = snapshot(DATABASE + "-wal")
    except ValueError as error:
        return {}, str(error) + "本次不使用可能過期的資料庫日期。"
    with tempfile.TemporaryDirectory(prefix="iphonebackup-dates-", ignore_cleanup_errors=True) as folder:
        local = Path(folder) / "Photos.sqlite"
        try:
            for source in (main, wal):
                if source is None:
                    continue
                with (Path(folder) / PurePosixPath(source.path).name).open("wb") as output:
                    size = 0
                    for chunk in session.chunks(source):
                        if cancel.is_set():
                            raise RuntimeError("已取消讀取相片日期。")
                        output.write(chunk)
                        size += len(chunk)
                    if size != source.size:
                        raise ValueError("相片資料庫讀取不完整。")
            if snapshot(DATABASE) != main or snapshot(DATABASE + "-wal") != wal:
                return {}, "相片資料庫正在更新，本次未套用資料庫日期；可稍後重新讀取。"
            return read_dates(local, cancel), None
        except (sqlite3.Error, ValueError, OSError) as error:
            return {}, f"本次無法使用相片資料庫日期（{error}），僅使用可確認的內嵌日期。"
        finally:
            # Only local scratch copies are touched; no write APIs on phone.
            for file in Path(folder).iterdir():
                file.unlink(missing_ok=True)
