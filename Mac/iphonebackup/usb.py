"""Small ctypes binding to documented libimobiledevice APIs.

Only USB connections; no filesystem mutations, backup, unpair, or encryption APIs.
Native handles belong to the worker opening the session and never cross threads.
"""
import ctypes as c
import ctypes.util
import os
import sys
from pathlib import Path, PurePosixPath
from threading import Event

from .models import DeviceFile


class DeviceError(Exception):
    pass


class NativeDependencyError(DeviceError):
    pass


class AFCError(DeviceError):
    def __init__(self, operation: str, code: int):
        self.code = code
        reason = {5: "裝置讀取失敗", 8: "裝置未提供此檔案或目錄", 10: "裝置拒絕存取權限",
                  11: "檔案服務已中斷", 12: "等待裝置回應逾時", 15: "裝置不支援此操作",
                  30: "USB 連線服務錯誤"}.get(code, "裝置檔案服務錯誤")
        super().__init__(f"{operation}失敗（AFC {code}：{reason}）。")


P = c.c_void_p
STRINGS = c.POINTER(c.c_char_p)


class Library:
    def __init__(self):
        native_dir = Path(__file__).resolve().parent.parent / "native"
        if getattr(sys, "frozen", False):
            native_dir = Path(sys._MEIPASS) / "native"
        self._dll_directory = None
        if sys.platform == "win32" and native_dir.exists():
            self._dll_directory = os.add_dll_directory(str(native_dir))
        names = (["libimobiledevice-1.0.dll", "imobiledevice-1.0.dll"] if sys.platform == "win32"
                 else ["libimobiledevice-1.0.dylib"] if sys.platform == "darwin"
                 else ["libimobiledevice-1.0.so"])
        candidates = [str(native_dir / name) for name in names]
        if not getattr(sys, "frozen", False):
            found = ctypes.util.find_library("imobiledevice-1.0")
            if found:
                candidates.append(found)
            candidates += names
            if sys.platform == "darwin":
                candidates += [f"{root}/{name}" for root in ("/opt/homebrew/lib", "/usr/local/lib") for name in names]
        load_errors = []
        for candidate in candidates:
            try:
                self.api = c.CDLL(candidate)
                break
            except OSError as error:
                load_errors.append(f"{Path(candidate).name}: {error}")
        else:
            raise NativeDependencyError("iPhone USB 元件載入失敗；目前尚未讀取手機內容。\n" + "\n".join(load_errors))
        self._bind("idevice_get_device_list", [c.POINTER(STRINGS), c.POINTER(c.c_int)])
        self._bind("idevice_device_list_free", [STRINGS])
        self._bind("idevice_new_with_options", [c.POINTER(P), c.c_char_p, c.c_int])
        self._bind("idevice_free", [P])
        self._bind("lockdownd_client_new_with_handshake", [P, c.POINTER(P), c.c_char_p])
        self._bind("lockdownd_client_free", [P])
        self._bind("lockdownd_start_service", [P, c.c_char_p, c.POINTER(P)])
        self._bind("lockdownd_service_descriptor_free", [P])
        self._bind("afc_client_new", [P, P, c.POINTER(P)])
        self._bind("afc_client_free", [P])
        self._bind("afc_read_directory", [P, c.c_char_p, c.POINTER(STRINGS)])
        self._bind("afc_get_file_info", [P, c.c_char_p, c.POINTER(STRINGS)])
        self._bind("afc_dictionary_free", [STRINGS])
        self._bind("afc_file_open", [P, c.c_char_p, c.c_int, c.POINTER(c.c_uint64)])
        self._bind("afc_file_read", [P, c.c_uint64, P, c.c_uint32, c.POINTER(c.c_uint32)])
        self._bind("afc_file_close", [P, c.c_uint64])

    def _bind(self, name, args):
        try:
            fn = getattr(self.api, name)
        except AttributeError as exc:
            raise NativeDependencyError(f"USB 元件版本不相容：缺少 {name}") from exc
        fn.argtypes = args
        fn.restype = c.c_int

    def devices(self) -> tuple[str, ...]:
        pointers = STRINGS()
        count = c.c_int()
        code = self.api.idevice_get_device_list(c.byref(pointers), c.byref(count))
        try:
            if code:
                raise DeviceError(f"USB 裝置偵測失敗（{code}）。")
            return tuple(pointers[i].decode("utf-8") for i in range(count.value))
        finally:
            if pointers:
                self.api.idevice_device_list_free(pointers)


