@echo off
chcp 65001 >nul
echo 正在从云端拉取最新脚本更新...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri ('https://ztb.139771.xyz/update_i5.zip?t=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -OutFile 'D:\ztb_collector\update_i5.zip'"
if exist "D:\ztb_collector\update_i5.zip" (
    powershell -Command "Expand-Archive -Path 'D:\ztb_collector\update_i5.zip' -DestinationPath 'D:\ztb_collector' -Force"
    del "D:\ztb_collector\update_i5.zip"
    echo ========================================
    echo 脚本与微信推送配置已成功更新并覆盖！
    echo ========================================
) else (
    echo 下载更新包失败，请检查网络连接。
)
pause
