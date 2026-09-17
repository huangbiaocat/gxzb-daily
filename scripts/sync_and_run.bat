@echo off
cd /d "D:\ztb_collector"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri ('https://ztb.139771.xyz/update_i5.zip?t=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile 'D:\ztb_collector\update_i5.zip' -UseBasicParsing -TimeoutSec 15; Expand-Archive -Path 'D:\ztb_collector\update_i5.zip' -DestinationPath 'D:\ztb_collector' -Force; Remove-Item 'D:\ztb_collector\update_i5.zip' -Force; Write-Host '[Update] Successfully updated.' } catch { Write-Host '[Update] Keep existing local files.' }"

set "PYTHON_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" run_daily.py >> D:\ztb_collector\run.log 2>&1