class Session:
    def __init__(self, udid: str, library: Library | None = None):
        self.library = library or Library()
        self.api = self.library.api
        self.udid = udid
        self.device = P()
        self.lockdown = P()
        self.service = P()
        self.afc = P()

    def __enter__(self):
        try:
            # IDEVICE_LOOKUP_USBMUX = 1 << 1. Never use the network flag.
            code = self.api.idevice_new_with_options(c.byref(self.device), self.udid.encode(), 2)
            if code:
                raise DeviceError(f"USB 裝置已中斷或無法開啟（{code}）。")
            code = self.api.lockdownd_client_new_with_handshake(self.device, c.byref(self.lockdown), b"iPhoneBackUp")
            if code:
                raise DeviceError(f"等待手機解鎖／信任電腦，或授權失敗（lockdown {code}）。")
            self._connect_filesystem()
            return self
        except BaseException:
            self.close()
            raise

    def _connect_filesystem(self):
        code = self.api.lockdownd_start_service(self.lockdown, b"com.apple.afc", c.byref(self.service))
        if code:
            raise DeviceError(f"手機拒絕開啟檔案讀取服務（lockdown {code}）。")
        self._check(self.api.afc_client_new(self.device, self.service, c.byref(self.afc)), "連接檔案服務")

    def __exit__(self, *_):
        self.close()

    def close(self):
        for attr, free in (("afc", "afc_client_free"), ("service", "lockdownd_service_descriptor_free"),
                           ("lockdown", "lockdownd_client_free"), ("device", "idevice_free")):
            pointer = getattr(self, attr)
            if pointer.value:
                getattr(self.api, free)(pointer)
                setattr(self, attr, P())

    @staticmethod
    def _check(code: int, operation: str):
        if code:
            raise AFCError(operation, code)

    def _strings(self, operation, path):
        result = STRINGS()
        code = getattr(self.api, operation)(self.afc, path.encode("utf-8"), c.byref(result))
        try:
            self._check(code, f"讀取 {path}")
            values = []
            if result:
                index = 0
                while result[index] is not None:
                    values.append(result[index].decode("utf-8"))
                    index += 1
            return values
        finally:
            if result:
                self.api.afc_dictionary_free(result)

    def listdir(self, path: str):
        return [entry for entry in self._strings("afc_read_directory", path) if entry not in (".", "..")]

    def stat(self, path: str):
        values = self._strings("afc_get_file_info", path)
        if len(values) % 2:
            raise DeviceError(f"裝置回傳不完整的檔案資訊：{path}")
        return dict(zip(values[::2], values[1::2]))

    def walk(self, root: str, cancel: Event):
        pending = [root]
        while pending:
            if cancel.is_set():
                raise DeviceError("已取消讀取清單。")
            folder = pending.pop()
            for name in self.listdir(folder):
                if cancel.is_set():
                    raise DeviceError("已取消讀取清單。")
                if "/" in name or "\\" in name or "\x00" in name:
                    raise DeviceError("裝置回傳無效檔案名稱。")
                path = str(PurePosixPath(folder) / name)
                metadata = self.stat(path)
                if metadata.get("st_ifmt") == "S_IFDIR":
                    pending.append(path)
                elif metadata.get("st_ifmt") == "S_IFREG":
                    if "st_size" not in metadata:
                        raise DeviceError(f"無法取得檔案容量：{path}")
                    yield DeviceFile(path, int(metadata["st_size"]), metadata.get("st_mtime"))
                # Never follow symlinks into unknown device folders.

    def chunks(self, file: DeviceFile):
        before = self.stat(file.path)
        if int(before.get("st_size", -1)) != file.size or (file.modified_ns is not None and before.get("st_mtime") != file.modified_ns):
            raise DeviceError(f"檔案在清單讀取後已改變，請重新讀取清單：{file.path}")
        yield from self._read(file.path)
        after = self.stat(file.path)
        if any(before.get(key) != after.get(key) for key in ("st_size", "st_mtime", "st_ifmt")):
            raise DeviceError(f"匯出途中來源檔案已改變：{file.path}")

    def _read(self, path: str, limit: int | None = None):
        handle = c.c_uint64()
        # AFC_FOPEN_RDONLY = 1; never bind any write/remove API.
        self._check(self.api.afc_file_open(self.afc, path.encode(), 1, c.byref(handle)), f"開啟 {path}")
        failed = False
        try:
            buffer = c.create_string_buffer(1024 * 1024)
            count = c.c_uint32()
            consumed = 0
            while limit is None or consumed < limit:
                size = len(buffer) if limit is None else min(len(buffer), limit - consumed)
                count.value = 0
                code = self.api.afc_file_read(self.afc, handle, buffer, size, c.byref(count))
                if code == 14 and not count.value:  # AFC_E_END_OF_DATA
                    break
                self._check(code, f"讀取 {path}")
                if count.value > size:
                    raise DeviceError("裝置回傳的讀取容量無效。")
                if not count.value:
                    break
                consumed += count.value
                yield buffer.raw[:count.value]
        except BaseException:
            failed = True
            raise
        finally:
            code = self.api.afc_file_close(self.afc, handle)
            if not failed:
                self._check(code, f"關閉 {path}")

    def prefix(self, path: str, limit=256 * 1024):
        return b"".join(self._read(path, limit))
