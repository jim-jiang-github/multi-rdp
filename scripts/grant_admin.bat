@echo off
REM Grant administrator rights to the managed remote users (see grant_admin.ps1)
title Grant administrator to remote users
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0grant_admin.ps1"
echo.
echo Result file: C:\Users\Public\grant_admin_result.txt
pause
