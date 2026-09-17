@echo off
cd /d "%~dp0"

echo [1/2] Checking Python Environment...

set "PY_EXE="
if exist "C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe" (
    set "PY_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
)

if "%PY_EXE%"=="" (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PY_EXE set "PY_EXE=%%I"
    )
)

if "%PY_EXE%"=="" (
    echo [ERROR] Python not found in system PATH or default directory!
    echo Please ensure Python is installed or added to PATH.
    echo.
    pause
    exit /b 1
)

echo [OK] Python path: %PY_EXE%
echo [2/2] Launching desktop_app.py ...
echo.

"%PY_EXE%" desktop_app.py
if errorlevel 1 (
    echo.
    echo [ERROR] Application exited with error code: %errorlevel%
    pause
)
