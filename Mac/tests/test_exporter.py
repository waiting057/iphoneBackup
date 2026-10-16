import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from threading import Event
from unittest.mock import patch

from iphonebackup.exporter import Cancelled, ExportError, export_zip, safe_name, unfinished_exports
from iphonebackup.models import DeviceFile, MediaItem


class ExportTests(unittest.TestCase):
    def setUp(self):
        # Keep test scratch data inside the project, including in restricted
        # environments whose OS temporary directory is not fully writable.
        self.temp = tempfile.TemporaryDirectory(prefix="iphonebackup-test-", ignore_cleanup_errors=True)
        self.folder = Path(self.temp.name)
        self.destination = self.folder / "result.zip"
        self.data = {"/DCIM/A/IMG.JPG": b"photo-original", "/DCIM/A/IMG.MOV": b"live-video", "/DCIM/B/IMG.JPG": b"other-photo"}

    def tearDown(self):
        # Explicitly check that test files can be removed. Some managed hosts
        # prohibit rmdir() of empty temporary directories; that restriction must
        # not mask file-cleanup failures in the exporter assertions above.
        for file in self.folder.iterdir():
            file.unlink()
        self.temp.cleanup()

    def item(self, *paths):
        return MediaItem(paths[0], paths[0], "photos", tuple(DeviceFile(path, len(self.data[path])) for path in paths))

    def read(self, file):
        yield self.data[file.path]

    def assert_no_partial(self):
        self.assertFalse(self.destination.exists())
        self.assertEqual(unfinished_exports(self.folder), ())

    def test_selection_only_and_all_components(self):
        export_zip([self.item("/DCIM/A/IMG.JPG", "/DCIM/A/IMG.MOV")], self.destination, self.read)
        with zipfile.ZipFile(self.destination) as archive:
            self.assertEqual(archive.read("日期未知/IMG.JPG"), self.data["/DCIM/A/IMG.JPG"])
            self.assertEqual(archive.read("日期未知/IMG.MOV"), self.data["/DCIM/A/IMG.MOV"])
            self.assertEqual(len(archive.namelist()), 3)
            manifest = json.loads(archive.read("export-manifest.json"))
            self.assertEqual(len(manifest["files"]), 2)
            self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))

    def test_same_filename_keeps_both(self):
        export_zip([self.item("/DCIM/A/IMG.JPG"), self.item("/DCIM/B/IMG.JPG")], self.destination, self.read)
        with zipfile.ZipFile(self.destination) as archive:
            self.assertEqual(archive.read("日期未知/IMG__2.JPG"), self.data["/DCIM/B/IMG.JPG"])

    def test_required_companion_read_failure_fails_whole_batch(self):
        def fail(file):
            if file.path.endswith("MOV"):
                raise OSError("USB disconnected")
            yield self.data[file.path]
        with self.assertRaises(OSError):
            export_zip([self.item("/DCIM/A/IMG.JPG", "/DCIM/A/IMG.MOV")], self.destination, fail)
        self.assert_no_partial()

    def test_short_read_fails(self):
        with self.assertRaises(ExportError):
            export_zip([self.item("/DCIM/A/IMG.JPG")], self.destination, lambda _: [b"short"])
        self.assert_no_partial()

    def test_long_read_fails(self):
        with self.assertRaises(ExportError):
            export_zip([self.item("/DCIM/A/IMG.JPG")], self.destination, lambda _: [b"x" * 100])
        self.assert_no_partial()

    def test_cancel_during_read(self):
        cancel = Event()
        def read(file):
            yield self.data[file.path][:2]
            cancel.set()
            yield self.data[file.path][2:]
        with self.assertRaises(Cancelled):
            export_zip([self.item("/DCIM/A/IMG.JPG")], self.destination, read, cancel)
        self.assert_no_partial()

    def test_cancel_during_validation(self):
        cancel = Event()
        def progress(value):
            if value.phase == "驗證":
                cancel.set()
        with self.assertRaises(Cancelled):
            export_zip([self.item("/DCIM/A/IMG.JPG")], self.destination, self.read, cancel, progress)
        self.assert_no_partial()

    def test_existing_output_is_preserved(self):
        self.destination.write_bytes(b"previous successful export")
        with self.assertRaises(ExportError):
            export_zip([self.item("/DCIM/A/IMG.JPG")], self.destination, self.read)
        self.assertEqual(self.destination.read_bytes(), b"previous successful export")

    def test_concurrent_output_is_not_overwritten(self):
        def progress(value):
            if value.phase == "驗證":
                self.destination.write_bytes(b"other job")
        with self.assertRaises(FileExistsError):
            export_zip([self.item("/DCIM/A/IMG.JPG")], self.destination, self.read, progress=progress)
        self.assertEqual(self.destination.read_bytes(), b"other job")
        self.assertEqual(unfinished_exports(self.folder), ())

    def test_insufficient_space(self):
        with patch("iphonebackup.exporter.shutil.disk_usage", return_value=type("Space", (), {"free": 1})()):
            with self.assertRaises(ExportError):
                export_zip([self.item("/DCIM/A/IMG.JPG")], self.destination, self.read)
        self.assert_no_partial()

    def test_write_error_removes_partial(self):
        with patch.object(zipfile._ZipWriteFile, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                export_zip([self.item("/DCIM/A/IMG.JPG")], self.destination, self.read)
        self.assert_no_partial()

    def test_archive_corruption_fails_verification(self):
        original_open = zipfile.ZipFile.__init__
        def corrupt_on_verify(archive, file, mode="r", *args, **kwargs):
            if mode == "r":
                path = Path(file)
                raw = path.read_bytes()
                raw = raw.replace(b"photo-original", b"photo-corrupt!", 1)
                path.write_bytes(raw)
            return original_open(archive, file, mode, *args, **kwargs)
        with patch.object(zipfile.ZipFile, "__init__", corrupt_on_verify):
            with self.assertRaises(zipfile.BadZipFile):
                export_zip([self.item("/DCIM/A/IMG.JPG")], self.destination, self.read)
        self.assert_no_partial()

    def test_no_selection_no_output(self):
        with self.assertRaises(ExportError):
            export_zip([], self.destination, self.read)
        self.assert_no_partial()

    def test_duplicate_source_is_rejected(self):
        item = self.item("/DCIM/A/IMG.JPG")
        with self.assertRaises(ExportError):
            export_zip([item, item], self.destination, self.read)
        self.assert_no_partial()

    def test_crash_leftovers_are_identified_and_preserved(self):
        partial = self.folder / ".iphonebackup-crashed.partial"
        partial.write_bytes(b"unfinished")
        self.assertEqual(unfinished_exports(self.folder), (partial,))
        self.assertTrue(partial.exists())

    def test_safe_names_cannot_escape_or_use_windows_reserved_names(self):
        for name in ("../../file", "CON.txt", "NUL", "a\\b", "a:b", ".."):
            result = safe_name(name)
            self.assertNotIn("/", result)
            self.assertNotIn("\\", result)
            self.assertNotIn(":", result)
            self.assertNotIn(result.upper().split(".")[0], {"CON", "NUL"})

    def test_zip64_file_count(self):
        # Cross the traditional 65535-entry limit with bounded zero-byte files.
        items = [MediaItem(str(i), str(i), "voice", (DeviceFile(f"/Recordings/{i}.m4a", 0),)) for i in range(65536)]
        export_zip(items, self.destination, lambda _: [])
        with zipfile.ZipFile(self.destination) as archive:
            self.assertEqual(len(archive.infolist()), 65537)
        self.assertIn(b"PK\x06\x06", self.destination.read_bytes())


if __name__ == "__main__":
    unittest.main()
