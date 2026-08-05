@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0dejaview.ps1" %*
exit /b %errorlevel%
