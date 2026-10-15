@echo off
rem Starts Focus Trainer without a console window.
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw "%~dp0focus_trainer.py"
) else (
  start "" pythonw "%~dp0focus_trainer.py"
)
