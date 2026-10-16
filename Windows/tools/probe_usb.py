"""Read-only real-device diagnostic, no backup and no media transfer."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iphonebackup.usb import Library, Session


def main():
    report = {"mode": "real USB read-only probe", "complete_library_verified": False, "devices": []}
    try:
        library = Library()
        for position, udid in enumerate(library.devices()):
            device = {"index": position + 1, "roots": {}}
            try:
                with Session(udid, library) as session:
                    for path in ("/DCIM", "/Recordings"):
                        try:
                            device["roots"][path] = {"readable": True, "entry_count": len(session.listdir(path))}
                        except Exception as exc:
                            device["roots"][path] = {"readable": False, "error": str(exc)}
            except Exception as exc:
                device["error"] = str(exc)
            report["devices"].append(device)
    except Exception as exc:
        report["error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if "error" in report or not report["devices"] or any("error" in device for device in report["devices"]):
        return 1
    return 0 if all(root["readable"] for device in report["devices"] for root in device["roots"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
