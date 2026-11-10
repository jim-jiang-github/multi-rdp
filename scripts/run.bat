@echo off
REM ==== MultiRDP run / debug script (just double-click) ====
REM Starts the user-management web UI with a visible console (so you can read logs);
REM the browser opens http://127.0.0.1:8123 automatically.
REM NOTE: keep this file pure ASCII + CRLF. cmd.exe misparses UTF-8 Chinese text.
cd /d "%~dp0.."
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator permission...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)
echo Starting MultiRDP web UI (debug mode)...
python src\webui\server.py
if %errorlevel% neq 0 (
    echo.
    echo exited with code %errorlevel%
)
pause
