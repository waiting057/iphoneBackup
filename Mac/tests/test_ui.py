import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from iphonebackup.ui import MediaModel
except ModuleNotFoundError:
    QApplication = None

from iphonebackup.models import DeviceFile, MediaItem


@unittest.skipIf(QApplication is None, "PySide6 尚未安裝；介面測試未執行")
class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_large_selection_and_deselect_one(self):
        model = MediaModel()
        items = tuple(MediaItem(str(i), str(i), "photos", (DeviceFile(f"/{i}.heic", 1024),)) for i in range(10000))
        model.replace(items)
        model.select_all()
        self.assertEqual(len(model.selected_items()), 10000)
        model.setData(model.index(1), Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        self.assertEqual(len(model.selected_items()), 9999)

    def test_page_replacement_clears_stale_selection(self):
        model = MediaModel()
        model.replace([MediaItem("1", "photo", "photos", (DeviceFile("/1.heic", 10),))])
        model.select_all()
        model.replace([])
        self.assertEqual(model.selected_items(), ())
        self.assertEqual(model.rowCount(), 0)


@unittest.skipIf(QApplication is None, 'PySide6 尚未安裝')
class BackupLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from unittest.mock import patch
        from iphonebackup.ui import Window
        self.patches = [patch.object(Window, name) for name in ('poll_devices', 'check_previous_exports', 'check_previous_backups')]
        for item in self.patches:
            item.start()
        self.window = Window()
        self.window.timer.stop()

    def tearDown(self):
        self.window.backup_folder = None
        self.window.backup_reader = None
        self.window.close()
        for item in self.patches:
            item.stop()

    def test_invalidation_does_not_delete_backup_during_active_worker(self):
        from unittest.mock import patch
        self.window.backup_folder = '/synthetic-fixture'
        self.window.set_busy(True)
        with patch.object(self.window, 'release_backup') as release:
            self.window.invalidate()
            release.assert_not_called()
        self.window.set_busy(False)

    def test_both_export_success_and_failure_clear_source_catalog(self):
        from unittest.mock import patch
        from pathlib import Path
        from iphonebackup.ui import QMessageBox
        for error in (None, RuntimeError('test failed export')):
            self.window.backup_folder = Path('/synthetic-fixture')
            self.window.backup_reader = object()
            self.window.model.replace([MediaItem('voice', 'voice', 'voice', (DeviceFile('/voice.m4a', 10, source='backup'),))])
            self.window.set_busy(True)
            jobs = []
            with patch.object(self.window, 'launch', side_effect=lambda job, ready: jobs.append(job)), patch.object(QMessageBox, 'warning'), patch.object(QMessageBox, 'information'):
                self.window.export_ready(Path('/synthetic-output.zip'), error)
            self.assertEqual(len(jobs), 1)
            self.assertIsNone(self.window.backup_folder)
            self.assertIsNone(self.window.backup_reader)
            self.assertEqual(self.window.model.items, ())

    def test_existing_backup_remains_usable_without_usb(self):
        from types import SimpleNamespace
        from pathlib import Path
        self.window.backup_reader = SimpleNamespace(root=Path('/synthetic-read-only-backup'))
        self.window.device_ids = ('old-device',)
        self.window.model.replace([MediaItem('voice', 'voice', 'voice', (DeviceFile('/voice.m4a', 10, source='backup'),))])
        self.window.model.select_all()
        self.window.devices_ready((), None)
        self.assertEqual(len(self.window.model.items), 1)
        self.assertEqual(self.window.connection_badge.text(), '本機備份')
        self.assertTrue(self.window.export_button.isEnabled())
