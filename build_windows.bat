@echo off
REM Build the standalone DriverThermalQC.exe (run on a Windows PC).
REM Requires: Python 3.11+ available as `python`.

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller DriverThermalQC.spec --noconfirm

echo.
echo Build finished. The EXE is at: dist\DriverThermalQC.exe
echo First run creates the data folder %%LOCALAPPDATA%%\DriverThermalQC.
pause
