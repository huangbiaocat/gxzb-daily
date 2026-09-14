import os, sys, subprocess

py_exe = r"C:\Users\DELL\AppData\Local\Programs\Python\Python314\python.exe"
if not os.path.exists(py_exe):
    py_exe = sys.executable

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(root_dir)

print("[1/3] 检查并安装 pyinstaller...")
subprocess.run([py_exe, "-m", "pip", "install", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "pyinstaller", "pystray", "pillow"], check=True)

print("[2/3] 打包桌面控制中心 (TenderManager.exe)...")
cmd_desktop = [
    py_exe, "-m", "PyInstaller",
    "-F", "-w", "desktop_app.py",
    "--name", "TenderManager",
    "--add-data", "manager;manager",
    "--add-data", "scripts;scripts",
    "--add-data", "templates;templates",
    "--add-data", "config.py;.",
    "--add-data", "run_daily.py;.",
    "--hidden-import=manager.server",
    "--hidden-import=pystray",
    "--hidden-import=PIL",
    "--hidden-import=PIL.Image",
    "--hidden-import=PIL.ImageDraw",
    "--hidden-import=scripts.collect",
    "--hidden-import=scripts.store",
    "--hidden-import=scripts.build_daily_page",
    "--hidden-import=scripts.build_archive_page",
    "--hidden-import=scripts.diff_missing",
    "--hidden-import=scripts.upload_vps",
    "--hidden-import=scripts.notify_wechat"
]
subprocess.run(cmd_desktop, check=True)

print("[3/3] 打包控制台每日任务 (ztb_collector.exe)...")
cmd_collector = [
    py_exe, "-m", "PyInstaller",
    "-F", "-c", "run_daily.py",
    "--name", "ztb_collector",
    "--add-data", "config.py;.",
    "--add-data", "templates;templates",
    "--add-data", "scripts;scripts",
    "--hidden-import=scripts.collect",
    "--hidden-import=scripts.store",
    "--hidden-import=scripts.build_daily_page",
    "--hidden-import=scripts.build_archive_page",
    "--hidden-import=scripts.diff_missing",
    "--hidden-import=scripts.upload_vps",
    "--hidden-import=scripts.notify_wechat"
]
subprocess.run(cmd_collector, check=True)

print("打包全部完成！")
