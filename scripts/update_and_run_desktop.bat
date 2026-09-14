@echo off
chcp 65001 >nul
title 招投标采集控制台 - 自动更新与启动 (v2.2.0)
cd /d "%~dp0\.."

echo ======================================================
echo   招投标数据中心控制台 - 检查更新并启动 (v2.2.0)
echo ======================================================
echo 1. 正在拉取云端最新代码及控制台页面 (v2.2.0)...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri ('https://ztb.139771.xyz/update_i5.zip?t=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile 'update_i5.zip' -TimeoutSec 15; Expand-Archive -Path 'update_i5.zip' -DestinationPath '.' -Force; Remove-Item 'update_i5.zip' -Force; Write-Host '   -> 更新成功！已更新至 v2.2.0' } catch { Write-Host '   -> 暂时无法连接云端或已是最新版，直接运行本地版本' }"

echo 2. 检查并清理旧的服务进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8089 ^| findstr LISTENING') do (
    echo    -> 关闭占用 8089 端口的旧进程 PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo 3. 正在启动桌面控制中心...
set "PY_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=python"

start "" "%PY_EXE%" desktop_app.py
exit
