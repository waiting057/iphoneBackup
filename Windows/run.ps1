$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "請先依 Windows/README.md 建立虛擬環境並安裝依賴。"
}
& ".venv\Scripts\python.exe" "main.py"
exit $LASTEXITCODE
