# Windows 開發版

## Windows 連線診斷（2026-10-20）

已收到實測回報：iTunes 能顯示 iPhone，但此 App 無法偵測；根因尚未確認。Windows 版新增「連線診斷」，檢查 Apple Mobile Device Service 的查詢結果、本機預設 USB 服務連接埠及內附元件的 USB 裝置列舉。結果可按「複製結果」回報，並附上 Windows／iOS 版本與 iTunes 是否能顯示手機資訊。元件載入失敗會保留原始錯誤；主畫面訊息可懸停查看完整內容。

診斷不會重啟服務、變更驅動或建立備份；連接埠開啟不代表手機授權成功，自訂 USBMUXD_SOCKET_ADDRESS 存在時會標明預設連接埠檢查的限制。只有元件測試與雲端打包成功，不能代替 Windows 手機連線實測。需 push 後重新執行 Build Apps，下載新產物才包含此功能。

若 iTunes 本身也無法辨識，參考 [Apple 裝置辨識說明](https://support.apple.com/zh-tw/108643) 與 [AMDS 服務說明](https://support.apple.com/zh-tw/102347)。若 iTunes 已可辨識，先回報診斷結果，避免在根因不明時重設手機信任或重裝驅動。

2026-10-16：同步擴大媒體清單的版面，統計移到左側，連線與匯出區精簡，卡片以至少 5 排為目標調整高度。Mac 離屏環境已驗證三種視窗大小及跨月份排列；Windows 顯示縮放仍需實機確認。

## 介面更新（2026-10-15）

已同步新版側邊選單、連線區、統計卡片、月份卡片、空清單引導與固定匯出區。視覺檢查在 Mac 離屏環境完成，Windows 的字型、視窗縮放與操作仍需實機檢查。

## 目前狀態

語音目前只採 USB：先嘗試 `/Recordings`，再檢查 iOS 是否允許語音備忘錄的 App 目錄存取。使用者已拒絕建立本機手機備份；如果 iOS 未開放，App 會明確顯示限制，不會擅自切換來源。兩種 App USB 請求及 Windows 原生元件仍須實機驗證。

包含與 Mac 對應的 Python／PySide6 桌面介面、唯讀 USB 存取與 ZIP64 匯出程式。尚未在 Windows 執行，尚未產出 `.exe` 或安裝檔。Mac 上的程式與測試結果不能代替 Windows 實測。

目前僅嘗試 USB `/DCIM`、`/Recordings` 檔案存取，尚未完成完整照片／影片／語音備忘錄清單、Live Photo 資產配對及編輯素材驗證。讀不到錄音目錄會明確顯示無法存取，不能視為手機沒有錄音。缺日期時放「日期未知」，清單數量標示為檔案項目。

照片預覽現以完整檔案產生縮圖，支援 JPEG、PNG 等 Pillow 格式，Windows HEIC 使用 `pillow-heif`（已加入 `requirements.txt`）。既有環境請重新執行依賴安裝指令。缺少 HEIC 解碼元件時卡片懸停提示會說明，不影響原檔選取與匯出。

日期補讀 EXIF 主／子目錄、XMP，以及 USB 可存取時的 `/PhotoData/Photos.sqlite` 相片資料庫；資料庫採穩定副本與日誌在本機唯讀查詢，不建立整機備份。無可信日期仍標示未知；UTC 日期來源會明確說明。影片只有在可配對到資料庫日期時才能補上月份，畫格預覽尚未實作。詳見 Mac README 的日期語意與限制；Windows 實機仍待測試。

## 啟動

在 `Windows/` 的 PowerShell 執行，建議 Python 3.12 以上：

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

也可執行 `run.ps1`。另外需要：

1. 相容的 Apple iPhone USB 驅動與裝置服務；須在實際 Windows 環境核對安裝方式。
2. 將相同 CPU 架構的 libimobiledevice DLL、全部相依 DLL 及授權資訊放進 `native/`，見 `native/README.md`。
3. 透過 USB 連接 iPhone，在手機上解鎖並信任電腦。

此程式不會下載未驗證來源的 DLL、不會自動建立手機備份、不會刪除或修改手機內容。

## 唯讀診斷、測試與建置

```powershell
py tools\probe_usb.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe build.py
```

在專案根目錄執行 `.\build.cmd`，會使用 `Windows/.venv` 打包，產出 `output/Windows/iPhoneBackUp.exe` 單一可執行檔。打包的電腦需要 Python、requirements 套件及 `Windows/native/` 的原生 DLL／完整依賴／授權文件；可用 `.\build.cmd --native-dir C:\path\to\native` 指定其他目錄。建置後會檢查內附 Qt、Pillow、USB／plist 元件能載入，通過才發布。

使用者端不需安裝 Python，但仍需相容的 Apple USB 驅動／裝置服務。單檔 exe 啟動時會把套件解開到臨時目錄，首次啟動可能稍慢；原生元件不完整或權限受限仍可能無法啟動。建置資訊在 `output/Windows/BUILD-INFO.json`。Windows 尚未實際建置／執行驗證，驅動、DLL、支援系統與簽章仍待完成。

## 匯出與共用程式

匯出採 ZIP64、不額外壓縮、月份資料夾、跨平台安全檔名與重名後綴；附帶來源與 SHA-256 清單。全部所選可讀取檔案寫入並重新驗證後才產生成品，任何讀取／寫入／驗證失敗或取消均整批失敗，清除本次暫存；同名成功 ZIP 不會被覆蓋。

已加入曾使用輸出資料夾的啟動暫存檔檢查，尚待介面實測。目前未完成 FAT32 等檔案系統上限的事前判斷。尚未驗證 USB 提供的檔案是否涵蓋全部拍攝原始素材及編輯內容。

共用 Python 程式在 `Mac/` 維護，以 `python Mac/tools/sync_windows.py` 同步到本目錄；`--check` 可驗證一致性。平台啟動腳本、README、原生函式庫與建置產物分別維護。


### 語音備忘錄的暫存備份

語音頁按「從暫存備份讀取錄音」，選擇暫存位置與容量上限，再按開始。無法只備份錄音，可能需要大量空間。備份讀取後選取錄音再匯出，對應附屬素材自動包含；成功驗證、失敗／取消、離開清單與正常關閉時清除本次暫存。若異常退出或清除失敗，可按「清除殘留備份」重試，只清理 App 專屬標記的資料夾。

目前不支援加密備份解密，會在備份前檢查並停止，不改手機加密設定。備份涵蓋全部錄音與編輯素材仍需實機核對。建置需要 idevicebackup2、ideviceinfo（Windows 為 .exe）及依賴與授權；Mac 使用已安裝 libimobiledevice 的工具，Windows 請放入 native/。


語音清單依資料庫排除帶移除標記的項目，一張卡片代表一筆錄音，顯示實際名稱、時長與月份。波形／組成／明確引用的原始素材一併匯出，不另列卡片；所選錄音資料庫欄位附在 ZIP 的 export-manifest.json。

可按「讀取現有備份」沿用已完成的備份；此入口為唯讀借用來源，不會自動清除此來源。若備份由另一個開啟的 App 視窗建立，請在匯出前保留原視窗，避免其清理來源。自建暫存備份仍沿用匯出後自動清除規則。
