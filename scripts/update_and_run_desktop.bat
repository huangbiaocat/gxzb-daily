@echo off
cd /d "%~dp0\.."

echo [1/3] Downloading latest update from cloud...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri ('https://ztb.139771.xyz/update_i5.zip?t=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile 'update_i5.zip' -TimeoutSec 15; Expand-Archive -Path 'update_i5.zip' -DestinationPath '.' -Force; Remove-Item 'update_i5.zip' -Force; Write-Host '[Update] Successfully updated to latest version!' } catch { Write-Host '[Update] Use local existing version.' }"

echo [2/3] Cleaning up old port 8089 processes...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8089 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo [3/3] Starting Tender Desktop App...
set "PY_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=python"

start "" "%PY_EXE%" desktop_app.py
exit
