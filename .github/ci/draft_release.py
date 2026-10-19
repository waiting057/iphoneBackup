"""Create/update a draft only after both platform assets verify successfully."""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def verify_assets(folder, revision, tag):
    files = sorted(path for path in folder.iterdir() if path.is_file())
    checked = set()
    for target, suffix in (('macOS-arm64', '.zip'), ('Windows-x64', '.exe')):
        product = folder / f'iPhoneBackUp-{tag}-{target}{suffix}'
        if not product.is_file():
            raise ValueError(f'Missing platform product: {product.name}')
        info = json.loads((folder / f'BUILD-INFO-{target}.json').read_text(encoding='utf-8'))
        if info.get('source_commit') != revision or info.get('version') != tag or not info.get('packaging_check', {}).get('ok'):
            raise ValueError('Build commit/version or packaging result mismatch')
        for line in (folder / f'SHA256SUMS-{target}.txt').read_text(encoding='utf-8').splitlines():
            expected, name = line.split('  ', 1)
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('Unsafe checksum path')
            with (folder / name).open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if expected != actual:
                raise ValueError(f'Checksum mismatch: {name}')
            checked.add(name)
        if product.name not in checked:
            raise ValueError('Product checksum is missing')
    checksum_files = {f'SHA256SUMS-{target}.txt' for target in ('macOS-arm64', 'Windows-x64')}
    if checked | checksum_files != {path.name for path in files}:
        raise ValueError('Unverified or unexpected release attachments')
    return files


def main():
    tag, revision = os.environ['GITHUB_REF_NAME'], os.environ['GITHUB_SHA']
    repository = os.environ['GITHUB_REPOSITORY']
    folder = Path('release-assets')
    files = verify_assets(folder, revision, tag)
    result = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository, '--json', 'isDraft'], capture_output=True, text=True)
    if result.returncode == 0:
        if not json.loads(result.stdout)['isDraft']:
            raise ValueError('Refusing to overwrite an already published release')
        subprocess.run(['gh', 'release', 'upload', tag, '--repo', repository, '--clobber', *map(str, files)], check=True)
    else:
        notes = Path(os.environ.get('RUNNER_TEMP', '.')) / 'iphonebackup-release-notes.md'
        notes.write_text('此版本由 GitHub Actions 封裝，尚須 Mac／Windows 連接實體 iPhone 驗證。\n\n'
            '請下載 App ZIP／EXE 附件；Source code ZIP 是原始碼。\n\n'
            'Mac 為 Apple Silicon、ad-hoc 簽章且未公證；Windows 未簽章，需 Apple USB 驅動／服務。\n\n'
            '請在實機測試通過後再發布此草稿。\n', encoding='utf-8')
        subprocess.run(['gh', 'release', 'create', tag, '--repo', repository, '--draft', '--verify-tag',
                        '--title', tag, '--notes-file', str(notes), *map(str, files)], check=True)


if __name__ == '__main__':
    main()
