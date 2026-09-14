@echo off
chcp 65001 >nul
cd /d "D:\ztb_collector"
echo 正在检查并拉取最新脚本...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri 'https://ztb.139771.xyz/update_i5.zip' -OutFile 'D:\ztb_collector\update_i5.zip' -TimeoutSec 10; Expand-Archive -Path 'D:\ztb_collector\update_i5.zip' -DestinationPath 'D:\ztb_collector' -Force; Remove-Item 'D:\ztb_collector\update_i5.zip' -Force; Write-Host '脚本已自动更新为最新版' } catch { Write-Host '未获取到新更新或网络异常，继续按本地版本执行' }"

set "PYTHON_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
"%PYTHON_EXE%" run_daily.py >> D:\ztb_collector\run.log 2>&1
