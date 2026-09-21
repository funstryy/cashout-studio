@echo off
setlocal
cd /d "%~dp0"

echo ===================================================
echo   Cashout Studio - prerequisite toolchain installer
echo ===================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_prereqs.ps1" %*

endlocal
