@echo off
echo Installing dependencies...
pip install Pillow pynput

echo.
echo Starting Crosshair...
python crosshair_gui.py

pause
