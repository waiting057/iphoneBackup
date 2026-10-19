# Windows USB 元件

請放置符合 Windows 目標 CPU 架構的 `libimobiledevice-1.0.dll` 或 `imobiledevice-1.0.dll`，以及全部相依 DLL。不要放入 macOS dylib。

`licenses/` 需保留隨 App 分發元件的授權文件及必要來源碼取得資訊。建置工具會收集本目錄的 DLL 與授權資訊；還需要 Apple USB 驅動／裝置服務，須在 Windows 上驗證。

目前尚未下載或驗證可分發的 Windows 原生函式庫，也尚未完成打包後的 DLL 載入與 iPhone 連線測試。

使用官方來源建置：[libimobiledevice](https://libimobiledevice.org/)、[官方原始碼](https://github.com/libimobiledevice/libimobiledevice)。


GitHub Actions 另使用 MSYS2 UCRT64 套件及 `.github/ci/windows_native.py`，在忽略的 `Windows/native-ci/` 準備工具、DLL 相依與授權，再以 `--native-dir Windows/native-ci` 傳給既有 build。設定已建立但尚未跑完 Windows 雲端建置；首次執行仍需確認，不能將本機演算法測試視為 Windows 實機驗收。詳見根目錄 README 第 17 節。
