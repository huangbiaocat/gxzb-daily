@echo off
rem ========================================================
rem  Sync and Update from Server (Compatible with all CMD/ANSI)
rem ========================================================
cd /d "%~dp0.."

echo [1/3] Fetching update_i5.zip from cloud server...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri ('https://ztb.139771.xyz/update_i5.zip?t=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile 'update_i5.zip' -UseBasicParsing -TimeoutSec 30; Write-Host '[OK] Download successful.' } catch { Write-Host '[FAIL] Download error:' $_.Exception.Message; exit 1 }"

if not exist "update_i5.zip" (
    echo [ERROR] update_i5.zip not found! Check network connection.
    goto END
)

echo [2/3] Extracting and updating files...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Expand-Archive -Path 'update_i5.zip' -DestinationPath '.' -Force; Remove-Item 'update_i5.zip' -Force; Write-Host '[OK] Update extracted.' } catch { Write-Host '[FAIL] Extract error:' $_.Exception.Message; exit 1 }"

echo [3/3] Update completed successfully!
echo ========================================================
echo  All scripts and templates updated to v0.0.8
echo ========================================================

:END
pause
