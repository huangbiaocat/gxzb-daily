# -*- coding: utf-8 -*-
"""
招投标数据采集监控与控制面板 (Tender Web Manager)
内置原生 HTTP 服务，无需安装 Flask/FastAPI 即可运行。
"""
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime, date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 载入根项目与配置
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

try:
    import config
except ImportError:
    config = None

PORT = int(getattr(config, "MANAGER_PORT", os.environ.get("MANAGER_PORT", 8089)))
STATIC_DIR = Path(__file__).resolve().parent / "static"

class ProcessManager:
    """管理后台任务进程执行与实时日志捕获"""
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.log_lines = []
        self.max_log_lines = 1000
        self.status = "idle"  # idle | running | success | error
        self.start_time = None
        self.end_time = None
        self.task_name = ""
        self.exit_code = None

    def append_log(self, text):
        with self.lock:
            lines = text.splitlines(keepends=True)
            for line in lines:
                self.log_lines.append(line)
            if len(self.log_lines) > self.max_log_lines:
                self.log_lines = self.log_lines[-self.max_log_lines:]

    def start_task(self, name, cmd):
        with self.lock:
            if self.process and self.process.poll() is None:
                return False, "已有任务正在运行中，请等待完成"
            self.status = "running"
            self.task_name = name
            self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.end_time = None
            self.log_lines = []
            self.exit_code = None

        def _worker():
            try:
                self.append_log(f"[{self.start_time}] 开始执行任务: {name}\n")
                self.append_log(f"命令: {' '.join(str(c) for c in cmd)}\n" + "-" * 50 + "\n")
                # 使用 unbuffered 运行
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )
                with self.lock:
                    self.process = proc

                for line in iter(proc.stdout.readline, ''):
                    if not line:
                        break
                    self.append_log(line)
                proc.stdout.close()
                rc = proc.wait()
                with self.lock:
                    self.exit_code = rc
                    self.status = "success" if rc == 0 else "error"
                    self.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.append_log("-" * 50 + f"\n[{self.end_time}] 任务结束，退出代码: {rc}\n")
            except Exception as e:
                with self.lock:
                    self.status = "error"
                    self.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.append_log(f"\n[执行异常]: {str(e)}\n")

        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        return True, "任务已启动"

    def stop_task(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                    self.status = "error"
                    self.append_log("\n[用户手动终止任务]\n")
                    return True, "已发送终止信号"
                except Exception as e:
                    return False, str(e)
            return False, "当前无运行中任务"

    def get_state(self):
        with self.lock:
            # 校验当前状态
            if self.process and self.process.poll() is not None and self.status == "running":
                self.status = "success" if self.process.returncode == 0 else "error"
                self.exit_code = self.process.returncode
            return {
                "status": self.status,
                "task_name": self.task_name,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "exit_code": self.exit_code,
                "log": "".join(self.log_lines)
            }

PROC_MGR = ProcessManager()

def get_system_status():
    """获取系统整体统计与数据概览"""
    site_dir = ROOT_DIR / "dist"
    db_path = getattr(config, "DB_PATH", ROOT_DIR / "data" / "tenders.db")
    
    # 统计历史 HTML 页面
    html_files = []
    if site_dir.exists():
        for p in site_dir.glob("20*.html"):
            if re.match(r"^20\d{2}-\d{2}-\d{2}\.html$", p.name):
                stat = p.stat()
                html_files.append({
                    "date": p.stem,
                    "filename": p.name,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                })
    html_files.sort(key=lambda x: x["date"], reverse=True)

    # 统计 SQLite 数据库条目
    db_exists = db_path.exists()
    db_total = 0
    today_db_count = 0
    today_str = date.today().isoformat()
    if db_exists:
        try:
            import sqlite3
            with sqlite3.connect(str(db_path)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM tenders")
                db_total = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM tenders WHERE date = ?", (today_str,))
                today_db_count = cur.fetchone()[0]
        except Exception:
            pass

    # 读取最近一次 run.log 的末尾
    run_log_path = ROOT_DIR / "run.log"
    last_run_log = ""
    if run_log_path.exists():
        try:
            with open(run_log_path, "rb") as f:
                # 倒读 4KB
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 4096), 0)
                last_run_log = f.read().decode("utf-8", errors="replace")
        except Exception:
            pass

    return {
        "today": today_str,
        "html_count": len(html_files),
        "recent_pages": html_files[:10],
        "db_exists": db_exists,
        "db_total": db_total,
        "today_db_count": today_db_count,
        "vps_host": getattr(config, "VPS_HOST", "217.142.149.2"),
        "vps_user": getattr(config, "VPS_USER", "root"),
        "vps_path": getattr(config, "VPS_PATH", "/opt/1panel/apps/openresty/openresty/www/sites/ztb/index/"),
        "auto_upload_vps": getattr(config, "AUTO_UPLOAD_VPS", False),
        "wechat_configured": bool(getattr(config, "WECHAT_APPID", "") and getattr(config, "WECHAT_TOUSER", "")),
        "last_run_log_tail": last_run_log[-1000:] if last_run_log else ""
    }

