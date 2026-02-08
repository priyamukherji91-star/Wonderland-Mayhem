@echo off
setlocal enabledelayedexpansion

REM ------------------------------------------
REM 1. Change this to your GUI filename
REM ------------------------------------------
set GUI_FILE=cheshire_admin_gui.py

echo.
echo ===============================
echo   Building Cheshire Admin EXE
echo ===============================
echo.

REM Check file exists
if not exist "%GUI_FILE%" (
    echo ERROR: %GUI_FILE% not found in this folder.
    pause
    exit /b
)

REM ------------------------------------------
REM 2. Install PyInstaller if missing
REM ------------------------------------------
echo Checking PyInstaller...
python -m PyInstaller -h >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    python -m pip install pyinstaller
)

REM ------------------------------------------
REM 3. Run build
REM ------------------------------------------
echo Building EXE...
pyinstaller --onefile "%GUI_FILE%"

REM ------------------------------------------
REM 4. Done
REM ------------------------------------------
echo.
echo =======================================
echo   Build complete!
echo   EXE is located in the /dist folder
echo =======================================
echo.
pause
