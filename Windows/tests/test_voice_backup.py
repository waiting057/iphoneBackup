"""Synthetic backups only: these tests never contact a phone."""
import json
from contextlib import closing
import plistlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from iphonebackup import voice_backup as vb
from iphonebackup.exporter import Cancelled, ExportError, export_zip


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.parent = Path(self.temp.name)
        self.job = vb.create_job(self.parent)
        self.udid = '12345678-1234567890123456'
        self.root = self.job / self.udid
        self.root.mkdir()
        self.db = sqlite3.connect(self.root / 'Manifest.db')
        self.db.execute('CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER)')
        self.counter = 0

    def tearDown(self):
        self.db.close()
        lease = vb._LEASES.pop(str(self.job), None)
        if lease:
            lease.close()
        self.temp.cleanup()

    def add(self, relative, content=b'recording', domain='AppDomainGroup-group.com.apple.VoiceMemos.shared'):
        self.counter += 1
        key = f'{self.counter:040x}'
        physical = self.root / key[:2] / key
        physical.parent.mkdir(exist_ok=True)
        physical.write_bytes(content)
        self.db.execute('INSERT INTO Files VALUES (?,?,?,1)', (key, domain, relative))
        self.db.commit()
        return physical

    def reader(self):
        reader = vb.BackupReader(self.root)
        return reader, reader.catalog(Event(), lambda value: None)

    def test_only_voice_domains_export_original_bytes_and_auxiliary(self):
        self.add('Recordings/one.m4a')
        self.add('Recordings/one.composition', b'edits')
        self.add('DCIM/private.jpg', b'not selected', 'CameraRollDomain')
        reader, catalog = self.reader()
        self.assertEqual(len(catalog.items), 1)
        self.assertEqual(len(catalog.items[0].files), 2)
        self.assertFalse(catalog.complete)
        output = self.parent / 'selected.zip'
        export_zip(catalog.items, output, reader.chunks)
        import zipfile
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read('日期未知/one.m4a'), b'recording')
            self.assertFalse(any('private' in name for name in archive.namelist()))

    def test_known_recording_date_and_title(self):
        self.add('Recordings/one.m4a')
        metadata = self.parent / 'fixture.db'
        with closing(sqlite3.connect(metadata)) as connection, connection:
            connection.execute('CREATE TABLE ZCLOUDRECORDING(ZPATH TEXT, ZDATE REAL, ZCUSTOMLABEL TEXT)')
            connection.execute("INSERT INTO ZCLOUDRECORDING VALUES ('/private/Recordings/one.m4a', 800000000, '測試錄音')")
        self.add('Recordings/CloudRecordings.db', metadata.read_bytes())
        reader, catalog = self.reader()
        audio = next(item for item in catalog.items if item.title == '測試錄音')
        self.assertEqual(audio.captured.year, 2026)
        self.assertNotEqual(audio.month, '日期未知')

    def memo_db(self, rows):
        metadata = self.parent / 'recordings-fixture.db'
        with closing(sqlite3.connect(metadata)) as connection, connection:
            connection.execute('CREATE TABLE ZCLOUDRECORDING(ZPATH TEXT, ZDATE REAL, ZCUSTOMLABEL TEXT, ZCUSTOMLABELFORSORTING TEXT, ZENCRYPTEDTITLE TEXT, ZDURATION REAL, ZEVICTIONDATE REAL)')
            connection.executemany('INSERT INTO ZCLOUDRECORDING VALUES (?,?,?,?,?,?,?)', rows)
        self.add('Recordings/CloudRecordings.db', metadata.read_bytes())

    def test_one_live_recording_excludes_deleted_and_groups_assets(self):
        self.add('Recordings/current.m4a')
        self.add('Recordings/removed.m4a', b'deleted audio')
        self.add('Recordings/current-track0.waveform', b'waveform')
        self.add('Recordings/removed-track0.waveform', b'deleted waveform')
        self.add('Recordings/current.composition/manifest.plist', plistlib.dumps({'RCComposedAVURL':'file:///private/Recordings/current.m4a'}))
        self.add('Library/Preferences/preferences.plist', b'not a recording')
        self.memo_db([('current.m4a',800000000,'2026-05-08T00:00:00Z','測試名稱','測試名稱',1.108,None),
                      ('removed.m4a',799000000,'old','old','old',1.9,799000010)])
        reader, catalog = self.reader()
        self.assertEqual(len(catalog.items), 1)
        item = catalog.items[0]
        self.assertEqual(item.title, '測試名稱')
        self.assertEqual(item.duration_seconds, 1.108)
        self.assertEqual(len(item.files), 3)
        output = self.parent / 'one-recording.zip'
        export_zip(catalog.items, output, reader.chunks)
        import zipfile
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertFalse(any('removed' in name or name.endswith('.db') or 'preferences' in name for name in names))
            document = json.loads(archive.read('export-manifest.json'))
            self.assertEqual(document['items'][0]['title'], '測試名稱')
            self.assertEqual(document['items'][0]['duration_seconds'], 1.108)
            self.assertEqual(document['items'][0]['recording_metadata']['ZPATH'], 'current.m4a')

    def test_unindexed_audio_is_not_guessed_as_active_recording(self):
        self.add('Recordings/current.m4a')
        self.add('Recordings/leftover.m4a')
        self.memo_db([('current.m4a',800000000,'name',None,None,1,None)])
        _, catalog = self.reader()
        self.assertEqual(len(catalog.items), 1)

    def test_missing_active_audio_fails_instead_of_partial_success(self):
        self.add('Recordings/other.m4a')
        self.memo_db([('current.m4a',800000000,'name',None,None,1,None)])
        with self.assertRaisesRegex(ExportError, '找不到其唯一對應'):
            self.reader()

    def test_composition_requires_referenced_fragment(self):
        self.add('Recordings/current.m4a')
        self.add('Recordings/current.composition/manifest.plist', plistlib.dumps({'RCComposedAVURL':'file:///private/Recordings/missing.caf'}))
        with self.assertRaisesRegex(ExportError, '缺失'):
            self.reader()

    def test_explicit_composition_fragment_preserved(self):
        self.add('Recordings/current.m4a')
        self.add('Recordings/random.caf', b'original fragment')
        self.add('Recordings/current.composition/manifest.plist', plistlib.dumps({'RCComposedAVURL':'file:///private/Recordings/current.m4a', 'RCDecomposedFragments':['file:///private/Recordings/random.caf']}))
        self.memo_db([('current.m4a',800000000,'name',None,None,1,None)])
        _, catalog = self.reader()
        self.assertEqual(len(catalog.items), 1)
        self.assertEqual(len(catalog.items[0].files), 3)

    def test_open_existing_validates_completion_without_backup_or_cleanup(self):
        self.add('Recordings/current.m4a')
        (self.root / 'Status.plist').write_bytes(plistlib.dumps({'SnapshotState':'finished'}))
        (self.root / 'Manifest.plist').write_bytes(plistlib.dumps({'IsEncrypted':False}))
        before = set(self.root.rglob('*'))
        with patch.object(vb.subprocess, 'Popen') as process, patch.object(vb, 'cleanup') as cleanup:
            reader = vb.open_existing(self.job)
            catalog = reader.catalog(Event(), lambda _:None)
            self.assertEqual(len(catalog.items), 1)
            process.assert_not_called()
            cleanup.assert_not_called()
        self.assertEqual(before, set(self.root.rglob('*')))

    def test_missing_audio_and_empty_backup_are_not_empty_phone(self):
        with self.assertRaisesRegex(ExportError, '不能判定'):
            self.reader()
        physical = self.add('Recordings/one.m4a')
        physical.unlink()
        with self.assertRaisesRegex(ExportError, '缺少必要'):
            self.reader()

    def test_changed_source_fails_whole_export(self):
        physical = self.add('one.m4a')
        reader, catalog = self.reader()
        physical.write_bytes(b'changed')
        output = self.parent / 'failed.zip'
        with self.assertRaises(ExportError):
            export_zip(catalog.items, output, reader.chunks)
        self.assertFalse(output.exists())
        self.assertEqual(tuple(self.parent.glob('*.partial')), ())

    def test_cleanup_rejects_external_or_tampered_markers(self):
        with self.assertRaises(ExportError):
            vb.cleanup(self.root)
        (self.job / vb.MARKER).write_text('{}')
        with self.assertRaises(ExportError):
            vb.cleanup(self.job)
        self.assertTrue(self.root.exists())

    def test_discover_only_marked_jobs(self):
        (self.parent / (vb.PREFIX + 'unowned')).mkdir()
        self.assertEqual(vb.leftovers([self.parent]), ())
        vb._LEASES.pop(str(self.job)).close()
        self.assertEqual(vb.leftovers([self.parent]), (self.job,))

    def test_failed_cleanup_retains_recovery_marker(self):
        with patch.object(vb.shutil, 'rmtree', side_effect=PermissionError('test locked directory')):
            with self.assertRaisesRegex(ExportError, '清除未完成'):
                vb.cleanup(self.job)
        self.assertTrue((self.job / vb.MARKER).is_file())

    def test_encrypted_backup_fails_before_writer_starts(self):
        with patch.object(vb, 'tool', return_value='ideviceinfo'), patch.object(vb.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='true\n')), patch.object(vb.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(ExportError, '未開始備份'):
                vb.create_backup(self.job, self.udid, 10*vb.GIB, 2*vb.GIB, Event(), lambda value: None)
            popen.assert_not_called()

    def test_cancel_and_capacity_stop_writer_before_return(self):
        for cancel, size, expected in ((True, 0, Cancelled), (False, 20*vb.GIB, ExportError)):
            event = Event()
            if cancel:
                event.set()
            process = SimpleNamespace(poll=lambda: None, terminate=lambda: None, kill=lambda: None, wait=lambda **kwargs: None)
            with patch.object(vb, 'check_encryption'), patch.object(vb, 'tool', return_value='idevicebackup2'), patch.object(vb.subprocess, 'Popen', return_value=process), patch.object(vb, 'usage', return_value=size), patch.object(vb, 'stop_process') as stop:
                with self.assertRaises(expected):
                    vb.create_backup(self.job, self.udid, 10*vb.GIB, 2*vb.GIB, event, lambda value: None)
                stop.assert_called_once_with(process)

    def test_finished_status_required(self):
        for name, content in [('Status.plist', {'SnapshotState': 'new'}), ('Manifest.plist', {'IsEncrypted': False})]:
            (self.root / name).write_bytes(plistlib.dumps(content))
        process = SimpleNamespace(poll=lambda: 0)
        with patch.object(vb, 'check_encryption'), patch.object(vb, 'tool', return_value='idevicebackup2'), patch.object(vb.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(ExportError, '尚未完成'):
                vb.create_backup(self.job, self.udid, 10*vb.GIB, 2*vb.GIB, Event(), lambda value: None)

    def test_live_snapshot_movement_does_not_abort_completed_backup(self):
        from contextlib import nullcontext
        from unittest.mock import Mock
        import stat
        for name, data in [('Status.plist', {'SnapshotState':'finished'}), ('Manifest.plist', {'IsEncrypted':False})]:
            (self.root / name).write_bytes(plistlib.dumps(data))
        transient = SimpleNamespace(path=str(self.root / 'Snapshot' / 'a5' / 'moving'),
            stat=Mock(side_effect=FileNotFoundError(2, 'moved while backing up')))
        stable = SimpleNamespace(path=str(self.root / 'Manifest.db'),
            stat=Mock(return_value=SimpleNamespace(st_size=1024, st_mode=stat.S_IFREG)))
        process = SimpleNamespace(poll=lambda:0, terminate=Mock(), kill=Mock())
        with patch.object(vb, 'check_encryption'), patch.object(vb, 'tool', return_value='idevicebackup2'), patch.object(vb.subprocess, 'Popen', return_value=process), patch.object(vb.os, 'scandir', return_value=nullcontext(iter([transient, stable]))):
            reader = vb.create_backup(self.job, self.udid, 10*vb.GIB, 2*vb.GIB, Event(), lambda _:None)
        self.assertEqual(reader.root, self.root.resolve())
        process.terminate.assert_not_called()
        process.kill.assert_not_called()


class LiveBackupUsageTests(unittest.TestCase):
    """Reproduce Snapshot movement without any real phone or backup."""
    def entry(self, path, size=10, mode=None, error=None):
        import stat
        def read_stat(*, follow_symlinks):
            if follow_symlinks:
                raise AssertionError('The scanner must not follow symlinks')
            if error:
                raise error
            return SimpleNamespace(st_size=size, st_mode=mode if mode is not None else stat.S_IFREG)
        return SimpleNamespace(path=str(path), stat=read_stat)

    def scan_context(self, entries):
        from contextlib import nullcontext
        return nullcontext(iter(entries))

    def test_snapshot_file_disappearing_during_stat_is_not_a_failure(self):
        root = Path('/synthetic-owned-backup')
        entries = [self.entry(root/'Snapshot'/'a5'/'moving', error=FileNotFoundError(2, 'moved by writer')),
                   self.entry(root/'still-being-written', size=128)]
        with patch.object(vb.os, 'scandir', return_value=self.scan_context(entries)):
            self.assertEqual(vb.usage(root), 128)

    def test_queued_snapshot_directory_can_move_before_scan(self):
        import stat
        root = Path('/synthetic-owned-backup')
        entries = [self.entry(root/'Snapshot', mode=stat.S_IFDIR), self.entry(root/'Manifest.db', size=256)]
        def scan(folder):
            if folder == root:
                return self.scan_context(entries)
            raise FileNotFoundError(2, 'Snapshot renamed')
        with patch.object(vb.os, 'scandir', side_effect=scan):
            self.assertEqual(vb.usage(root), 256)

    def test_directory_replaced_by_file_is_transient(self):
        import stat
        root = Path('/synthetic-owned-backup')
        entries = [self.entry(root/'Snapshot', mode=stat.S_IFDIR), self.entry(root/'file', size=128)]
        def scan(folder):
            if folder == root:
                return self.scan_context(entries)
            raise NotADirectoryError(20, 'Snapshot replaced')
        with patch.object(vb.os, 'scandir', side_effect=scan):
            self.assertEqual(vb.usage(root), 128)

    def test_missing_job_root_is_not_reported_as_zero_capacity(self):
        with patch.object(vb.os, 'scandir', side_effect=FileNotFoundError(2, 'job removed')):
            with self.assertRaises(FileNotFoundError):
                vb.usage(Path('/synthetic-owned-backup'))

    def test_permission_and_disk_errors_are_not_suppressed(self):
        root = Path('/synthetic-owned-backup')
        for error in (PermissionError(13, 'permission denied'), OSError(5, 'disk I/O error')):
            with self.subTest(error=type(error).__name__):
                entries = [self.entry(root/'Snapshot'/'file', error=error)]
                with patch.object(vb.os, 'scandir', return_value=self.scan_context(entries)):
                    with self.assertRaises(type(error)):
                        vb.usage(root)

    def test_symlinks_are_excluded_and_single_stat_counts_regular_files(self):
        import stat
        root = Path('/synthetic-owned-backup')
        entries = [self.entry(root/'external-link', size=999999, mode=stat.S_IFLNK), self.entry(root/'file', size=512)]
        with patch.object(vb.os, 'scandir', return_value=self.scan_context(entries)):
            self.assertEqual(vb.usage(root), 512)
