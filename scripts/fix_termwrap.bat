@echo off
REM Self-elevating: point TermService ServiceDll back to TermWrap.dll
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator permission...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_termwrap.ps1"
echo.
echo Done. REBOOT the PC to activate multi-session.
pause
