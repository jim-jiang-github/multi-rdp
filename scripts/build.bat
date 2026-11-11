@echo off
REM ==== MultiRDP build script: package the web UI + tray app into one exe ====
REM Output: dist\MultiRDP.exe (tray resident).
REM The remote client is mstsc.exe, which ships with Windows, so nothing has to
REM be copied next to the exe any more (FreeRDP was removed).
REM The build is driven by MultiRDP.spec (icon + bundled tray.png live there).
REM PyInstaller works in .build (auto-removed when the build succeeds).
REM Runtime data (users.json, config.json, aliases.json) is created next to the exe.
REM NOTE: keep this file pure ASCII + CRLF. cmd.exe misparses UTF-8 Chinese text.
cd /d "%~dp0.."
REM UTF-8 mode: lets pip/pyinstaller read UTF-8 files on a GBK console
set PYTHONUTF8=1

where python >nul 2>&1
if errorlevel 1 (
    echo [FAIL] python not found in PATH. Install Python 3.x first.
    pause & exit /b 1
)

echo [1/3] Installing dependencies (pystray / Pillow / pyinstaller)...
python -m pip install --quiet -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [FAIL] pip install failed.
    pause & exit /b 1
)

REM Icon is generated from scripts\make_icon.py so the sources of the artwork
REM stay versioned (assets\multirdp.ico is the build product).
echo [2/3] Generating app icon (assets\multirdp.ico / assets\tray.png)...
python scripts\make_icon.py
if errorlevel 1 (
    echo [FAIL] icon generation failed.
    pause & exit /b 1
)

REM A running MultiRDP.exe locks dist\MultiRDP.exe and PyInstaller cannot
REM overwrite it - warn early instead of failing halfway through.
tasklist /fi "imagename eq MultiRDP.exe" 2>nul | find /i "MultiRDP.exe" >nul
if not errorlevel 1 (
    echo [WARN] MultiRDP.exe is running - exit it from the tray first, or the build may fail.
)

echo [3/3] Building dist\MultiRDP.exe ...
python -m PyInstaller --noconfirm --clean --workpath .build MultiRDP.spec
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed.
    pause & exit /b 1
)

REM PyInstaller work dir is temporary - remove it after a successful build
if exist ".build" rd /s /q ".build"
echo.
echo ==== BUILD OK ====
echo Output : dist\MultiRDP.exe
echo Client : %%SystemRoot%%\System32\mstsc.exe  (part of Windows, nothing to copy)
echo.
echo Next: double-click dist\MultiRDP.exe  (asks for admin rights, then stays in tray)
echo       tray menu -^> Open Web UI
pause
