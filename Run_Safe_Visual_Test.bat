@echo off
setlocal
set "DRIVERTHERMALQC_HIK_RAW=0"
echo Starting DriverThermalQC in SAFE visual-first mode.
echo Raw HIK native probing is disabled.
"%~dp0dist\DriverThermalQC.exe" %*
endlocal
