@echo off
setlocal
cd /d "%~dp0"
if exist "Windows\.venv\Scripts\python.exe" (
  "Windows\.venv\Scripts\python.exe" build.py %*
) else (
  py build.py %*
)
exit /b %errorlevel%
