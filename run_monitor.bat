@echo off
title C5VRX-3 Serial Monitor
cd /d "%~dp0"
python tools\monitor.py COM10
pause