def read_config_env():
    """读取当前配置并返回可编辑表单字典"""
    env_file = ROOT_DIR / ".env"
    raw_env = {}
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    raw_env[k.strip()] = v.strip()

    res = {
        "FOCUS_KEYWORDS": getattr(config, "FOCUS_KEYWORDS", ["公路", "医院", "学校", "水利", "防洪", "大桥"]),
        "FOCUS_KEYWORDS_STR": ",".join(getattr(config, "FOCUS_KEYWORDS", [])) if getattr(config, "FOCUS_KEYWORDS", None) else "",
        "AUTO_UPLOAD_VPS": "true" if getattr(config, "AUTO_UPLOAD_VPS", False) else "false",
        "VPS_HOST": getattr(config, "VPS_HOST", "217.142.149.2"),
        "VPS_PORT": getattr(config, "VPS_PORT", "22"),
        "VPS_USER": getattr(config, "VPS_USER", "root"),
        "VPS_PATH": getattr(config, "VPS_PATH", "/opt/1panel/www/tender_site/"),
        "SITE_BASE_URL": getattr(config, "SITE_BASE_URL", "https://ztb.139771.xyz"),
        "WECHAT_APPID": getattr(config, "WECHAT_APPID", ""),
        "WECHAT_APPSECRET": getattr(config, "WECHAT_APPSECRET", ""),
        "WECHAT_TOUSER": getattr(config, "WECHAT_TOUSER", ""),
        "WECHAT_TEMPLATE_ID": getattr(config, "WECHAT_TEMPLATE_ID", ""),
        "WECHAT_ALERT_TEMPLATE_ID": getattr(config, "WECHAT_ALERT_TEMPLATE_ID", ""),
    }
    return res

def save_config_env(data):
    """保存配置项到 .env 文件并重新载入 config"""
    env_file = ROOT_DIR / ".env"
    lines = []
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

    keys_written = set()
    new_lines = []
    
    mapping = {
        "FOCUS_KEYWORDS": data.get("FOCUS_KEYWORDS_STR", "").strip(),
        "AUTO_UPLOAD_VPS": data.get("AUTO_UPLOAD_VPS", "false").strip().lower(),
        "VPS_HOST": data.get("VPS_HOST", "").strip(),
        "VPS_PORT": data.get("VPS_PORT", "22").strip(),
        "VPS_USER": data.get("VPS_USER", "root").strip(),
        "VPS_PATH": data.get("VPS_PATH", "").strip(),
        "SITE_BASE_URL": data.get("SITE_BASE_URL", "").strip(),
        "WECHAT_APPID": data.get("WECHAT_APPID", "").strip(),
        "WECHAT_APPSECRET": data.get("WECHAT_APPSECRET", "").strip(),
        "WECHAT_TOUSER": data.get("WECHAT_TOUSER", "").strip(),
        "WECHAT_TEMPLATE_ID": data.get("WECHAT_TEMPLATE_ID", "").strip(),
        "WECHAT_ALERT_TEMPLATE_ID": data.get("WECHAT_ALERT_TEMPLATE_ID", "").strip(),
    }

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            k = k.strip()
            if k in mapping:
                new_lines.append(f"{k}={mapping[k]}\n")
                keys_written.add(k)
                continue
        new_lines.append(line)

    # 追加未出现的配置
    for k, v in mapping.items():
        if k not in keys_written and v != "":
            new_lines.append(f"{k}={v}\n")

    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # 重新载入 config
    if config:
        import importlib
        importlib.reload(config)

    return True

