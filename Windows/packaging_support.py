"""Collect installed native USB libraries; do not download executable code."""
import ctypes.util
import hashlib
import shutil
import subprocess
from pathlib import Path


def macos_libraries(primary: Path, primary_name="libimobiledevice-1.0.dylib"):
    queue = [(primary, primary_name)]
    discovered = {}
    while queue:
        source, name = queue.pop()
        source = source.resolve(strict=True)
        if name in discovered:
            if discovered[name] != source:
                raise ValueError(f"原生函式庫名稱衝突：{name}")
            continue
        discovered[name] = source
        result = subprocess.run(["/usr/bin/otool", "-L", str(source)], check=True, text=True, capture_output=True)
        for line in result.stdout.splitlines()[1:]:
            dependency = line.strip().split(" (compatibility version", 1)[0]
            if not dependency or dependency.startswith(("/System/Library/", "/usr/lib/")):
                continue
            path = Path(dependency)
            if dependency.startswith("@loader_path/"):
                path = source.parent / dependency.removeprefix("@loader_path/")
            elif dependency.startswith("@rpath/"):
                basename = Path(dependency).name
                path = next((directory / basename for directory in (source.parent, primary.parent, Path("/opt/homebrew/lib"), Path("/usr/local/lib")) if (directory / basename).exists()), path)
            if not path.is_file():
                raise ValueError(f"原生函式庫依賴不存在：{dependency}")
            if path.resolve() != source:
                queue.append((path, path.name))
    return discovered


def homebrew_license_root(binary: Path):
    return next((directory for directory in binary.resolve().parents if directory.parent.parent.name == "Cellar"), None)


def copy_licenses(source: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for pattern in ("COPYING*", "LICENSE*", "LICENCE*", "NOTICE*"):
        for path in source.glob(pattern):
            if path.is_file() and path.suffix.lower() not in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}:
                shutil.copyfile(path, destination / path.name)
                copied.append(path.name)
    return copied


def prepare_native(platform: str, supplied: Path | None, stage: Path):
    stage.mkdir(parents=True, exist_ok=True)
    extension = ".dylib" if platform == "darwin" else ".dll"
    if supplied is not None:
        libraries = sorted(supplied.glob(f"*{extension}"))
        if not any("imobiledevice" in file.name for file in libraries):
            raise ValueError(f"{supplied} 缺少 libimobiledevice 的 {extension}。")
        licenses = supplied / "licenses"
        if not licenses.is_dir() or not any(licenses.iterdir()):
            raise ValueError("自備原生元件需在 licenses/ 放入所有元件授權文件。")
        for file in libraries:
            shutil.copyfile(file, stage / file.name)
        shutil.copytree(licenses, stage / "licenses", dirs_exist_ok=True)
    elif platform == "darwin":
        candidates = [Path("/opt/homebrew/lib/libimobiledevice-1.0.dylib"), Path("/usr/local/lib/libimobiledevice-1.0.dylib")]
        found = ctypes.util.find_library("imobiledevice-1.0")
        if found:
            candidates.insert(0, Path(found))
        primary = next((path for path in candidates if path.is_file()), None)
        if primary is None:
            raise ValueError("缺少 USB 套件，請先在本機終端機執行 brew install libimobiledevice。")
        components = set()
        for name, source in macos_libraries(primary).items():
            shutil.copyfile(source, stage / name)
            license_root = homebrew_license_root(source)
            if license_root is None:
                raise ValueError(f"無法自動取得 {name} 的授權文件，請改用 --native-dir 提供完整元件。")
            component = license_root.parent.name
            if component not in components:
                if not copy_licenses(license_root, stage / "licenses" / component):
                    raise ValueError(f"{component} 缺少可複製的授權文件。")
                (stage / "licenses" / component / "SOURCE.txt").write_text(
                    f"Component: {component}\nInstalled version: {license_root.name}\n"
                    f"Homebrew formula and upstream source: https://formulae.brew.sh/formula/{component}\n"
                    "Dynamic library is bundled separately and may be replaced by a compatible build.\n", encoding="utf-8")
                components.add(component)
    else:
        raise ValueError("Windows 請先將 libimobiledevice DLL、全部相依 DLL 及 licenses/ 放到 Windows/native/，或用 --native-dir 指定。")
    return [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(stage.iterdir()) if path.is_file() and path.suffix in {".dll", ".dylib"}]


def prepare_backup_tools(platform, supplied, stage):
    """Bundle backup and read-only info CLIs with their complete dependencies."""
    names = ('idevicebackup2', 'ideviceinfo')
    sources = []
    for name in names:
        filename = name + ('.exe' if platform == 'win32' else '')
        if supplied:
            source = supplied / filename
        else:
            found = shutil.which(filename)
            source = Path(found) if found else next((Path(folder) / filename for folder in ('/opt/homebrew/bin', '/usr/local/bin') if (Path(folder) / filename).is_file()), Path(filename))
        if not source.is_file():
            raise ValueError(f'缺少備份工具 {filename}，請安裝 libimobiledevice 或放入 native/。')
        sources.append(source)
    for source in sources:
        if platform == 'darwin':
            dependencies = macos_libraries(source, source.name)
        else:
            dependencies = {source.name: source}
        for name, binary in dependencies.items():
            destination = stage / name
            if not destination.exists():
                shutil.copyfile(binary, destination)
            if name == source.name:
                destination.chmod(destination.stat().st_mode | 0o111)
            if not supplied:
                license_root = homebrew_license_root(binary)
                if license_root is None or not copy_licenses(license_root, stage / 'licenses' / license_root.parent.name):
                    raise ValueError(f'缺少 {name} 授權文件。')
    return [{'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(stage.iterdir()) if path.is_file()]
