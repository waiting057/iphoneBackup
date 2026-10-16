from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from packaging_support import macos_libraries, prepare_native


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.scratch.name).resolve()

    def tearDown(self):
        # File cleanup is explicit; managed hosts may prohibit removing empty
        # scratch directories, which is independent of the packaging checks.
        for path in self.root.rglob("*"):
            if path.is_file():
                path.unlink()
        self.scratch.cleanup()

    def test_missing_windows_native_libraries_fail_before_packaging(self):
        supplied = self.root / "input"
        supplied.mkdir()
        with self.assertRaisesRegex(ValueError, "缺少 libimobiledevice"):
            prepare_native("win32", supplied, self.root / "stage")

    def test_supplied_native_requires_licenses(self):
        supplied = self.root / "input"
        supplied.mkdir()
        (supplied / "libimobiledevice-1.0.dll").write_bytes(b"test fixture, not executable")
        with self.assertRaisesRegex(ValueError, "授權"):
            prepare_native("win32", supplied, self.root / "stage")

    def test_complete_supplied_native_is_copied_with_hash_and_license(self):
        supplied = self.root / "input"
        (supplied / "licenses").mkdir(parents=True)
        (supplied / "licenses" / "COPYING").write_text("Test fixture license")
        name = "libimobiledevice-1.0.dll"
        (supplied / name).write_bytes(b"test fixture, not executable")
        manifest = prepare_native("win32", supplied, self.root / "stage")
        self.assertEqual(manifest[0]["name"], name)
        self.assertEqual(len(manifest[0]["sha256"]), 64)
        self.assertTrue((self.root / "stage" / "licenses" / "COPYING").exists())

    def test_macos_follows_dependencies_and_excludes_system_frameworks(self):
        primary = self.root / "libimobiledevice-1.0.dylib"
        dependency = self.root / "libplist.dylib"
        primary.touch()
        dependency.touch()
        listings = {str(primary): f"{primary}:\n\t{primary} (compatibility version 1)\n\t{dependency} (compatibility version 1)\n\t/usr/lib/libSystem.B.dylib (compatibility version 1)",
                    str(dependency): f"{dependency}:\n\t{dependency} (compatibility version 1)"}
        def inspect(command, **kwargs):
            return SimpleNamespace(stdout=listings[command[-1]])
        with patch("packaging_support.subprocess.run", side_effect=inspect):
            found = macos_libraries(primary)
        self.assertEqual(set(found), {primary.name, dependency.name})
