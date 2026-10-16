"""One build entry point, executed on the OS being packaged."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="封裝目前平台的 iPhoneBackUp 到專案 output/。")
    parser.add_argument("--target", choices=("auto", "mac", "windows", "all"), default="auto")
    parser.add_argument("--native-dir", type=Path, help="自備原生 USB 函式庫與 licenses/ 的目錄")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    host = {"darwin": "mac", "win32": "windows"}.get(sys.platform)
    if host is None:
        parser.error("目前僅支援在 macOS 或 Windows 上建置。")
    if args.target == "all":
        parser.error("雙平台須分別在 macOS 與 Windows 建置；請在兩個環境執行相同 build。")
    if args.target not in ("auto", host):
        parser.error("請在目標作業系統建置，Mac 不能直接打包 Windows，Windows 也不能直接打包 Mac。")
    platform = root / ("Mac" if host == "mac" else "Windows")
    python = platform / (".venv/bin/python" if host == "mac" else ".venv/Scripts/python.exe")
    if not python.is_file():
        python = Path(sys.executable)
    subprocess.run([str(python), str(root / "Mac/tools/sync_windows.py"), "--check"], check=True)
    command = [str(python), str(platform / "build.py")]
    if args.native_dir:
        command += ["--native-dir", str(args.native_dir.absolute())]
    return subprocess.run(command, cwd=root).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
