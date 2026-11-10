@echo off
REM ==== MultiRDP build script: package the web UI + tray app into one exe ====
REM Output: dist\MultiRDP.exe (tray resident) + dist\FreeRDP (client, copied automatically)
REM Runtime data (users.json, config.json) is created next to the exe on first run.
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

echo [2/3] Building dist\MultiRDP.exe ...
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name MultiRDP src\tray_app.py
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed.
    pause & exit /b 1
)

echo [3/3] Copying FreeRDP client to dist\FreeRDP ...
if not exist "3rd\FreeRDP\sdl3-freerdp.exe" (
    echo [WARN] 3rd\FreeRDP\sdl3-freerdp.exe not found - skipping client copy.
    goto done
)
xcopy "3rd\FreeRDP" "dist\FreeRDP" /E /I /Y /Q >nul
if errorlevel 1 (
    echo [FAIL] copying 3rd\FreeRDP to dist\FreeRDP failed.
    pause & exit /b 1
)

:done
echo.
echo ==== BUILD OK ====
echo Output : dist\MultiRDP.exe
echo Client : dist\FreeRDP\   (sdl3-freerdp.exe / wfreerdp.exe)
echo.
echo Next: double-click dist\MultiRDP.exe  (asks for admin rights, then stays in tray)
echo       tray menu -^> Open Web UI
pause
