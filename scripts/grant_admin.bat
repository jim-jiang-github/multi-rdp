@echo off
chcp 437 >nul
title Grant administrator to remote users
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0grant_admin.ps1"
echo.
echo Result file: C:\Users\Public\grant_admin_result.txt
pause
