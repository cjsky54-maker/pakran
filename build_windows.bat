@echo off
setlocal
REM Build the standalone DriverThermalQC.exe (run on a Windows PC).
REM Requires Python 3.11+ from https://www.python.org/downloads/
REM (tick "Add python.exe to PATH" during setup).

set "PY="

REM Prefer the py launcher (never faked by the Microsoft Store alias).
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"

REM Fall back to python (the Microsoft Store stub fails this check).
if not defined PY (
    python --version >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo.
    echo [ERROR] Python was not found on this computer.
    echo.
    echo Fix 1: install Python 3.11+ from https://www.python.org/downloads/
    echo        and tick "Add python.exe to PATH" in the installer,
    echo        then run this file again.
    echo Fix 2: no Python needed at all - download the ready-built EXE from
    echo        GitHub ^> Actions ^> "Build Windows EXE" ^> latest run ^> Artifacts.
    echo.
    pause
    exit /b 1
)

echo Using Python: %PY%
echo.

%PY% -m pip install --upgrade pip
if errorlevel 1 goto :fail

%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

%PY% -m PyInstaller DriverThermalQC.spec --noconfirm
if errorlevel 1 goto :fail

if not exist "dist\DriverThermalQC.exe" goto :fail

echo.
echo Build finished. The EXE is at: dist\DriverThermalQC.exe
echo First run creates the data folder %%LOCALAPPDATA%%\DriverThermalQC.
pause
exit /b 0

:fail
echo.
echo [ERROR] Build failed - the messages above explain why.
echo You can always download the ready-built EXE instead:
echo GitHub ^> Actions ^> "Build Windows EXE" ^> latest run ^> Artifacts.
pause
exit /b 1
