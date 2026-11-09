@echo off
REM ==== MultiRDP 打包脚本：把 Web UI + 托盘程序打包成单个 exe ====
REM 产物: dist\MultiRDP.exe （托盘常驻，菜单：打开 Web UI / 设置 / 退出）
REM 运行时数据（users.json / config.json）和 FreeRDP 客户端放在 exe 同目录。
cd /d "%~dp0.."

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
echo 接下来（首次使用）:
echo   1. 把仓库 bin\FreeRDP\ 目录复制到 dist\FreeRDP\ （FreeRDP 客户端）
echo   2. 双击 dist\MultiRDP.exe（会自动请求管理员权限），程序驻留托盘
echo   3. 托盘右键菜单 -^> 打开 Web UI
pause
