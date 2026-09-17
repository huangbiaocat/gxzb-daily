@echo off
cd /d "%~dp0\.."
set "PY_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=python"
"%PY_EXE%" scripts\build_exe.py
pause
