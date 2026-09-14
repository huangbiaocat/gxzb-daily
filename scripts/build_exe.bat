@echo off
chcp 65001 >nul
cd /d "%~dp0\.."

echo ========================================================
echo 正在安装 PyInstaller（使用国内清华镜像源加速）...
echo ========================================================
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller

echo.
echo ========================================================
echo 正在打包 run_daily.py 为独立可执行程序 ztb_collector.exe...
echo ========================================================

pyinstaller -F -c run_daily.py ^
    --name ztb_collector ^
    --add-data "config.py;." ^
    --add-data "scripts;scripts" ^
    --hidden-import="scripts.collect" ^
    --hidden-import="scripts.store" ^
    --hidden-import="scripts.build_daily_page" ^
    --hidden-import="scripts.build_archive_page" ^
    --hidden-import="scripts.diff_missing" ^
    --hidden-import="scripts.reconcile" ^
    --hidden-import="scripts.refresh_archive" ^
    --hidden-import="scripts.upload_vps" ^
    --hidden-import="scripts.notify_wechat"

if exist "dist\ztb_collector.exe" (
    echo.
    echo ========================================================
    echo 打包成功！
    echo 可执行文件路径: %CD%\dist\ztb_collector.exe
    echo 可直接双击运行，或将计划任务程序指向该 exe！
    echo ========================================================
) else (
    echo.
    echo 打包遇到错误，请检查上方日志。
)

pause
