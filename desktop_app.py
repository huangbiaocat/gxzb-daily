# -*- coding: utf-8 -*-
"""
招投标数据采集桌面控制中心 (Tender Desktop App)
启动内置监控与控制面板，并在桌面启动专用 App 窗口（Edge / Chrome App Mode 或默认浏览器）。
"""
import os
import sys
import time
import socket
import threading
import subprocess
import webbrowser
from pathlib import Path
try:
    from PIL import Image, ImageDraw
    import pystray
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False
    Image = ImageDraw = pystray = None

# 定位当前运行环境目录
if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).resolve().parent
    APP_DIR = _exe_dir.parent if _exe_dir.name.lower() == "dist" else _exe_dir
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = APP_DIR

# 将必要路径加入 sys.path
for p in [str(APP_DIR), str(BUNDLE_DIR), str(BUNDLE_DIR / "scripts"), str(APP_DIR / "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

import manager.server as manager_server

# 确保在冻结环境中从当前运行目录加载 .env
try:
    import config
    # 强制让 config 的路径以 APP_DIR 为基准
    if getattr(sys, "frozen", False):
        config.REPO_ROOT = APP_DIR
        config._ENV = config.load_env_file(APP_DIR / ".env")
        config.SITE_DIR = config.get_path("SITE_DIR", APP_DIR / "dist")
        config.DATA_DIR = config.get_path("DATA_DIR", APP_DIR / "data")
        config.RAW_DIR = config.DATA_DIR / "raw"
        config.COLLECT_DIR = config.DATA_DIR / "collect"
        config.DAILY_DIR = config.DATA_DIR / "daily"
        config.STATE_DIR = config.DATA_DIR / "state"
        config.LOG_DIR = config.get_path("LOG_DIR", config.SITE_DIR / "logs")
        config.REPORT_DIR = config.get_path("REPORT_DIR", config.SITE_DIR / "reports")
        config.TEMPLATE_PREVIEW = BUNDLE_DIR / "templates" / "index-preview.html"
        config.TEMPLATE_ARCHIVE_SAMPLE = BUNDLE_DIR / "templates" / "index-sample.html"
        config.ensure_dirs()
except Exception as e:
    print(f"[Desktop App] 配置初始化告警: {e}")


PORT = 8089


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

def kill_old_server_on_port(port):
    """在 Windows 上如果端口被占用且不是自身，尝试清理旧的后台服务"""
    if not is_port_in_use(port):
        return
    if sys.platform == "win32":
        try:
            # 查找占用指定端口的 PID
            out = subprocess.check_output(f"netstat -ano | findstr :{port}", shell=True).decode(errors="ignore")
            lines = [l.strip() for l in out.splitlines() if f":{port}" in l and "LISTENING" in l]
            current_pid = os.getpid()
            for line in lines:
                parts = line.split()
                if parts:
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) != current_pid:
                        print(f"[Desktop App] 正在释放旧进程 PID: {pid} 对端口 {port} 的占用...")
                        subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)
        except Exception as e:
            print(f"[Desktop App] 检查/释放旧端口占用失败: {e}")

def start_server_background():
    kill_old_server_on_port(PORT)
    if is_port_in_use(PORT):
        print(f"[Desktop App] 端口 {PORT} 已有服务运行，复用现有服务")
        return
    t = threading.Thread(target=manager_server.run_server, args=("127.0.0.1", PORT), daemon=True)
    t.start()
    for _ in range(30):
        if is_port_in_use(PORT):
            print(f"[Desktop App] 后台管理服务已就绪: http://127.0.0.1:{PORT}")
            return
        time.sleep(0.1)


def find_browser_app_cmd(url):
    """优先寻找 Edge 或 Chrome 的 --app 独立视窗模式"""
def create_tray_icon_image():
    """动态绘制托盘图标（蓝底白标盾徽造型）"""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(24, 144, 255, 255))
    draw.rectangle([18, 26, 46, 48], fill=(255, 255, 255, 255))
    draw.rectangle([26, 20, 38, 26], fill=(255, 255, 255, 255))
    draw.rectangle([22, 32, 42, 34], fill=(24, 144, 255, 255))
    return image

def setup_system_tray(url, on_exit_callback):
    """配置系统状态栏托盘图标"""
    if not HAS_TRAY:
        print("[Desktop App] 未检测到 pystray/PIL 模块，跳过托盘图标常驻")
        return None

    def on_open_panel(icon, item):
        open_ui(url)

    def on_quit(icon, item):
        print("[Desktop App] 用户从状态栏选择退出整个服务...")
        icon.stop()
        if on_exit_callback:
            on_exit_callback()

    menu = pystray.Menu(
        pystray.MenuItem("打开控制台", on_open_panel, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出整个服务", on_quit)
    )

    icon = pystray.Icon(
        name="ZtbCollector",
        icon=create_tray_icon_image(),
        title="广西招投标数据采集 · 控制中心 (后台运行中)",
        menu=menu
    )
    return icon


def find_browser_app_cmd(url):
    candidates = [
        # Edge 路径
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        # Chrome 路径
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for exe in candidates:
        if os.path.exists(exe):
            return [
                exe,
                f"--app={url}",
                "--window-size=1200,800",
                "--no-proxy-server",
                "--proxy-bypass-list=127.0.0.1;localhost",
            ]
    return None


def open_ui(url):
    app_cmd = find_browser_app_cmd(url)
    if app_cmd:
        try:
            return subprocess.Popen(app_cmd)
        except Exception as e:
            print(f"[Desktop App] 启动独立视窗失败 ({e})，降级为默认浏览器打开")
    webbrowser.open(url)
    return None


def main():
    url = f"http://127.0.0.1:{PORT}"
    print(f"==================================================")
    print(f"  广西招投标数据采集 · 桌面控制中心 v0.0.1")
    print(f"  访问地址: {url}")
    print(f"==================================================")
    start_server_background()

    open_ui(url)

    if "--test" in sys.argv:
        time.sleep(2)
        return

    def handle_exit():
        os._exit(0)

    tray_icon = setup_system_tray(url, on_exit_callback=handle_exit)

    if tray_icon:
        print("[Desktop App] 系统状态栏托盘已启动。关闭浏览器窗口后后台仍常驻，托盘右键可彻底退出。")
        try:
            tray_icon.run()
        except Exception as e:
            print(f"[Desktop App] 托盘运行异常: {e}")
            handle_exit()
        return

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n桌面控制中心已退出。")


if __name__ == "__main__":
    main()
