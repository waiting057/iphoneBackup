"""Build, verify, then publish to project output/."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import uuid

from packaging_support import prepare_native, prepare_backup_tools


def main():
    parser = argparse.ArgumentParser(description="建置目前平台的獨立 App。")
    parser.add_argument("--native-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    expected = "darwin" if root.name == "Mac" else "win32"
    if sys.platform != expected:
        raise SystemExit("請在對應平台建置；不能用 Mac 打包 Windows 可執行檔。")
    modules = ["PySide6", "PIL", "PyInstaller"] + (["pillow_heif"] if expected == "win32" else [])
    for module in modules:
        if importlib.util.find_spec(module) is None:
            raise SystemExit(f"缺少建置依賴 {module}，請先安裝 {root.name}/requirements.txt。")
    build_id = uuid.uuid4().hex[:12]
    work = root / "build" / build_id
    work.mkdir(parents=True)
    supplied = args.native_dir
    if supplied is None and any((root / "native").glob("*.dylib" if expected == "darwin" else "*.dll")):
        supplied = root / "native"
    try:
        prepare_native(expected, supplied, work / "native")
        libraries = prepare_backup_tools(expected, supplied, work / "native")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"USB 元件準備失敗：{error}") from error
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if python_license.is_file():
        destination = work / "native" / "licenses" / "Python"
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(python_license, destination / "LICENSE.txt")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed",
               "--onedir" if expected == "darwin" else "--onefile", "--name", "iPhoneBackUp",
               "--distpath", str(work / "dist"), "--workpath", str(work / "pyinstaller"),
               "--specpath", str(work), "--paths", str(root)]
    if expected == "darwin":
        command += ["--osx-bundle-identifier", "local.iphonebackup.app"]
    for library in libraries:
        command += ["--add-binary", f"{work / 'native' / library['name']}:native"]
    command += ["--add-data", f"{work / 'native' / 'licenses'}:native/licenses"]
    for distribution in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "Pillow", "pyinstaller"):
        command += ["--copy-metadata", distribution]
    if expected == "win32":
        command += ["--copy-metadata", "pillow-heif"]
    command.append(str(root / "main.py"))
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(work / "cache")
    print(f"封裝 {root.name} {platform.machine()}，USB 元件 {len(libraries)} 個…", flush=True)
    subprocess.run(command, cwd=root, env=environment, check=True)
    name = "iPhoneBackUp.app" if expected == "darwin" else "iPhoneBackUp.exe"
    artifact = work / "dist" / name
    executable = artifact / "Contents/MacOS/iPhoneBackUp" if expected == "darwin" else artifact
    report = work / "packaging-check.json"
    subprocess.run([str(executable), "--packaging-check", str(report)], env=environment, timeout=90, check=True)
    check = json.loads(report.read_text(encoding="utf-8"))
    if not check.get("ok") or not check.get("bundled_usb"):
        raise SystemExit(f"封裝檢查失敗，未發布：{check}")
    if expected == "darwin":
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(artifact)], check=True)
    output = root.parent / "output" / root.name
    output.mkdir(parents=True, exist_ok=True)
    target = output / name
    previous = None
    if target.exists():
        previous = root / "build" / "previous" / build_id / name
        previous.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.rename(previous)
        except PermissionError:
            # Preserve the existing App if a running process or managed host
            # prevents moving it. Publish the new verified build separately.
            previous = None
            target = output / (f"iPhoneBackUp-{build_id}.app" if expected == "darwin" else f"iPhoneBackUp-{build_id}.exe")
            print(f"原有 App 無法搬移，將保留舊版並輸出新版：{target.name}", flush=True)
    try:
        if artifact.is_dir() and previous is None:
            # Initial publication may copy the verified bundle, preserving
            # relative symlinks and the signed contents without directory moves.
            shutil.copytree(artifact, target, symlinks=True)
        else:
            artifact.rename(target)
    except OSError:
        if previous is not None:
            previous.rename(target)
        raise
    info = {"built_at_utc": datetime.now(timezone.utc).isoformat(), "platform": root.name,
            "architecture": platform.machine(), "artifact": target.name, "build_id": build_id,
            "packaging_check": check, "native_libraries": libraries,
            "device_acceptance_tested": False,
            "distribution_signing": "ad-hoc, not notarized" if expected == "darwin" else "unsigned"}
    (output / "BUILD-INFO.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"建置成功：{target}", flush=True)
    print("已檢查獨立啟動依賴與內附 USB 元件；手機實測仍需另行驗證。", flush=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
