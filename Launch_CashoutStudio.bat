@echo off
REM Convenience shortcut for the packaged app - the same thing as
REM double-clicking CashoutStudio\CashoutStudio.exe.
cd /d "%~dp0"

if not exist "CashoutStudio\CashoutStudio.exe" (
    echo CashoutStudio\CashoutStudio.exe not found. Build it first:
    echo     python build_dist.py
    pause
    exit /b 1
)

start "" "CashoutStudio\CashoutStudio.exe"
