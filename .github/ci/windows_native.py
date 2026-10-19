"""Collect UCRT64 package binaries and their complete non-system PE imports.

Run with the setup-python interpreter on a Windows runner after setup-msys2.
No device access; package sources are read only to preserve original licenses.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile


def run(command, **kwargs):
    return subprocess.run([str(value) for value in command], check=True, capture_output=True,
                          timeout=300, **kwargs)


def imports(binary, objdump):
    output = run([objdump, '-p', binary], text=True, errors='replace').stdout
    if 'file format pei-x86-64' not in output:
        raise ValueError(f'Expected Windows x64 PE: {binary}')
    names = re.findall(r'DLL Name:\s*(\S+)', output, flags=re.IGNORECASE)
    if not names:
        raise ValueError(f'Could not inspect PE imports: {binary}')
    for name in names:
        if Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError(f'Invalid DLL import name: {name}')
    return names


def dependency_closure(roots, binary_dir, system_dir, inspect):
    available = {path.name.casefold(): path for path in binary_dir.iterdir() if path.is_file()}
    queue, found = list(roots), {}
    while queue:
        binary = queue.pop()
        key = binary.name.casefold()
        if key in found:
            continue
        if not binary.is_file():
            raise FileNotFoundError(binary)
        found[key] = binary
        for name in inspect(binary):
            key = name.casefold()
            if key in available:
                queue.append(available[key])
            elif key.startswith(('api-ms-win-', 'ext-ms-win-')) or (system_dir / name).is_file():
                continue
            else:
                raise ValueError(f'Missing native dependency {name}, imported by {binary.name}')
    return tuple(sorted(found.values(), key=lambda path: path.name.casefold()))


def parse_desc(text):
    result, key = {}, None
    for line in text.splitlines():
        if line.startswith('%') and line.endswith('%'):
            key = line.strip('%')
            result[key] = []
        elif line and key:
            result[key].append(line)
    return result


def owner_metadata(binary, msys_root, prefix):
    pacman = msys_root / 'usr/bin/pacman.exe'
    name = run([pacman, '-Qoq', f'/{prefix}/bin/{binary.name}'], text=True).stdout.strip()
    if not re.fullmatch(r'[a-zA-Z0-9@_.+-]+', name):
        raise ValueError(f'Package owner not identified: {binary}')
    for desc in (msys_root / 'var/lib/pacman/local').glob('*/desc'):
        metadata = parse_desc(desc.read_text(encoding='utf-8', errors='replace'))
        if metadata.get('NAME') == [name]:
            if not metadata.get('BASE') or not metadata.get('VERSION'):
                raise ValueError(f'Package source metadata missing: {name}')
            return metadata
    raise ValueError(f'Installed package metadata not found: {name}')


def license_member(name):
    base = PurePosixPath(name).name.upper()
    return not name.endswith('/') and base.startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'AUTHORS'))


def collect_archive_licenses(archive, destination, tar, scratch, depth=0):
    """Read member streams, never extract paths or execute package scripts."""
    members = run([tar, '-tf', archive], text=True, errors='replace').stdout.splitlines()
    count = 0
    for name in members:
        if name.startswith(('/', '-')) or '..' in PurePosixPath(name).parts:
            raise ValueError(f'Unsafe source archive member: {name}')
        if license_member(name):
            # tar may enumerate symlinks; only retain nonempty UTF-8 text.
            data = run([tar, '-xOf', archive, '--', name]).stdout
            if not data or len(data) > 2 * 1024 * 1024 or b'\0' in data:
                continue
            name_hash = hashlib.sha256(name.encode()).hexdigest()[:12]
            (destination / f'{name_hash}-{PurePosixPath(name).name}').write_bytes(data)
            count += 1
        elif depth < 2 and name.endswith(('.tar.gz', '.tar.xz', '.tar.bz2', '.tar.zst', '.tgz')):
            suffix = ''.join(PurePosixPath(name).suffixes)
            nested = scratch / (hashlib.sha256((str(archive) + name).encode()).hexdigest() + suffix)
            with nested.open('wb') as stream:
                subprocess.run([str(tar), '-xOf', str(archive), '--', name], stdout=stream, check=True, timeout=300)
            count += collect_archive_licenses(nested, destination, tar, scratch, depth + 1)
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--msys-root', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows runner only')
    msys = args.msys_root.resolve()
    prefix = 'ucrt64'
    binary_dir = msys / prefix / 'bin'
    objdump = binary_dir / 'objdump.exe'
    system = Path(os.environ['SystemRoot']) / 'System32'
    roots = [binary_dir / name for name in ('libimobiledevice-1.0.dll', 'libplist-2.0.dll', 'idevicebackup2.exe', 'ideviceinfo.exe')]
    binaries = dependency_closure(roots, binary_dir, system, lambda binary: imports(binary, objdump))
    destination = args.destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Use a fresh native staging directory')
    destination.mkdir(parents=True, exist_ok=True)
    package_metadata = {}
    for binary in binaries:
        shutil.copyfile(binary, destination / binary.name)
        metadata = owner_metadata(binary, msys, prefix)
        package_metadata[metadata['NAME'][0]] = metadata
    sources = []
    with tempfile.TemporaryDirectory(prefix='iphonebackup-native-sources-') as scratch_name:
        scratch = Path(scratch_name)
        completed = set()
        for package, metadata in sorted(package_metadata.items()):
            base, version = metadata['BASE'][0], metadata['VERSION'][0].split(':')[-1]
            if not re.fullmatch(r'[A-Za-z0-9_.+-]+', base + version):
                raise ValueError('Invalid package source name')
            url = f'https://mirror.msys2.org/mingw/sources/{base}-{version}.src.tar.zst'
            sources.append({'package': package, 'version': metadata['VERSION'][0],
                            'source_url': url, 'licenses': metadata.get('LICENSE', [])})
            if (base, version) in completed:
                continue
            completed.add((base, version))
            licenses = destination / 'licenses' / base
            licenses.mkdir(parents=True)
            # Some packages already install full upstream license text.
            package_licenses = msys / prefix / 'share/licenses' / package.removeprefix('mingw-w64-ucrt-x86_64-')
            for file in package_licenses.rglob('*') if package_licenses.is_dir() else ():
                if file.is_file():
                    shutil.copyfile(file, licenses / file.name)
            archive = scratch / f'{base}-{version}.src.tar.zst'
            run([msys / 'usr/bin/curl.exe', '--fail', '--location', '--retry', '3', '--silent', '--show-error', '--output', archive, url])
            count = collect_archive_licenses(archive, licenses, msys / 'usr/bin/tar.exe', scratch)
            if not count and not any(licenses.iterdir()):
                raise ValueError(f'No upstream license text found: {package}')
            with archive.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            (licenses / 'SOURCE.txt').write_text(f'MSYS2 package source: {url}\nSource archive SHA256: {digest}\nPackage recipes and patches: https://github.com/msys2/MINGW-packages/tree/master/{base}\n', encoding='utf-8')
    manifest = {'provider': 'MSYS2 UCRT64 signed packages; versions recorded at build time',
                'packages': sources, 'binaries': []}
    for path in sorted(destination.iterdir()):
        if path.is_file():
            manifest['binaries'].append({'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (destination / 'licenses/NATIVE-MANIFEST.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Collected {len(binaries)} native binaries and original source licenses.')


if __name__ == '__main__':
    main()
