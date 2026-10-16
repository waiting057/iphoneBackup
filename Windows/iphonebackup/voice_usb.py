"""Read-only USB App-folder access, only if iOS explicitly grants it.

No backup, developer mode, jailbreak, shared Mac library, or iCloud fallback.
Reference: libimobiledevice/include/libimobiledevice/house_arrest.h.
"""
import ctypes as c
import ctypes.util
from dataclasses import replace
from pathlib import Path
import sys

from .models import Catalog, MediaItem
from .usb import DeviceError, NativeDependencyError, P, Session

VOICE_EXTENSIONS = {".m4a", ".wav", ".caf", ".aiff", ".aif", ".mp3", ".qta"}
APP_ID = b"com.apple.VoiceMemos"


class AppAccessError(DeviceError):
    pass


class Plist:
    def __init__(self):
        frozen = bool(getattr(sys, "frozen", False))
        directory = Path(sys._MEIPASS) / "native" if frozen else Path(__file__).resolve().parents[1] / "native"
        extension = ".dll" if sys.platform == "win32" else ".dylib"
        candidates = [str(path) for path in directory.glob(f"*plist*{extension}")]
        if not frozen:
            found = ctypes.util.find_library("plist-2.0")
            if found:
                candidates.append(found)
            candidates += ["/opt/homebrew/lib/libplist-2.0.dylib", "/usr/local/lib/libplist-2.0.dylib"]
        for candidate in candidates:
            try:
                self.api = c.CDLL(candidate)
                break
            except OSError:
                pass
        else:
            raise NativeDependencyError("USB App 資料檢查缺少 libplist 元件。")
        self.api.plist_dict_get_item.argtypes = [P, c.c_char_p]
        self.api.plist_dict_get_item.restype = P
        self.api.plist_get_string_ptr.argtypes = [P, c.POINTER(c.c_uint64)]
        self.api.plist_get_string_ptr.restype = c.c_char_p
        self.api.plist_free.argtypes = [P]
        self.api.plist_free.restype = None

    def string(self, result, key):
        item = self.api.plist_dict_get_item(result, key)
        if not item:
            return None
        length = c.c_uint64()
        value = self.api.plist_get_string_ptr(item, c.byref(length))
        return value.decode("utf-8", errors="replace") if value is not None else None


class VoiceAppSession(Session):
    def __init__(self, udid, command="VendDocuments", library=None):
        if command not in ("VendDocuments", "VendContainer"):
            raise ValueError("只允許唯讀 App 目錄請求。")
        self.house = P()
        self.command = command
        super().__init__(udid, library)

    def _connect_filesystem(self):
        bindings = {
            "house_arrest_client_start_service": [P, c.POINTER(P), c.c_char_p],
            "house_arrest_send_command": [P, c.c_char_p, c.c_char_p],
            "house_arrest_get_result": [P, c.POINTER(P)],
            "afc_client_new_from_house_arrest_client": [P, c.POINTER(P)],
            "house_arrest_client_free": [P],
        }
        for name, arguments in bindings.items():
            self.library._bind(name, arguments)
        plist = Plist()
        code = self.api.house_arrest_client_start_service(self.device, c.byref(self.house), b"iPhoneBackUp")
        if code:
            raise AppAccessError(f"USB App 資料服務無法開啟（{code}）。")
        code = self.api.house_arrest_send_command(self.house, self.command.encode(), APP_ID)
        if code:
            raise AppAccessError(f"USB App 目錄請求失敗（{code}）。")
        result = P()
        try:
            code = self.api.house_arrest_get_result(self.house, c.byref(result))
            if code or not result.value:
                raise AppAccessError(f"無法確認 USB App 存取授權（{code}）。")
            error = plist.string(result, b"Error")
            if error:
                raise AppAccessError(f"iPhone 未開放語音備忘錄 App 資料（{error}）。")
            if plist.string(result, b"Status") != "Complete":
                raise AppAccessError("iPhone 未確認語音備忘錄 App 存取成功。")
        finally:
            if result.value:
                plist.api.plist_free(result)
        self._check(self.api.afc_client_new_from_house_arrest_client(self.house, c.byref(self.afc)), "連接 App 唯讀檔案介面")

    def close(self):
        # Header contract: AFC must be freed before its House Arrest owner.
        if self.afc.value:
            self.api.afc_client_free(self.afc)
            self.afc = P()
        if self.house.value:
            self.api.house_arrest_client_free(self.house)
            self.house = P()
        super().close()


def scan_voice_apps(udid, cancel, progress, attempts):
    details = list(attempts)
    for command in ("VendDocuments", "VendContainer"):
        if cancel.is_set():
            raise DeviceError("已取消語音 USB 存取檢查。")
        try:
            session = VoiceAppSession(udid, command)
            with session:
                files = []
                for file in session.walk("/", cancel):
                    if Path(file.path).suffix.lower() in VOICE_EXTENSIONS:
                        files.append(replace(file, source=command))
                        progress(len(files))
                if files:
                    items = tuple(MediaItem(file.path, Path(file.path).name, "voice", (file,)) for file in files)
                    return Catalog(items, ("這是 iPhone 授權的 USB App 目錄中可讀取的錄音檔案，完整語音備忘錄清單、錄製日期與編輯素材仍須核對。",
                        "未建立本機手機備份。", *details), complete=False)
                details.append(f"{command}：USB App 目錄可讀取，但未取得可辨識的錄音檔。")
        except (AppAccessError, NativeDependencyError) as error:
            details.append(f"{command}：{error}")
    return Catalog((), ("目前無法透過 USB 取得語音備忘錄，不能判定手機沒有錄音。", *details,
        "依使用者要求維持 USB-only，不建立本機備份。若 iOS 未開放以上介面，本版無法直接取得錄音。"), availability="unavailable")
