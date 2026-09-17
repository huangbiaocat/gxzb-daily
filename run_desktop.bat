@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY_EXE%" (
    set "PY_EXE=python"
)

"%PY_EXE%" desktop_app.py
if errorlevel 1 (
    echo.
    echo 启动失败，请检查报错信息。
    pause
)
