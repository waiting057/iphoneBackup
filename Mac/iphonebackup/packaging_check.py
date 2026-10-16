"""Check a frozen executable without opening UI or querying any phone."""
import json
from pathlib import Path
import sys


def main(report: Path):
    result = {"ok": False, "bundled_usb": False, "frozen": bool(getattr(sys, "frozen", False))}
    try:
        from PySide6 import QtCore, QtGui, QtWidgets, QtSvg
        from PIL import Image
        from .usb import Library
        from .voice_usb import Plist
        library = Library()
        plist = Plist()
        bundle = Path(sys._MEIPASS).resolve()
        loaded = Path(library.api._name).resolve()
        result["bundled_usb"] = loaded.is_relative_to(bundle)
        result["bundled_plist"] = Path(plist.api._name).resolve().is_relative_to(bundle)
        result["qt_version"] = QtCore.qVersion()
        result["pillow_version"] = Image.__version__
        import subprocess
        from .voice_backup import tool, process_options
        for name in ("idevicebackup2", "ideviceinfo"):
            process = subprocess.run([tool(name), "--help"], capture_output=True, timeout=20, **process_options())
            if process.returncode:
                raise RuntimeError(f"{name} 無法啟動")
        result["bundled_backup_tools"] = True
        result["ok"] = result["frozen"] and result["bundled_usb"] and result["bundled_plist"]
    except Exception as error:
        result["error"] = str(error)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
