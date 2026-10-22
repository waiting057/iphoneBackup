"""Explicit, read-only Windows connection checks; never change services/drivers."""
import os
from pathlib import Path
import socket
import subprocess
import sys

from .usb import Library


def decode_output(data):
    try:
        return data.decode("oem", errors="replace")
    except LookupError:  # Allows diagnostic fixtures on non-Windows test hosts.
        return data.decode("utf-8", errors="replace")


def windows_report():
    lines = ["iPhoneBackUp Windows 連線診斷", "不會建立備份或更改驅動、服務與手機設定。"]
    if sys.platform != "win32":
        return "此診斷僅適用於 Windows。"
    # Absolute system executable; do not run an sc.exe from the current folder.
    sc = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "sc.exe"
    try:
        result = subprocess.run([str(sc), "query", "Apple Mobile Device Service"],
                                capture_output=True, timeout=8,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 1060:
            lines.append("Apple Mobile Device Service：未找到這個服務名稱（不代表所有 Apple 裝置元件均未安裝）。")
        else:
            # Keep the numeric state and original localized output for support.
            lines.append(f"Apple Mobile Device Service 查詢：exit {result.returncode}")
        lines.append(decode_output(result.stdout + result.stderr).strip())
    except (OSError, subprocess.TimeoutExpired) as error:
        lines.append(f"Apple 服務查詢未完成：{error}")
    if os.environ.get("USBMUXD_SOCKET_ADDRESS"):
        lines.append("偵測到 USBMUXD_SOCKET_ADDRESS 自訂設定；以下 27015 測試不能代表自訂端點。")
    try:
        with socket.create_connection(("127.0.0.1", 27015), timeout=2):
            lines.append("本機 127.0.0.1:27015：可連線（僅表示連接埠開啟，尚未確認 Apple 協定或手機）。")
    except OSError as error:
        lines.append(f"本機 127.0.0.1:27015：無法連線，{error}")
    try:
        devices = Library().devices()
        lines.append(f"內附 USB 元件列舉：{len(devices)} 台 USB 裝置（不顯示手機識別碼）。")
    except Exception as error:
        lines.append(f"內附 USB 元件列舉失敗：{error}")
    lines += ["", "請一併回報：iTunes／Apple 裝置 App 能否顯示這支 iPhone 的資訊、Windows 與 iOS 版本。",
              "若 Apple App 也無法辨識，請先檢查 Apple 裝置服務與 USB 驅動。",
              "若 Apple App 可辨識，請保留此結果以檢查本 App 的元件相容性。"]
    return "\n".join(lines)
