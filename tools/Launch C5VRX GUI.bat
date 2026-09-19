@echo off
setlocal
cd /d "%~dp0.."

echo Preparing the C5VRX GUI and flashing dependencies...
py -3 -m pip install --disable-pip-version-check -r tools\requirements-gui.txt
if errorlevel 1 (
    echo.
    echo Unable to install the GUI dependencies. Check that Python is installed and that this PC has Internet access.
    pause
    exit /b 1
)

start "C5VRX Firmware Builder and Flasher" pyw -3 tools\c5vrx_gui.py
endlocal
