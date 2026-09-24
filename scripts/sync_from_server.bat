@echo off
rem ========================================================
rem  Sync and Update from Server (Compatible with all CMD/ANSI)
rem ========================================================
if "%~1"=="--in-temp" goto DO_SYNC

copy /y "%~f0" "%TEMP%\ztb_sync_tmp.bat" >nul
call "%TEMP%\ztb_sync_tmp.bat" --in-temp "%~dp0.."
del "%TEMP%\ztb_sync_tmp.bat" >nul 2>&1
exit /b

:DO_SYNC
cd /d "%~2"

echo [1/3] Fetching update_i5.zip from cloud server...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri ('https://ztb.139771.xyz/update_i5.zip?t=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile 'update_i5.zip' -UseBasicParsing -TimeoutSec 30; Write-Host '[OK] Download successful.' } catch { Write-Host '[FAIL] Download error:' $_.Exception.Message; exit 1 }"

if not exist "update_i5.zip" (
    echo [ERROR] update_i5.zip not found! Check network connection.
    goto END
)

echo [2/3] Extracting and updating files...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Expand-Archive -Path 'update_i5.zip' -DestinationPath '.' -Force; Remove-Item 'update_i5.zip' -Force; Write-Host '[OK] Update extracted.' } catch { Write-Host '[FAIL] Extract error:' $_.Exception.Message; exit 1 }"

echo [3/3] Checking updated system version...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $v = 'v0.3.2'; if (Test-Path 'config.py') { $l = Get-Content 'config.py' | Select-String 'APP_VERSION'; if ($l -match '[\x22\x27]([^\x22\x27]+)[\x22\x27]') { $v = $matches[1] } }; Write-Host '========================================================' -ForegroundColor Green; Write-Host ('  [OK] 恭喜！已成功升级至最新版本: ' + $v) -ForegroundColor Green; Write-Host '========================================================' -ForegroundColor Green"

:END
pause
