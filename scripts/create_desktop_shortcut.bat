@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo 正在更新桌面快捷方式...
powershell -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = [Environment]::GetFolderPath('Desktop'); $s = $ws.CreateShortcut($desktop + '\招投标控制中心.lnk'); $s.TargetPath = '%~dp0update_and_run_desktop.bat'; $s.WorkingDirectory = '%~dp0..'; $s.Description = '招投标数据中心控制台 v0.0.5'; $s.Save(); Write-Host '桌面快捷方式【招投标控制中心】已更新！'"
pause
