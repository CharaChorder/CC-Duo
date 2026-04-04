@echo off
cd /d "%~dp0"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%~dp0dual_mouse_hands.py"
if errorlevel 1 pause
