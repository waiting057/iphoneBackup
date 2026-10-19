"""CI helper checks use synthetic binaries/metadata, never an attached phone."""
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, CI / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native, package, draft = (load(name) for name in ('windows_native', 'package_release', 'draft_release'))


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        for path in self.root.rglob('*'):
            if path.is_file():
                path.unlink()
        self.temp.cleanup()

    def test_pe_dependency_closure_recurses_and_excludes_system(self):
        binary_dir, system = self.root/'bin', self.root/'System32'
        binary_dir.mkdir(); system.mkdir()
        for folder, name in ((binary_dir,'tool.exe'), (binary_dir,'library.dll'), (binary_dir,'transitive.dll'), (system,'kernel32.dll')):
            (folder/name).touch()
        imports = {'tool.exe':['LIBRARY.DLL','kernel32.dll'], 'library.dll':['transitive.dll'], 'transitive.dll':['api-ms-win-test.dll']}
        result = native.dependency_closure([binary_dir/'tool.exe'], binary_dir, system, lambda path:imports[path.name])
        self.assertEqual({path.name for path in result}, {'tool.exe','library.dll','transitive.dll'})

    def test_missing_transitive_dll_fails(self):
        (self.root/'tool.exe').touch()
        with self.assertRaisesRegex(ValueError, 'Missing native dependency'):
            native.dependency_closure([self.root/'tool.exe'], self.root, self.root/'System32', lambda _:['missing.dll'])

    def test_package_metadata_reads_base_version_licenses(self):
        text = '%NAME%\nmingw-w64-ucrt-x86_64-example\n\n%BASE%\nmingw-w64-example\n\n%VERSION%\n1.2.3-4\n\n%LICENSE%\nLGPL-2.1-or-later\n\n'
        result = native.parse_desc(text)
        self.assertEqual(result['BASE'], ['mingw-w64-example'])
        self.assertEqual(result['VERSION'], ['1.2.3-4'])
        self.assertEqual(result['LICENSE'], ['LGPL-2.1-or-later'])

    def test_license_collection_reads_nested_sources(self):
        tar = shutil.which('tar')
        if not tar:
            self.skipTest('tar is required to read MSYS2 source archives')
        inner = io.BytesIO()
        with tarfile.open(fileobj=inner, mode='w:gz') as archive:
            data = b'Test fixture license, not an actual native library'
            item = tarfile.TarInfo('example/COPYING'); item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        outer = self.root/'source.tar'
        with tarfile.open(outer, 'w') as archive:
            item = tarfile.TarInfo('example/upstream.tar.gz'); item.size = len(inner.getvalue())
            archive.addfile(item, io.BytesIO(inner.getvalue()))
        destination = self.root/'licenses'; destination.mkdir()
        scratch = self.root/'scratch'; scratch.mkdir()
        count = native.collect_archive_licenses(outer,destination,Path(tar),scratch)
        self.assertEqual(count,1)
        self.assertIn(b'Test fixture license', next(destination.iterdir()).read_bytes())

    def test_windows_package_uses_metadata_and_emits_verified_checksums(self):
        folder = self.root/'output/Windows'; folder.mkdir(parents=True)
        (folder/'exact-build.exe').write_bytes(b'Synthetic executable fixture')
        info = {'platform':'Windows','architecture':'AMD64','artifact':'exact-build.exe',
                'packaging_check':{'ok':True,'bundled_backup_tools':True}}
        (folder/'BUILD-INFO.json').write_text(json.dumps(info))
        licenses = self.root/'Windows/native-ci/licenses'; licenses.mkdir(parents=True)
        (licenses/'COPYING').write_text('Synthetic license fixture')
        product = package.prepare(self.root,'Windows','v0.1.0','a'*40)
        self.assertEqual(product.read_bytes(),b'Synthetic executable fixture')
        self.assertTrue(product.with_name('LICENSES-v0.1.0-Windows-x64.zip').is_file())
        checksums = product.parent/'SHA256SUMS-Windows-x64.txt'
        for line in checksums.read_text().splitlines():
            expected,name = line.split('  ',1)
            self.assertEqual(expected,hashlib.sha256((product.parent/name).read_bytes()).hexdigest())

    def test_failed_packaging_and_path_escape_are_rejected(self):
        folder = self.root/'output/Windows'; folder.mkdir(parents=True)
        info={'platform':'Windows','architecture':'AMD64','artifact':'../outside.exe','packaging_check':{'ok':True,'bundled_backup_tools':True}}
        (folder/'BUILD-INFO.json').write_text(json.dumps(info))
        with self.assertRaisesRegex(ValueError,'Invalid artifact path'):
            package.prepare(self.root,'Windows','v0.1.0','a'*40)
        info['packaging_check']['ok']=False
        (folder/'BUILD-INFO.json').write_text(json.dumps(info))
        with self.assertRaisesRegex(ValueError,'successful packaging'):
            package.prepare(self.root,'Windows','v0.1.0','a'*40)

    def release_fixtures(self):
        for target,suffix in (('macOS-arm64','.zip'),('Windows-x64','.exe')):
            product=self.root/f'iPhoneBackUp-v0.1.0-{target}{suffix}'; product.write_bytes(b'synthetic download')
            info=self.root/f'BUILD-INFO-{target}.json'
            info.write_text(json.dumps({'source_commit':'a'*40,'version':'v0.1.0','packaging_check':{'ok':True}}))
            (self.root/f'SHA256SUMS-{target}.txt').write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in (product,info)))

    def test_release_needs_both_platforms_same_commit_and_valid_hashes(self):
        self.release_fixtures()
        files=draft.verify_assets(self.root,'a'*40,'v0.1.0')
        self.assertEqual(len(files),6)
        (self.root/'iPhoneBackUp-v0.1.0-Windows-x64.exe').write_bytes(b'changed bytes')
        with self.assertRaisesRegex(ValueError,'Checksum mismatch'):
            draft.verify_assets(self.root,'a'*40,'v0.1.0')

    def test_release_rejects_mixed_commit_and_missing_platform(self):
        self.release_fixtures()
        with self.assertRaisesRegex(ValueError,'mismatch'):
            draft.verify_assets(self.root,'b'*40,'v0.1.0')
        (self.root/'iPhoneBackUp-v0.1.0-Windows-x64.exe').unlink()
        with self.assertRaisesRegex(ValueError,'Missing platform product'):
            draft.verify_assets(self.root,'a'*40,'v0.1.0')

    def test_tag_filename_validation(self):
        self.assertEqual(package.version_label('branch','feature/slash','a'*40),'build-'+'a'*12)
        with self.assertRaises(ValueError):
            package.version_label('tag','v0.1.0/unsafe','a'*40)


