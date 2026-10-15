@echo off
rem Installs pynput, which Focus Trainer uses for its keyboard shortcut.
echo Installing pynput for the keyboard shortcut...
py -m pip install --user pynput
if errorlevel 1 python -m pip install --user pynput
echo.
echo Done. Start the app with "Start Focus Trainer.bat".
pause
