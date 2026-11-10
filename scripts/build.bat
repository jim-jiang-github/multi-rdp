@echo off
REM ==== MultiRDP build script: package the web UI + tray app into one exe ====
REM Output: dist\MultiRDP.exe (tray resident) + dist\FreeRDP (client, copied automatically)
REM PyInstaller works in .build (auto-removed when the build succeeds).
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

REM A running MultiRDP.exe locks dist\MultiRDP.exe and a running remote window locks
REM dist\FreeRDP\sdl3-freerdp.exe. Warn early instead of failing halfway through.
tasklist /fi "imagename eq MultiRDP.exe" 2>nul | find /i "MultiRDP.exe" >nul
if not errorlevel 1 (
    echo [WARN] MultiRDP.exe is running - exit it from the tray first, or the build may fail.
)

echo [2/3] Building dist\MultiRDP.exe ...
python -m PyInstaller --noconfirm --clean --onefile --noconsole --workpath .build --paths src --hidden-import webui.server --name MultiRDP src\tray_app.py
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed.
    pause & exit /b 1
)

echo [3/3] Copying FreeRDP client to dist\FreeRDP ...
if not exist "3rd\FreeRDP\sdl3-freerdp.exe" (
    echo [WARN] 3rd\FreeRDP\sdl3-freerdp.exe not found - skipping client copy.
    goto done
)
REM A running remote window keeps dist\FreeRDP\sdl3-freerdp.exe open, and writing an
REM open exe fails with a sharing violation. So compare first: when the client is
REM already identical there is nothing to copy and the locked file is never touched.
fc /b "3rd\FreeRDP\sdl3-freerdp.exe" "dist\FreeRDP\sdl3-freerdp.exe" >nul 2>&1
if not errorlevel 1 (
    echo [OK] FreeRDP client already up to date in dist\FreeRDP - nothing to copy.
    goto done
)
xcopy "3rd\FreeRDP" "dist\FreeRDP" /E /I /Y /Q >nul 2>&1
if errorlevel 1 (
    REM Still identical after the failed copy? Then a running window had it open and
    REM the existing copy is fine; only report FAIL when the files really differ.
    fc /b "3rd\FreeRDP\sdl3-freerdp.exe" "dist\FreeRDP\sdl3-freerdp.exe" >nul 2>&1
    if errorlevel 1 (
        echo [FAIL] copying 3rd\FreeRDP to dist\FreeRDP failed.
        echo        Close the remote windows / exit MultiRDP from the tray, then rebuild.
        pause & exit /b 1
    )
    echo [WARN] FreeRDP client is in use by a running window - existing copy kept.
)

:done
REM PyInstaller work dir is temporary - remove it after a successful build
if exist ".build" rd /s /q ".build"
echo.
echo ==== BUILD OK ====
echo Output : dist\MultiRDP.exe
echo Client : dist\FreeRDP\   (sdl3-freerdp.exe / wfreerdp.exe)
echo.
echo Next: double-click dist\MultiRDP.exe  (asks for admin rights, then stays in tray)
echo       tray menu -^> Open Web UI
pause
