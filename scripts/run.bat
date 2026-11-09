@echo off
REM ==== MultiRDP 运行/调试脚本（双击即可）====
REM 以管理员身份启动用户管理 Web UI（控制台模式，可直接看日志），
REM 浏览器会自动打开 http://127.0.0.1:8123
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
