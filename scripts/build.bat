@echo off
REM ==== MultiRDP build script: package the web UI + tray app into one exe ====
REM Output: dist\MultiRDP.exe (tray resident; menu: Open Web UI / Settings / Exit)
REM Runtime data (users.json, config.json) and the FreeRDP client live next to the exe.
REM NOTE: keep this file pure ASCII + CRLF. cmd.exe misparses UTF-8 Chinese text.
cd /d "%~dp0.."
REM UTF-8 mode: lets pip/pyinstaller read UTF-8 files on a GBK console
set PYTHONUTF8=1

where python >nul 2>&1
if errorlevel 1 (
    echo [FAIL] python not found in PATH. Install Python 3.x first.
    pause & exit /b 1
)

echo [1/2] Installing dependencies (pystray / Pillow / pyinstaller)...
python -m pip install --quiet -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [FAIL] pip install failed.
    pause & exit /b 1
)

echo [2/2] Building dist\MultiRDP.exe ...
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name MultiRDP src\tray_app.py
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed.
    pause & exit /b 1
)

echo.
echo ==== BUILD OK ====
echo Output: dist\MultiRDP.exe
echo.
echo First run:
echo   1. copy the repo folder 3rd\FreeRDP  to  dist\FreeRDP  (FreeRDP client)
echo   2. double-click dist\MultiRDP.exe  (it asks for admin rights, then stays in tray)
echo   3. tray menu: Open Web UI
pause