class ManagerHandler(http.server.BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            index_file = STATIC_DIR / "index.html"
            if index_file.exists():
                content = index_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404, "index.html not found")
            return

        if path == "/api/status":
            self.send_json(get_system_status())
            return

        if path == "/api/task_state":
            self.send_json(PROC_MGR.get_state())
            return

        if path == "/api/config":
            self.send_json(read_config_env())
            return

        # 预览静态 dist 中的文件
        if path.startswith("/dist/") or path == "/dist":
            rel = path[6:].lstrip("/") if path.startswith("/dist/") else "index.html"
            if not rel:
                rel = "index.html"
            target = ROOT_DIR / "dist" / rel
            if target.exists() and target.is_file():
                mime = "text/html; charset=utf-8" if target.suffix == ".html" else "application/octet-stream"
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        post_data = {}
        if length > 0:
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                post_data = json.loads(raw)
            except Exception:
                pass

        py_exe = sys.executable
        collector_exe = None
        if getattr(sys, 'frozen', False):
            candidate = Path(py_exe).resolve().parent / "ztb_collector.exe"
            if candidate.exists():
                collector_exe = candidate
            elif (ROOT_DIR / "dist" / "ztb_collector.exe").exists():
                collector_exe = ROOT_DIR / "dist" / "ztb_collector.exe"

        # 如果没有安装全局 python 命令且没有直接 py_exe，回退使用内置或默认 Python 解释器
        if not getattr(sys, 'frozen', False):
            run_cmd_prefix = [py_exe]
        else:
            # 优先看有没有配置好的 python.exe
            run_cmd_prefix = [collector_exe] if collector_exe else [py_exe]

        if path == "/api/run_daily":
            target_date = post_data.get("date", "").strip() or date.today().isoformat()
            if collector_exe:
                cmd = [str(collector_exe), "--date", target_date]
            else:
                cmd = [py_exe, str(ROOT_DIR / "run_daily.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"全流程日报流水线 ({target_date})", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_collect":
            target_date = post_data.get("date", "").strip() or date.today().isoformat()
            cmd = [py_exe, str(ROOT_DIR / "scripts" / "collect.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"单步采集公告 ({target_date})", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_build":
            target_date = post_data.get("date", "").strip() or date.today().isoformat()
            cmd = [py_exe, str(ROOT_DIR / "scripts" / "build_daily_page.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"重新构建静态页面 ({target_date})", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_upload":
            cmd = [py_exe, str(ROOT_DIR / "scripts" / "upload_vps.py")]
            ok, msg = PROC_MGR.start_task("同步上传静态文件至 VPS", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/test_wechat":
            cmd = [py_exe, "-c", "import sys; sys.path.insert(0, '.'); from scripts.notify_wechat import test_push; sys.exit(0 if test_push() else 1)"]
            ok, msg = PROC_MGR.start_task("测试微信服务号推送", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/stop_task":
            ok, msg = PROC_MGR.stop_task()
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/save_config":
            try:
                save_config_env(post_data)
                self.send_json({"ok": True, "msg": "配置已成功保存并重新加载"})
            except Exception as exc:
                self.send_json({"ok": False, "msg": f"保存失败: {str(exc)}"}, status=500)
            return

        self.send_error(404, "Not Found")


def run_server(host="127.0.0.1", port=PORT):
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((host, port), ManagerHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass

def main():
    print(f"==================================================")
    print(f"招投标数据中心控制台 (Tender Manager) 启动中...")
    print(f"本地访问地址: http://127.0.0.1:{PORT}")
    print(f"局域网访问:   http://0.0.0.0:{PORT}")
    print(f"按 Ctrl+C 可停止控制台服务")
    print(f"==================================================")
    # 支持端口复用
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), ManagerHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n控制台服务已停止。")

if __name__ == "__main__":
    main()