class WindowsArchiveRegressionTests(unittest.TestCase):
    def test_windows_drive_path_is_cwd_not_a_tar_archive_argument(self):
        from pathlib import PureWindowsPath
        from types import SimpleNamespace
        archive = PureWindowsPath('C:/Users/Runner Name/Temp/sources/package.src.tar.zst')
        tar = PureWindowsPath('D:/a/_temp/msys64/usr/bin/tar.exe')
        with patch.object(native, 'run', return_value=SimpleNamespace(stdout='')) as execute:
            native.archive_run(tar, archive, '-tf', text=True)
        arguments, options = execute.call_args
        self.assertEqual(arguments[0], [tar, '-tf', 'package.src.tar.zst'])
        self.assertEqual(options['cwd'], archive.parent)
        self.assertTrue(options['env']['PATH'].startswith(str(tar.parent)))
        self.assertNotIn(':', arguments[0][2])

    def test_all_extractions_use_relative_archive_and_keep_member_literal(self):
        from pathlib import PureWindowsPath
        from types import SimpleNamespace
        archive = PureWindowsPath('C:/Users/Runner Name/Temp/nested.tar.xz')
        tar = PureWindowsPath('D:/msys64/usr/bin/tar.exe')
        member = 'package/name with spaces/COPYING'
        stream = io.BytesIO()
        with patch.object(native, 'run', return_value=SimpleNamespace(stdout=None)) as execute:
            native.archive_run(tar, archive, '-xOf', (member,), stdout=stream)
        arguments, options = execute.call_args
        self.assertEqual(arguments[0], [tar, '-xOf', archive.name, '--', member])
        self.assertIs(options['stdout'], stream)
        self.assertEqual(options['cwd'], archive.parent)

    def test_captured_failure_includes_stderr_in_action_log(self):
        import subprocess
        failure = subprocess.CalledProcessError(128, ['tar'], stderr=b'tar: Cannot connect to C: resolve failed')
        with patch.object(native.subprocess, 'run', side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, 'Cannot connect to C'):
                native.run(['tar', '-tf', 'C:/archive.tar.zst'])

    def test_streamed_failure_includes_decompressor_stderr(self):
        import subprocess
        stream = io.BytesIO()
        failure = subprocess.CalledProcessError(2, ['tar'], stderr=b'zstd: command not found')
        with patch.object(native.subprocess, 'run', side_effect=failure) as execute:
            with self.assertRaisesRegex(RuntimeError, 'zstd: command not found'):
                native.run(['tar', '-xOf', 'nested.tar.zst'], stdout=stream)
        options = execute.call_args.kwargs
        self.assertIs(options['stdout'], stream)
        self.assertEqual(options['stderr'], subprocess.PIPE)
        self.assertNotIn('capture_output', options)

    def test_timeout_includes_available_stderr(self):
        import subprocess
        failure = subprocess.TimeoutExpired(['tar'], 300, stderr=b'partial decompressor error')
        with patch.object(native.subprocess, 'run', side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, 'partial decompressor error'):
                native.run(['tar', '-tf', 'source.tar.zst'])
