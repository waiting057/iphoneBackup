"""Mirror platform-independent code without adding a shared root directory.

Mac contains the maintained common code; Windows contains an independent copy.
Platform README, native libraries, startup scripts and build outputs are untouched.
Run with --check in CI to detect drift without writing files.
"""
import shutil
import sys
from pathlib import Path


def main():
    mac = Path(__file__).resolve().parents[1]
    windows = mac.parent / "Windows"
    sources = [mac / name for name in ("main.py", "build.py", "packaging_support.py", "requirements.txt")]
    sources += sorted((mac / "iphonebackup").glob("*.py"))
    sources += sorted((mac / "tests").glob("test_*.py"))
    sources += [mac / "tools" / "probe_usb.py"]
    drift = []
    for source in sources:
        destination = windows / source.relative_to(mac)
        if not destination.exists() or source.read_bytes() != destination.read_bytes():
            drift.append(str(source.relative_to(mac)))
            if "--check" not in sys.argv:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
    if "--check" in sys.argv and drift:
        print("平台程式不同步：" + ", ".join(drift))
        return 1
    print("兩平台共用程式已同步。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
