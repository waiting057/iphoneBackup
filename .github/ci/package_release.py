"""Prepare downloadable files from the exact artifact in BUILD-INFO.json."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def version_label(ref_type, ref_name, revision):
    version = ref_name if ref_type == 'tag' else 'build-' + revision[:12]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', version):
        raise ValueError('Invalid release version; use a simple version tag such as v0.1.0')
    if not re.fullmatch(r'[a-fA-F0-9]{40,64}', revision):
        raise ValueError('Expected full GitHub commit SHA')
    return version


def prepare(root, platform, version, revision):
    folder = root / 'output' / platform
    info = json.loads((folder / 'BUILD-INFO.json').read_text(encoding='utf-8'))
    if info.get('platform') != platform or not info.get('packaging_check', {}).get('ok'):
        raise ValueError('Build metadata does not confirm successful packaging')
    if not info['packaging_check'].get('bundled_backup_tools'):
        raise ValueError('Backup CLI packaging checks must pass')
    name = info['artifact']
    if Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError('Invalid artifact path')
    artifact = folder / name
    if not artifact.resolve().is_relative_to(folder.resolve()):
        raise ValueError('Artifact must be inside its platform output folder')
    release = root / 'output' / 'release'
    release.mkdir(parents=True, exist_ok=True)
    if any(release.iterdir()):
        raise ValueError('Release staging must be empty; do not mix builds')
    target = 'macOS-arm64' if platform == 'Mac' else 'Windows-x64'
    if platform == 'Mac' and info['architecture'].lower() not in {'arm64', 'aarch64'}:
        raise ValueError('Expected Mac arm64 build')
    if platform == 'Windows' and info['architecture'].lower() not in {'amd64', 'x86_64'}:
        raise ValueError('Expected Windows x64 build')
    product = release / f'iPhoneBackUp-{version}-{target}'
    if platform == 'Mac':
        if not artifact.is_dir() or artifact.suffix != '.app':
            raise ValueError('Mac App bundle not found')
        product = product.with_name(product.name + '.zip')
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(artifact), str(product)], check=True)
    else:
        if not artifact.is_file() or artifact.suffix.lower() != '.exe':
            raise ValueError('Windows executable not found')
        product = product.with_name(product.name + '.exe')
        shutil.copyfile(artifact, product)
        # Also ship accessible native license/source notices next to the EXE.
        licenses = root / 'Windows/native-ci/licenses'
        if not licenses.is_dir() or not any(licenses.rglob('*')):
            raise ValueError('Native license notices are missing')
        with zipfile.ZipFile(release / f'LICENSES-{version}-{target}.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(licenses.rglob('*')):
                if path.is_file():
                    archive.write(path, 'licenses/' + path.relative_to(licenses).as_posix())
    info.update(source_commit=revision, version=version, ci_run_id=os.environ.get('GITHUB_RUN_ID'),
                device_acceptance_tested=False)
    (release / f'BUILD-INFO-{target}.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    (release / f'README-{target}.txt').write_text(
        f'iPhoneBackUp {version} / {target}\nSource commit: {revision}\n\n'
        'Test build: phone acceptance testing is still required.\n'
        'Mac: unzip the App ZIP, then open the .app. Apple Silicon only.\n'
        'Mac build uses ad-hoc signing, without Developer ID notarization.\n'
        'Windows: the EXE is unsigned; Apple iPhone USB drivers/services are required.\n'
        'Voice Memos: encrypted backups are not supported; full library coverage is unverified.\n'
        'Follow the App prompts for temporary backup size and cleanup.\n', encoding='utf-8')
    entries = sorted(path for path in release.iterdir() if path.is_file())
    (release / f'SHA256SUMS-{target}.txt').write_text(''.join(f'{sha256(path)}  {path.name}\n' for path in entries), encoding='utf-8')
    return product


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', choices=['Mac', 'Windows'], required=True)
    args = parser.parse_args()
    revision = os.environ['GITHUB_SHA']
    version = version_label(os.environ.get('GITHUB_REF_TYPE'), os.environ.get('GITHUB_REF_NAME'), revision)
    root = Path(__file__).resolve().parents[2]
    print(prepare(root, args.platform, version, revision))


if __name__ == '__main__':
    main()
