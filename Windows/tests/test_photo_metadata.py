from datetime import datetime
from contextlib import closing
from io import BytesIO
from pathlib import Path
from threading import Event
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, PngImagePlugin

from iphonebackup.models import DeviceFile
from iphonebackup.photo_metadata import inspect_photo, inspect_pillow, parse_date
from iphonebackup.photo_library import read_dates, library_dates
from iphonebackup.usb import AFCError


class MemorySession:
    def __init__(self, data):
        self.data = data

    def chunks(self, file):
        for position in range(0, len(self.data), 65536):
            yield self.data[position:position + 65536]


class PhotoTests(unittest.TestCase):
    def photo(self, data, name="/DCIM/100APPLE/test.JPG"):
        return inspect_photo(MemorySession(data), DeviceFile(name, len(data)), Event())

    def test_large_jpeg_preview_reads_beyond_256kb(self):
        image = Image.effect_noise((1600, 1200), 100).convert("RGB")
        output = BytesIO()
        image.save(output, "JPEG", quality=95)
        data = output.getvalue()
        self.assertGreater(len(data), 256 * 1024)
        date, _, thumbnail, error = self.photo(data)
        self.assertIsNone(date)
        self.assertIsNone(error)
        self.assertLessEqual(max(Image.open(BytesIO(thumbnail)).size), 320)

    def test_root_exif_date_is_not_missed(self):
        exif = Image.Exif()
        exif[36867] = "2024:09:23 12:34:56"
        output = BytesIO()
        Image.new("RGB", (100, 50), "green").save(output, "JPEG", exif=exif)
        date, source, thumbnail, error = self.photo(output.getvalue())
        self.assertEqual(date, datetime(2024, 9, 23, 12, 34, 56))
        self.assertIn("EXIF", source)
        self.assertTrue(thumbnail)

    def test_nested_exif_capture_date(self):
        exif = Image.Exif()
        exif[34665] = {36867: "2025:12:31 23:59:01"}
        output = BytesIO()
        Image.new("RGB", (100, 50)).save(output, "JPEG", exif=exif)
        self.assertEqual(self.photo(output.getvalue())[0], datetime(2025, 12, 31, 23, 59, 1))

    def test_png_preview_and_creation_xmp(self):
        info = PngImagePlugin.PngInfo()
        info.add_itxt("XML:com.adobe.xmp", '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:xmp="http://ns.adobe.com/xap/1.0/"><xmp:CreateDate>2026-08-01T00:02:00+08:00</xmp:CreateDate></x:xmpmeta>')
        output = BytesIO()
        Image.new("RGBA", (80, 120), (255, 0, 0, 120)).save(output, "PNG", pnginfo=info)
        date, source, thumbnail, error = self.photo(output.getvalue(), "/DCIM/100APPLE/test.PNG")
        self.assertEqual(date, datetime(2026, 8, 1, 0, 2))
        self.assertIn("XMP", source)
        self.assertTrue(thumbnail)
        self.assertIsNone(error)

    def test_exif_orientation_rotates_preview(self):
        exif = Image.Exif()
        exif[274] = 6
        output = BytesIO()
        Image.new("RGB", (100, 50), "red").save(output, "JPEG", exif=exif)
        thumbnail = self.photo(output.getvalue())[2]
        preview = Image.open(BytesIO(thumbnail))
        self.assertGreater(preview.height, preview.width)

    def test_missing_capture_date_does_not_use_modification_exif(self):
        exif = Image.Exif()
        exif[306] = "2026:01:01 00:00:00"
        output = BytesIO()
        Image.new("RGB", (100, 100)).save(output, "JPEG", exif=exif)
        self.assertIsNone(self.photo(output.getvalue())[0])

    def test_corrupt_image_retains_exportable_item_error(self):
        _, _, preview, error = self.photo(b"not-a-real-image")
        self.assertIsNone(preview)
        self.assertTrue(error)

    def test_short_transfer_is_not_reported_as_successful_preview(self):
        with self.assertRaisesRegex(RuntimeError, "不完整"):
            inspect_photo(MemorySession(b"short"), DeviceFile("/x.JPG", 100), Event())

    def test_cancel_during_transfer(self):
        cancel = Event()
        cancel.set()
        with self.assertRaisesRegex(RuntimeError, "取消"):
            inspect_photo(MemorySession(b"photo"), DeviceFile("/x.JPG", 5), cancel)

    def test_recorded_timezone_keeps_original_month(self):
        self.assertEqual(parse_date("2026-10-01T00:01:00+08:00"), datetime(2026, 10, 1, 0, 1))

    @unittest.skipUnless(sys.platform == "darwin", "macOS 系統 HEIC 解碼測試")
    def test_macos_heic_preview_without_pillow_plugin(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            source = Path(folder) / "source.png"
            target = Path(folder) / "image.heic"
            try:
                Image.new("RGB", (120, 80), "blue").save(source)
                completed = subprocess.run(["/usr/bin/sips", "-s", "format", "heic", str(source), "--out", str(target)], capture_output=True, text=True)
                if completed.returncode:
                    if "kern.hv_vmm_present" in completed.stderr:
                        self.skipTest("受限環境不允許系統建立 HEIC 測試圖；HEIC 仍需實機驗證")
                    self.fail("系統 HEIC 測試檔建立失敗：" + completed.stdout + completed.stderr)
                date, _, thumbnail, error = self.photo(target.read_bytes(), "/DCIM/100APPLE/test.HEIC")
                self.assertTrue(thumbnail)
                self.assertIsNone(error)
            finally:
                source.unlink(missing_ok=True)
                target.unlink(missing_ok=True)

    @unittest.skipUnless(sys.platform == "darwin", "macOS 原生 ImageIO")
    def test_native_imageio_metadata_and_preview_are_used(self):
        from iphonebackup.imageio_macos import inspect_image
        fd, name = tempfile.mkstemp(suffix=".jpg")
        import os
        os.close(fd)
        path = Path(name)
        try:
            exif = Image.Exif()
            exif[34665] = {36867: "2026:10:14 12:00:00"}
            Image.new("RGB", (800, 400), "green").save(path, exif=exif)
            metadata, preview = inspect_image(path)
            self.assertEqual(metadata["{Exif}"]["DateTimeOriginal"], "2026:10:14 12:00:00")
            self.assertTrue(preview)
            self.assertLessEqual(max(Image.open(BytesIO(preview)).size), 320)
        finally:
            path.unlink(missing_ok=True)


class LibraryDateTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.scratch.name) / "Photos.sqlite"

    def tearDown(self):
        for file in self.db.parent.iterdir():
            file.unlink()
        self.scratch.cleanup()

    def setup_database(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("CREATE TABLE ZASSET (Z_PK INTEGER, ZDIRECTORY TEXT, ZFILENAME TEXT, ZDATECREATED REAL)")
            connection.execute("CREATE TABLE ZADDITIONALASSETATTRIBUTES (ZASSET INTEGER, ZEXIFTIMESTAMPSTRING TEXT, ZTIMEZONEOFFSET INTEGER)")

    def test_phone_library_date_matches_exact_path_and_applies_recorded_offset(self):
        self.setup_database()
        utc = (datetime(2026, 9, 30, 18) - datetime(2001, 1, 1)).total_seconds()
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO ZASSET VALUES (1, 'DCIM/100APPLE', 'IMG.PNG', ?)", (utc,))
            connection.execute("INSERT INTO ZADDITIONALASSETATTRIBUTES VALUES (1, NULL, 28800)")
        result = read_dates(self.db, Event())
        self.assertEqual(result["/DCIM/100APPLE/IMG.PNG"][0], datetime(2026, 10, 1, 2))
        self.assertNotIn("/DCIM/101APPLE/IMG.PNG", result)

    def test_duplicate_ambiguous_path_is_not_guessed(self):
        self.setup_database()
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.executemany("INSERT INTO ZASSET VALUES (?, 'DCIM/100APPLE', 'IMG.PNG', ?)", [(1, 1000), (2, 2000)])
        self.assertEqual(read_dates(self.db, Event()), {})

    def test_unsupported_schema_does_not_invent_dates(self):
        sqlite3.connect(self.db).close()
        with self.assertRaises(ValueError):
            read_dates(self.db, Event())

    def test_inaccessible_phone_library_is_explicit(self):
        class NoAccess:
            def stat(self, path):
                raise AFCError("讀取相片資料庫", 10)
        result, warning = library_dates(NoAccess(), Event())
        self.assertEqual(result, {})
        self.assertIn("無法取得", warning)

    def test_unreadable_wal_does_not_use_stale_main_database(self):
        class DeniedWal:
            def stat(self, path):
                if path.endswith("-wal"):
                    raise AFCError("日誌存取遭拒", 10)
                return {"st_size": "100", "st_mtime": "1"}
        result, warning = library_dates(DeniedWal(), Event())
        self.assertEqual(result, {})
        self.assertIn("過期", warning)
