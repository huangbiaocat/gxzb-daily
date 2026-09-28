@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "D:\ztb_collector"

:: 1. 尝试拉取云端热更新包 (超时 15s 防网络阻塞)
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri ('https://ztb.139771.xyz/update_i5.zip?t=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile 'D:\ztb_collector\update_i5.zip' -UseBasicParsing -TimeoutSec 15; Expand-Archive -Path 'D:\ztb_collector\update_i5.zip' -DestinationPath 'D:\ztb_collector' -Force; Remove-Item 'D:\ztb_collector\update_i5.zip' -Force; Write-Host '[Update] Successfully synced.' } catch { Write-Host '[Update] Keep current.' }"

set "PYTHON_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

:: 2. 隧道守护与健康自愈
"%PYTHON_EXE%" scripts\tunnel_guardian.py --ensure

:: 3. 运行日常采集与推送任务
"%PYTHON_EXE%" run_daily.py >> D:\ztb_collector\run.log 2>&1
