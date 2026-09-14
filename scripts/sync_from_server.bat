@echo off
chcp 65001 >nul
echo 正在从云端拉取最新脚本更新...
powershell -Command "Invoke-WebRequest -Uri 'https://ztb.139771.xyz/update_i5.zip' -OutFile 'D:\ztb_collector\update_i5.zip'"
if exist "D:\ztb_collector\update_i5.zip" (
    powershell -Command "Expand-Archive -Path 'D:\ztb_collector\update_i5.zip' -DestinationPath 'D:\ztb_collector' -Force"
    del "D:\ztb_collector\update_i5.zip"
    echo 更新已成功应用！
) else (
    echo 下载失败，请检查网络。
)
pause
