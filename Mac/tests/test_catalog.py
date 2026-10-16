import unittest
from datetime import datetime
from threading import Event
from unittest.mock import patch

from iphonebackup.catalog import scan
from iphonebackup.models import Catalog, DeviceFile
from iphonebackup.usb import AFCError, DeviceError


class FakeSession:
    """Test fixture only; not selectable from the app."""
    files = ()
    error = None

    def __init__(self, *args):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def walk(self, root, cancel):
        if self.error:
            raise self.error
        yield from self.files

    def listdir(self, path):
        if self.error:
            raise self.error
        return ["test"] if self.files else []

    def stat(self, path):
        raise AFCError("測試環境不提供相片資料庫", 8)

    def chunks(self, file):
        yield b"x" * file.size


class CatalogTests(unittest.TestCase):
    def setUp(self):
        FakeSession.files = ()
        FakeSession.error = None
        fallback = patch("iphonebackup.catalog.scan_voice_apps", side_effect=lambda udid, cancel, progress, attempts:
            Catalog((), ("USB 無法取得語音備忘錄，不能判定手機沒有錄音。", *attempts), availability="unavailable"))
        self.app_fallback = fallback.start()
        self.addCleanup(fallback.stop)

    def test_unavailable_voice_root_is_not_empty_library_success(self):
        FakeSession.error = AFCError("讀取錄音", 8)
        with patch("iphonebackup.catalog.Session", FakeSession):
            result = scan("test-udid", "voice", Event())
        self.assertFalse(result.complete)
        self.assertEqual(result.items, ())
        self.assertIn("不能判定手機沒有錄音", result.warnings[0])
        self.assertEqual(result.availability, "unavailable")
        self.app_fallback.assert_called_once()

    def test_disconnect_does_not_return_partial_catalog(self):
        FakeSession.error = DeviceError("USB disconnected")
        with patch("iphonebackup.catalog.Session", FakeSession):
            with self.assertRaises(DeviceError):
                scan("test-udid", "photos", Event())

    def test_empty_legacy_voice_directory_is_unknown_not_zero_recordings(self):
        with patch("iphonebackup.catalog.Session", FakeSession):
            result = scan("test-udid", "voice", Event())
        self.assertEqual(result.availability, "unavailable")
        self.assertTrue(any("無法確認" in text for text in result.warnings))

    def test_permission_denied_voice_root_is_distinguished(self):
        FakeSession.error = AFCError("讀取錄音", 10)
        with patch("iphonebackup.catalog.Session", FakeSession):
            result = scan("test-udid", "voice", Event())
        self.assertEqual(result.availability, "unavailable")
        self.assertTrue(any("拒絕" in warning for warning in result.warnings))

    def test_error_after_voice_root_open_is_not_disguised_as_empty(self):
        class FailingFile(FakeSession):
            def listdir(self, path):
                return ["recording.m4a"]
            def walk(self, root, cancel):
                yield DeviceFile("/Recordings/first.m4a", 10)
                raise AFCError("讀取途中檔案消失", 8)
        with patch("iphonebackup.catalog.Session", FailingFile):
            with self.assertRaises(AFCError):
                scan("test-udid", "voice", Event())

    def test_live_photo_pairing_is_not_guessed_from_names(self):
        FakeSession.files = (DeviceFile("/DCIM/100APPLE/IMG_001.HEIC", 100), DeviceFile("/DCIM/100APPLE/IMG_001.MOV", 200))
        with patch("iphonebackup.catalog.Session", FakeSession):
            result = scan("test-udid", "photos", Event())
        self.assertEqual(len(result.items), 2)
        self.assertFalse(result.complete)
        self.assertTrue(all(item.captured is None for item in result.items))

    def test_recognized_sidecar_is_preserved_and_date_is_not_fabricated(self):
        FakeSession.files = (DeviceFile("/DCIM/100APPLE/IMG.HEIC", 100, "123456789"), DeviceFile("/DCIM/100APPLE/IMG.AAE", 20))
        with patch("iphonebackup.catalog.Session", FakeSession):
            result = scan("test-udid", "photos", Event())
        self.assertEqual(result.items[0].size, 120)
        self.assertEqual(result.items[0].month, "日期未知")

    def test_library_capture_date_fallback_is_used_for_photos_without_exif(self):
        FakeSession.files = (DeviceFile("/DCIM/100APPLE/IMG.PNG", 100),)
        date = datetime(2024, 9, 1, 12)
        with patch("iphonebackup.catalog.Session", FakeSession), \
             patch("iphonebackup.catalog.library_dates", return_value=({"/DCIM/100APPLE/IMG.PNG": (date, "手機相片資料庫")}, None)), \
             patch("iphonebackup.catalog.inspect_photo", return_value=(None, None, b"preview", None)):
            result = scan("test-udid", "photos", Event())
        self.assertEqual(result.items[0].month, "2024-09")
        self.assertEqual(result.items[0].date_source, "手機相片資料庫")
        self.assertEqual(result.items[0].thumbnail, b"preview")
