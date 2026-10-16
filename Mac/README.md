# macOS 開發版

## 語音 USB 存取（2026-10-16）

使用者已確認只接受 USB 直接讀取，不建立本機手機備份。先嘗試 `/Recordings`；若不存在、拒絕或沒有可辨識錄音，再向 iOS 的 App 檔案服務請求 `com.apple.VoiceMemos` 的 `VendDocuments`、`VendContainer` 唯讀目錄存取。這是兩條需實機驗證的 USB 存取路徑，不保證 iOS 會開放，也不保證可讀取目錄涵蓋共享容器中的所有錄音。

程式不啟用開發者模式、不越獄、不改權限、不建立備份、不改讀 Mac 同步資料。若 iOS 回覆 `ApplicationLookupFailed`、`PermissionDenied` 等拒絕結果，畫面顯示「語音存取受限」，總數／容量保持未知，詳細回覆可在「讀取提示」查看。資料夾為空也不能當作手機沒有錄音。

如成功讀取，匯出會使用同一個 USB 來源重新開啟錄音，並將來源記錄在 ZIP 清單；錄製日期、使用者命名、編輯素材與完整數量仍須實機核對。API 依據：[libimobiledevice House Arrest 標頭](https://github.com/libimobiledevice/libimobiledevice/blob/master/include/libimobiledevice/house_arrest.h)。

## 清單空間更新（2026-10-16）

數量、容量與選取統計移至左側，連線與匯出區改為精簡排列，完整讀取訊息可按「讀取提示」查看。卡片高度依照片清單可用空間及月份標題調整。以足量測試項目確認 1000×760、1200×850、1440×900 視窗均可完整顯示至少 5 排，包含跨月份清單；保留捲動及個別選取。重新啟動 `run.command` 套用。

## 介面更新（2026-10-15）

已更新深色側邊選單、淺色媒體工作區、USB 連線區、數量／容量／選取統計卡片、月份檔案卡片及固定匯出區。尚未讀取時顯示操作引導，未知總數與容量以「—」表示，工作進行時才顯示進度及取消。縮小視窗時會簡化空畫面插圖，保留可讀文字。重新啟動 `run.command` 即可套用。

介面已使用離屏渲染檢查空清單、測試檔案清單、語音頁面及較小視窗；測試資料僅用於檢查，沒有加入 App 的正式裝置流程。這次更新不代表手機內容完整性已驗證。

## 目前狀態

這是開發版，已可封裝獨立 `.app`，尚未完成所有功能驗收及正式發布簽章。包含 PySide6 桌面介面、唯讀 USB AFC 存取、檔案清單、選取、月份分組、背景工作與 ZIP64 整批匯出。沒有模擬裝置模式，也不會建立手機備份或刪除手機資料。

媒體檔案使用 USB `/DCIM` 與 `/Recordings` 存取；另外嘗試唯讀取得 `/PhotoData/Photos.sqlite` 及其 WAL 日誌以補讀照片日期。這些資料不等於已驗證的完整照片／語音備忘錄資料庫。清單數量標示為「檔案項目」；Live Photo 配對、編輯結果、錄音名稱／日期／音層、iCloud 內容及完整資產清單尚未完成。缺少錄音目錄會顯示無法存取，不能解讀為零筆錄音。

照片預覽會讀取完整檔案到私有暫存，逐張縮圖後清除暫存，不再只解碼前 256 KB。Mac 以系統 ImageIO 支援 HEIC／JPEG／PNG／TIFF 等格式，Pillow 作補充；不需要另外安裝 HEIC 套件。不套用舊版 3,000 萬像素的預覽限制，縮圖會處理圖片方向，原檔匯出不受縮圖影響。這會比只讀檔案開頭傳輸更多資料，仍在背景進行並可取消。影片畫格預覽尚未實作。

日期先讀原始 EXIF（含主目錄與 Exif 子目錄）與 XMP 拍攝／建立日期；不足時，若 USB 權限允許，從相片資料庫以完整目錄＋檔名配對補讀。資料庫複製含日誌且核對來源未變動，僅查詢本機暫存副本，不修改手機。相片資料庫可取得的日期也可供影片分組；無法取得時仍是日期未知。資料庫格式與可見性隨 iOS 不同，無法保證皆可取得。

EXIF／XMP 保留記錄的年月日，不轉換成電腦時區。資料庫提供時區偏移時保留拍攝時區，否則明確標示 UTC；這種情況的跨月邊界可能與手機顯示不同。沒有可信日期才列為「日期未知」，不使用檔案修改時間或檔名猜測。上方提示會顯示取得日期與解碼失敗數量，卡片懸停提示可查看來源。來源是否符合手機拍攝原始素材仍需實機驗證。

照片解碼參考：[Apple ImageIO](https://developer.apple.com/documentation/imageio)、[Pillow EXIF](https://pillow.readthedocs.io/en/stable/reference/Image.html)、[pillow-heif](https://pillow-heif.readthedocs.io/en/stable/pillow-plugin.html)。相片資料庫欄位參考：[iOS Photos.sqlite 查詢研究](https://github.com/AndrewRathbun/iOS_Photos.sqlite_Queries)。

## 啟動

在 `Mac/` 目錄執行，建議使用 Python 3.12 以上：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
brew install libimobiledevice
.venv/bin/python main.py
```

或執行 `run.command`。用 USB 連接 iPhone、解鎖、在手機上信任電腦，再按「讀取手機內容」。App 僅使用 USB，不使用無線讀取。多個 USB 裝置時可從下拉選單指定裝置。

### 出現「No module named PySide6」

代表 `.venv` 已存在，但套件尚未安裝完成。請在電腦的終端機執行：

```sh
cd /Users/mikelin/Documents/workspace/iphoneBackUp/Mac
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements.txt
```

安裝成功後重新執行 `run.command`。啟動程式會檢查 PySide6、Pillow 是否可載入；缺少套件時顯示使用的 Python 與修復指令，雙擊啟動失敗時會保留視窗直到按 Enter。libimobiledevice 是後續讀取手機所需的另一個元件，缺少時會在 App 內提示。

唯讀診斷不需要 PySide6：

```sh
python3 tools/probe_usb.py
```

診斷只回報可否列出目錄，不輸出錄音、照片內容或完整裝置識別碼。成功列出目錄不代表完整清單已通過驗證。

## 匯出行為

- 凍結此次選取清單，每個項目的已辨識必要檔案均須成功。
- ZIP64、不額外壓縮，按已取得的月份或「日期未知」放置檔案。
- 保留檔名；不適合跨平台的字元會替換，重名加 `__2` 等後綴，不覆蓋檔案。
- ZIP 含 `export-manifest.json`，保存來源路徑、日期、容量、SHA-256 及名稱對應。
- 寫入後重新讀取 ZIP，核對清單、容量與雜湊，完成才以不覆蓋方式發布。
- 任何讀取、寫入、驗證錯誤或取消均失敗，清除本次 `.partial`；清除失敗會顯示路徑。
- 記錄曾使用的輸出資料夾，啟動及選定輸出位置時偵測異常退出遺留的 `.partial`，提醒它不是成功成品，不自動刪除其他工作的檔案；未連接的外接磁碟待下次檢查。此啟動流程尚待介面實測。
- 檢查選取檔案容量與 ZIP／清單預留空間，尚未完成 FAT32 等檔案系統上限的事前辨識；寫入或發布失敗仍視為整批失敗。

## 測試與建置

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python build.py
```

也可直接在專案根目錄執行 `./build`；產物為 `output/Mac/iPhoneBackUp.app`。預設自動收集 Homebrew 已安裝的 libimobiledevice、所有非系統依賴與授權檔，不需手動複製。自備元件可用 `./build --native-dir /path/to/native`；仍須附 `licenses/`。

建置使用目前 `.venv` 的 Python／Qt 與 CPU 架構，這台 Mac 產出 Apple Silicon arm64 版本。封裝後會實際啟動無介面的依賴檢查，確認從 App 內載入 USB、plist、Qt、Pillow 元件，並驗證本機 ad-hoc 簽章。檢查不會讀手機，也不代表完成 USB 功能驗收。使用者執行 `.app` 不需要另裝 Python 或 Homebrew，但正式跨電腦分發的最低 macOS 版本、Developer ID 簽章、公證與實機相容性仍待完成。

每次建置的資訊在 `output/Mac/BUILD-INFO.json`。若原本 App 無法搬移（例如執行環境限制），保留原檔並發布含 build ID 的新版檔名；請以 BUILD-INFO 的 `artifact` 欄位確認這次的成品。過程不會刪除其他平台產物。歷史 App 位於 `Mac/build/previous/`，建置中間檔在 `Mac/build/`。

## 本次環境驗證

2026-10-14：使用者已成功啟動介面；本機 23 項核心與介面模型測試通過，並完成離屏畫面檢查。已固定完整淺色調色盤，避免系統深色模式造成白字配淺色背景。libimobiledevice 仍未安裝，App 打包及實體 iPhone 驗證尚未完成。

2026-10-15：使用者回報可列出 599 個檔案，但大部分預覽失敗且日期未知。已修正完整檔案解碼、格式支援與日期來源。系統 ImageIO 的 JPEG 解碼／EXIF、PNG／大 JPG／方向／資料庫日期等均有測試；系統 HEIC 測試檔編碼因環境限制而跳過，HEIC 實際照片及手機資料庫可存取性仍待使用者重讀確認。Windows 原生執行尚未驗證。


### 語音備忘錄的暫存備份

語音頁按「從暫存備份讀取錄音」，選擇暫存位置與容量上限，再按開始。無法只備份錄音，可能需要大量空間。備份讀取後選取錄音再匯出，對應附屬素材自動包含；成功驗證、失敗／取消、離開清單與正常關閉時清除本次暫存。若異常退出或清除失敗，可按「清除殘留備份」重試，只清理 App 專屬標記的資料夾。

目前不支援加密備份解密，會在備份前檢查並停止，不改手機加密設定。備份涵蓋全部錄音與編輯素材仍需實機核對。建置需要 idevicebackup2、ideviceinfo（Windows 為 .exe）及依賴與授權；Mac 使用已安裝 libimobiledevice 的工具，Windows 請放入 native/。


語音清單依資料庫排除帶移除標記的項目，一張卡片代表一筆錄音，顯示實際名稱、時長與月份。波形／組成／明確引用的原始素材一併匯出，不另列卡片；所選錄音資料庫欄位附在 ZIP 的 export-manifest.json。

可按「讀取現有備份」沿用已完成的備份；此入口為唯讀借用來源，不會自動清除此來源。若備份由另一個開啟的 App 視窗建立，請在匯出前保留原視窗，避免其清理來源。自建暫存備份仍沿用匯出後自動清除規則。
