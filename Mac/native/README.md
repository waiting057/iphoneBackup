# 原生 USB 元件

開發時可使用系統安裝的 libimobiledevice。Mac 建置預設會從已安裝的 Homebrew 套件遞迴收集 dylib 與授權，不需手動放置。若本目錄已有 dylib 或使用 `--native-dir` 自備元件，則須提供符合目標 OS、CPU 架構的全部函式庫及授權。

- macOS：`libimobiledevice-1.0.dylib` 與全部相依 `.dylib`。
- Windows：`libimobiledevice-1.0.dll` 或 `imobiledevice-1.0.dll` 與全部相依 `.dll`，並需實機驗證 Apple USB 驅動。
- `licenses/`：所有隨 App 發布元件的授權文件及必要來源碼取得資訊。

不要混用不同 CPU 架構，不要把 Windows DLL 放進 Mac 版本。
建置工具會收集這個目錄的動態函式庫。macOS 的載入路徑、Windows 的依賴及打包後連線仍須各自實測；有 DLL 或 dylib 不代表已通過驗收。

來源與 API：[libimobiledevice](https://libimobiledevice.org/)、[官方 AFC 標頭](https://github.com/libimobiledevice/libimobiledevice/blob/master/include/libimobiledevice/afc.h)。
