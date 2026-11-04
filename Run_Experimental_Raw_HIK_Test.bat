@echo off
setlocal
set "DRIVERTHERMALQC_HIK_RAW=1"
echo WARNING: Experimental HIK raw-stream mode.
echo Native camera probing may hang or terminate the application.
echo Use this only after SAFE visual-first mode has been tested.
pause
"%~dp0dist\DriverThermalQC.exe" %*
endlocal
