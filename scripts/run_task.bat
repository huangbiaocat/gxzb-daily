@echo off
chcp 65001 >nul
cd /d "D:\ztb_collector"
set "PYTHON_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
"%PYTHON_EXE%" run_daily.py >> D:\ztb_collector\run.log 2>&1
