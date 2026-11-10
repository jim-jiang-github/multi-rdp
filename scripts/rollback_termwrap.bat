@echo off
REM Self-elevating rollback: restore the original TermService ServiceDll
REM NOTE: keep this file pure ASCII + CRLF. cmd.exe misparses UTF-8 Chinese text.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator permission...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rollback_termwrap.ps1"
pause
