@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================================
echo   正在启动 招投标桌面控制中心...
echo ========================================================
echo.

set "PY_EXE="
if exist "C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe" (
    set "PY_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
) else (
    where python >nul 2>&1
    if not errorlevel 1 (
        set "PY_EXE=python"
    )
)

if "%PY_EXE%"=="" (
    echo [错误] 未能在本机找到可用 Python 环境！
    echo 请确认 Python 安装路径。
    echo.
    pause
    exit /b 1
)

echo 使用 Python: %PY_EXE%
echo 正在运行 desktop_app.py ...
echo.

"%PY_EXE%" desktop_app.py

echo.
echo ========================================================
echo 程序已退出（退出码: %errorlevel%）。
echo ========================================================
pause
