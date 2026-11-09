@echo off
chcp 65001 >nul
REM 强制删除残留的用户配置目录（takeown + icacls + rd）
REM 用法: remove_profile.bat <用户名>   例如: remove_profile.bat pq68b2w
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator permission...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%~1'"
    exit /b
)
if "%~1"=="" (
    echo Usage: remove_profile.bat ^<username^>
    echo Example: remove_profile.bat pq68b2w
    goto end
)
set TARGET=C:\Users\%~1
echo Cleaning up %TARGET% ...
if not exist "%TARGET%" (
    echo Directory does not exist. Nothing to do.
    goto end
)
takeown /f "%TARGET%" /r /d y >nul 2>&1
icacls "%TARGET%" /grant administrators:F /t /c /q >nul 2>&1
rd /s /q "%TARGET%"
if exist "%TARGET%" (
    echo STILL EXISTS - some files are locked. Close any program using this profile and retry.
    dir /b "%TARGET%"
) else (
    echo DELETED OK
)
:end
echo.
pause
