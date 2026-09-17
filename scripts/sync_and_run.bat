@echo off
cd /d "D:\ztb_collector"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri (\x27https://ztb.139771.xyz/update_i5.zip?t=\x27 + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile \x27D:\ztb_collector\update_i5.zip\x27 -UseBasicParsing -TimeoutSec 15; Expand-Archive -Path \x27D:\ztb_collector\update_i5.zip\x27 -DestinationPath \x27D:\ztb_collector\x27 -Force; Remove-Item \x27D:\ztb_collector\update_i5.zip\x27 -Force; Write-Host \x27[Update] Successfully updated.\x27 } catch { Write-Host \x27[Update] Keep existing local files.\x27 }"

set "PYTHON_EXE=C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" run_daily.py >> D:\ztb_collector\run.log 2>&1
