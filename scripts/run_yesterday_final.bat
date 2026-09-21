@echo off
chcp 65001 >nul
cd /d "D:\ztb_collector"
set "PYTHON_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)
"%PYTHON_EXE%" scripts\run_yesterday_final.py >> D:\ztb_collector\run_yesterday_final.log 2>&1
