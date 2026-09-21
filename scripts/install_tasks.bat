@echo off
chcp 65001 >nul
echo ======================================================
echo 正在安装/更新 Windows 计划任务...
echo ======================================================

cd /d "D:\ztb_collector"

rem 1. 安装 10 分钟同步与日间监控任务
schtasks /create /tn "ZtbCollector_Sync" /xml "scripts\ztb_task.xml" /f
if %errorlevel% equ 0 (
    echo [成功] ZtbCollector_Sync (每10分钟监控同步) 任务已配置。
) else (
    echo [提示] ZtbCollector_Sync 配置返回码: %errorlevel%
)

rem 2. 安装 每天凌晨 00:10 昨日最终版封存任务
schtasks /create /tn "ZtbCollector_YesterdayFinal" /xml "scripts\ztb_yesterday_task.xml" /f
if %errorlevel% equ 0 (
    echo [成功] ZtbCollector_YesterdayFinal (每日00:10昨日终版封存) 任务已配置。
) else (
    echo [提示] ZtbCollector_YesterdayFinal 配置返回码: %errorlevel%
)

echo ======================================================
echo 安装完成，按任意键退出...
pause >nul
