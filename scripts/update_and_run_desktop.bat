@echo off
chcp 65001 >nul
cd /d "%~dp0\.."

echo ========================================================
echo   正在启动 招投标桌面控制中心 (自动热更新 + 端口守护)
echo ========================================================

echo [1/3] 正在检查并获取云端最新版本...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri ('https://ztb.139771.xyz/update_i5.zip?t=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile 'update_i5.zip' -UseBasicParsing -TimeoutSec 15; Expand-Archive -Path 'update_i5.zip' -DestinationPath '.' -Force; Remove-Item 'update_i5.zip' -Force; $v = ''; if (Test-Path 'config.py') { $l = Get-Content 'config.py' | Select-String 'APP_VERSION'; if ($l -match '[\x22\x27]([^\x22\x27]+)[\x22\x27]') { $v = ' (' + $matches[1] + ')' } }; Write-Host ('[Update] 已成功同步云端最新程序版本' + $v + '！') -ForegroundColor Green } catch { Write-Host '[Update] 网络离线或跳过，使用本机现有版本继续启动。' }"

echo [2/3] 正在守护端口，清理可能残留的 8089 僵死进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8089 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo [3/3] 正在唤起桌面应用独立控制台...
set "PY_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY_EXE%" (
    set "PY_EXE=python"
)

"%PY_EXE%" desktop_app.py
if errorlevel 1 (
    echo.
    echo ========================================================
    echo [提示] 启动异常，请检查上方日志，窗口将暂停便于排查问题。
    echo ========================================================
    pause
)
