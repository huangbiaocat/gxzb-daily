# -*- coding: utf-8 -*-
"""
招投标数据采集监控与控制面板 (Tender Web Manager)
内置原生 HTTP 服务，无需安装 Flask/FastAPI 即可运行。
"""
import http.server
import json
import mimetypes
import os
import re
import shutil
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 载入根项目与配置
if getattr(sys, 'frozen', False):
    _exe_p = Path(sys.executable).resolve().parent
    ROOT_DIR = _exe_p.parent if _exe_p.name.lower() == 'dist' else _exe_p
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

try:
    import config
except ImportError:
    config = None

PORT = int(getattr(config, "MANAGER_PORT", os.environ.get("MANAGER_PORT", 8089)))
def get_static_dir() -> Path:
    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "manager" / "static")
    candidates.append(Path(__file__).resolve().parent / "static")
    candidates.append(ROOT_DIR / "manager" / "static")
    for p in candidates:
        if p.exists() and (p / "index.html").exists():
            return p
    return candidates[0] if candidates else Path(__file__).resolve().parent / "static"

STATIC_DIR = get_static_dir()

def get_site_dir() -> Path:
    """获取展示大屏与日报静态文件所在目录 (dist)"""
    site_dir = getattr(config, "SITE_DIR", ROOT_DIR / "dist")
    if not isinstance(site_dir, Path):
        site_dir = Path(site_dir)
    if not site_dir.is_absolute():
        site_dir = (ROOT_DIR / site_dir).resolve()
    if not site_dir.exists() and (ROOT_DIR / "dist").exists():
        site_dir = (ROOT_DIR / "dist").resolve()
    if site_dir.exists() and not (site_dir / "index.html").exists() and (ROOT_DIR / "dist" / "index.html").exists():
        site_dir = (ROOT_DIR / "dist").resolve()
    if not site_dir.exists() and getattr(sys, 'frozen', False):
        _alt = Path(sys.executable).resolve().parent / 'dist'
        if _alt.exists():
            site_dir = _alt
    return site_dir


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
                env["PYTHONIOENCODING"] = "utf-8"
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
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
            is_running = (self.status == "running")
            log_content = "".join(self.log_lines)
            return {
                "status": self.status,
                "running": is_running,
                "task_name": self.task_name,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "exit_code": self.exit_code,
                "log": log_content,
                "logs": log_content
            }

PROC_MGR = ProcessManager()

def get_git_info():
    """获取本地 Git 版本信息"""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0:
            commit = res.stdout.strip()
            app_ver = getattr(config, "APP_VERSION", "v0.3.2")
            return {"commit": commit, "version": f"{app_ver} (#{commit})"}
    except Exception:
        pass
    return {"commit": "release", "version": getattr(config, "APP_VERSION", "v0.3.2")}

def get_scheduled_task_status():
    """检测 Windows 计划任务 ZtbCollector_Sync 的运行/就绪/启用状态"""
    res_info = {
        "exists": False,
        "enabled": False,
        "state": "未知",
        "next_run": "--",
        "next_run_standard": "",
        "yesterday_task_exists": False,
        "yesterday_task_enabled": False
    }
    if sys.platform != "win32":
        return {
            "exists": True,
            "enabled": True,
            "state": "就绪 (监控中)",
            "next_run": "--",
            "next_run_standard": "",
            "yesterday_task_exists": True,
            "yesterday_task_enabled": True
        }
    try:
        cp = subprocess.run(
            ["schtasks", "/query", "/tn", "ZtbCollector_Sync", "/fo", "CSV", "/nh"],
            capture_output=True,
            timeout=3
        )
        if cp.returncode == 0:
            raw_bytes = cp.stdout
            text_out = ""
            try:
                text_out = raw_bytes.decode("gbk", errors="ignore")
            except Exception:
                text_out = raw_bytes.decode("utf-8", errors="ignore")
            
            parts = [p.strip().strip('"') for p in text_out.strip().split(",")]
            if len(parts) >= 3:
                res_info["exists"] = True
                next_run_raw = parts[1] if parts[1] != "N/A" else "无"
                res_info["next_run"] = next_run_raw
                if next_run_raw and next_run_raw != "无":
                    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
                        try:
                            dt = datetime.strptime(next_run_raw, fmt)
                            res_info["next_run_standard"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                            break
                        except Exception:
                            pass
                state_raw = parts[2]
                if "就绪" in state_raw or "Ready" in state_raw:
                    res_info["enabled"] = True
                    res_info["state"] = "就绪 (监控中)"
                elif "正在运行" in state_raw or "Running" in state_raw:
                    res_info["enabled"] = True
                    res_info["state"] = "执行中"
                elif "已禁用" in state_raw or "Disabled" in state_raw:
                    res_info["enabled"] = False
                    res_info["state"] = "已暂停监控"
                else:
                    res_info["enabled"] = True
                    res_info["state"] = state_raw
    except Exception:
        pass
    try:
        cp_y = subprocess.run(
            ["schtasks", "/query", "/tn", "ZtbCollector_YesterdayFinal", "/fo", "CSV", "/nh"],
            capture_output=True,
            timeout=3
        )
        if cp_y.returncode == 0:
            res_info["yesterday_task_exists"] = True
            res_info["yesterday_task_enabled"] = True
    except Exception:
        pass
    return res_info

def get_system_status():
    """获取系统整体统计与数据概览"""
    site_dir = get_site_dir()
    db_path = getattr(config, "DB_PATH", ROOT_DIR / "data" / "gxzb.sqlite3")
    if not db_path.exists():
        for cand in [ROOT_DIR / "data" / "gxzb.sqlite3", ROOT_DIR / "data" / "tenders.db"]:
            if cand.exists():
                db_path = cand
                break
    
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
    today_monitor_count = 0
    last_monitor_time = ""
    today_str = date.today().isoformat()
    if db_exists:
        try:
            import sqlite3
            with sqlite3.connect(str(db_path)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cur.fetchall()]
                if "notices" in tables:
                    cur.execute("SELECT count(*) FROM notices")
                    db_total = cur.fetchone()[0]
                    cur.execute("SELECT count(*) FROM notices WHERE pub_time LIKE ?", (f"{today_str}%",))
                    today_db_count = cur.fetchone()[0]
                elif "tenders" in tables:
                    cur.execute("SELECT count(*) FROM tenders")
                    db_total = cur.fetchone()[0]
                    cur.execute("SELECT count(*) FROM tenders WHERE date = ?", (today_str,))
                    today_db_count = cur.fetchone()[0]

                if "runs" in tables:
                    cur.execute("SELECT count(*) FROM runs WHERE day = ?", (today_str,))
                    today_monitor_count = cur.fetchone()[0]
                    cur.execute("SELECT started_at, finished_at FROM runs WHERE day = ? ORDER BY rowid DESC LIMIT 1", (today_str,))
                    row = cur.fetchone()
                    if row:
                        last_monitor_time = row[0] or row[1] or ""
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

    # 检查昨日最终版状态
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    state_dir = getattr(config, "STATE_DIR", ROOT_DIR / "data" / "state")
    final_file = state_dir / f"final-{yesterday_str}.json"
    yesterday_final_info = {
        "date": yesterday_str,
        "is_final": final_file.exists(),
        "finalized_at": "",
        "total": 0,
        "enable": getattr(config, "ENABLE_YESTERDAY_FINAL", True),
        "scheduled_time": getattr(config, "YESTERDAY_FINAL_TIME", "00:10"),
    }
    if final_file.exists():
        try:
            fin_d = json.loads(final_file.read_text(encoding="utf-8"))
            yesterday_final_info["finalized_at"] = fin_d.get("finalized_at", "")
            yesterday_final_info["total"] = fin_d.get("page_total", fin_d.get("total", 0))
        except Exception:
            pass

    git_info = get_git_info()
    return {
        "today": today_str,
        "git_commit": git_info["commit"],
        "app_version": git_info["version"],
        "html_count": len(html_files),
        "recent_pages": html_files[:10],
        "db_exists": db_exists,
        "db_total": db_total,
        "today_db_count": today_db_count,
        "today_monitor_count": today_monitor_count,
        "last_monitor_time": last_monitor_time,
        "vps_host": getattr(config, "VPS_HOST", "217.142.149.2"),
        "vps_user": getattr(config, "VPS_USER", "root"),
        "vps_path": getattr(config, "VPS_PATH", "/opt/1panel/apps/openresty/openresty/www/sites/ztb/index/"),
        "auto_upload_vps": getattr(config, "AUTO_UPLOAD_VPS", False),
        "wechat_configured": bool(getattr(config, "WECHAT_APPID", "") and getattr(config, "WECHAT_TOUSER", "")),
        "last_run_log_tail": last_run_log[-1000:] if last_run_log else "",
        "today_has_page": any(p["date"] == today_str for p in html_files),
        "monitor_start_time": getattr(config, "MONITOR_START_TIME", "08:00"),
        "monitor_end_time": getattr(config, "MONITOR_END_TIME", "20:00"),
        "monitor_interval": getattr(config, "MONITOR_INTERVAL_MINUTES", 10),
        "yesterday_final": yesterday_final_info,
        "vps": {
            "host": getattr(config, "VPS_HOST", "217.142.149.2"),
            "auto_upload": getattr(config, "AUTO_UPLOAD_VPS", False),
            "path": getattr(config, "VPS_PATH", "/opt/1panel/apps/openresty/openresty/www/sites/ztb/index/"),
        },
        "wechat": {
            "configured": bool(getattr(config, "WECHAT_APPID", "") and getattr(config, "WECHAT_APPSECRET", "")),
            "touser": getattr(config, "WECHAT_TOUSER", ""),
        },
        "monitor": get_scheduled_task_status(),
    }

def read_config_env():
    """读取当前配置并返回可编辑表单字典"""
    env_file = ROOT_DIR / ".env"
    raw_env = {}
    if env_file.exists():
        raw_b = env_file.read_bytes()
        try:
            c_text = raw_b.decode("utf-8")
        except UnicodeError:
            try:
                c_text = raw_b.decode("gb18030")
            except UnicodeError:
                c_text = raw_b.decode("utf-8", errors="replace")
        for line in c_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                val = v.strip()
                if any(ord(ch) > 127 for ch in val):
                    try:
                        val = val.encode("gbk").decode("utf-8")
                    except Exception:
                        pass
                raw_env[k.strip()] = val

    res = {
        "FOCUS_KEYWORDS": getattr(config, "FOCUS_KEYWORDS", []),
        "FOCUS_KEYWORDS_STR": ",".join(getattr(config, "FOCUS_KEYWORDS", [])) if getattr(config, "FOCUS_KEYWORDS", None) else "",
        "FOCUS_PROJECTS": "\n".join(getattr(config, "FOCUS_PROJECTS", [])) if getattr(config, "FOCUS_PROJECTS", None) else "",
        "FOCUS_OWNERS": "\n".join(getattr(config, "FOCUS_OWNERS", [])) if getattr(config, "FOCUS_OWNERS", None) else "",
        "FOCUS_PROJECT_TYPES": "\n".join(getattr(config, "FOCUS_PROJECT_TYPES", [])) if getattr(config, "FOCUS_PROJECT_TYPES", None) else "",
        "FOCUS_MIN_AMOUNT": getattr(config, "FOCUS_MIN_AMOUNT_RAW", ""),
        "AUTO_UPLOAD_VPS": "true" if getattr(config, "AUTO_UPLOAD_VPS", False) else "false",
        "VPS_HOST": getattr(config, "VPS_HOST", "217.142.149.2"),
        "VPS_PORT": getattr(config, "VPS_PORT", "22"),
        "VPS_USER": getattr(config, "VPS_USER", "root"),
        "VPS_PATH": getattr(config, "VPS_PATH", "/opt/1panel/www/tender_site/"),
        "VPS_KEY_PATH": getattr(config, "VPS_KEY_PATH", ""),
        "SYSTEM_DEFAULT_KEY": str(Path.home() / ".ssh" / "id_rsa"),
        "SYSTEM_SSH_DIR": str(Path.home() / ".ssh"),
        "SYSTEM_KEY_EXISTS": (Path.home() / ".ssh" / "id_rsa").is_file() or (Path.home() / ".ssh" / "id_ed25519").is_file(),
        "SITE_BASE_URL": getattr(config, "SITE_BASE_URL", "https://ztb.139771.xyz"),
        "WECHAT_APPID": getattr(config, "WECHAT_APPID", ""),
        "WECHAT_APPSECRET": getattr(config, "WECHAT_APPSECRET", ""),
        "WECHAT_TOUSER": getattr(config, "WECHAT_TOUSER", ""),
        "WECHAT_ADMIN_TOUSER": getattr(config, "WECHAT_ADMIN_TOUSER", ""),
        "WECHAT_TEMPLATE_ID": getattr(config, "WECHAT_TEMPLATE_ID", ""),
        "WECHAT_ALERT_TEMPLATE_ID": getattr(config, "WECHAT_ALERT_TEMPLATE_ID", ""),
        "PUSH_TRIGGER_MODE": getattr(config, "PUSH_TRIGGER_MODE", "any_complete"),
        "PUSH_ALERT_FOCUS": "true" if getattr(config, "PUSH_ALERT_FOCUS", True) else "false",
        "PUSH_MIN_COUNT": getattr(config, "PUSH_MIN_COUNT", 1),
        "PUSH_NOTIFY_ERROR": "true" if getattr(config, "PUSH_NOTIFY_ERROR", True) else "false",
        "PUSH_BATCH_HOURS": getattr(config, "PUSH_BATCH_HOURS", "08:00, 17:30"),
        "PUSH_CONDITIONAL_INCREMENTAL": "true" if getattr(config, "PUSH_CONDITIONAL_INCREMENTAL", True) else "false",
        "PUSH_TRIGGER_RULES": getattr(config, "PUSH_TRIGGER_RULES", ["focus", "batch_time", "error"]),
        "PUSH_TRIGGER_RULES_STR": ",".join(getattr(config, "PUSH_TRIGGER_RULES", ["focus", "batch_time", "error"])),
        "MONITOR_START_TIME": getattr(config, "MONITOR_START_TIME", "08:00"),
        "MONITOR_END_TIME": getattr(config, "MONITOR_END_TIME", "20:00"),
        "MONITOR_INTERVAL_MINUTES": getattr(config, "MONITOR_INTERVAL_MINUTES", 10),
        "ENABLE_YESTERDAY_FINAL": "true" if getattr(config, "ENABLE_YESTERDAY_FINAL", True) else "false",
        "YESTERDAY_FINAL_TIME": getattr(config, "YESTERDAY_FINAL_TIME", "00:10"),
        "BACKSCAN_ENABLED": "true" if getattr(config, "BACKSCAN_ENABLED", True) else "false",
        "BACKSCAN_DAYS": getattr(config, "BACKSCAN_DAYS", 30),
        "BACKSCAN_MIN_DELAY": getattr(config, "BACKSCAN_MIN_DELAY", 2),
    }
    return res

def save_config_env(data):
    """保存配置项到 .env 文件并重新载入 config"""
    env_file = ROOT_DIR / ".env"
    lines = []
    if env_file.exists():
        raw_b = env_file.read_bytes()
        try:
            content_str = raw_b.decode("utf-8")
        except UnicodeError:
            try:
                content_str = raw_b.decode("gb18030")
            except UnicodeError:
                content_str = raw_b.decode("utf-8", errors="replace")
        lines = [l + "\n" for l in content_str.splitlines()]

    keys_written = set()
    new_lines = []
    
    mapping = {
        "FOCUS_KEYWORDS": data.get("FOCUS_KEYWORDS_STR", "").strip(),
        "FOCUS_PROJECTS": ",".join([p.strip() for p in data.get("FOCUS_PROJECTS", "").replace("\r\n", "\n").replace("，", ",").split("\n") if p.strip()]) if "\n" in data.get("FOCUS_PROJECTS", "") else data.get("FOCUS_PROJECTS", "").strip(),
        "FOCUS_OWNERS": ",".join([p.strip() for p in data.get("FOCUS_OWNERS", "").replace("\r\n", "\n").replace("，", ",").split("\n") if p.strip()]) if "\n" in data.get("FOCUS_OWNERS", "") else data.get("FOCUS_OWNERS", "").strip(),
        "FOCUS_PROJECT_TYPES": ",".join([p.strip() for p in data.get("FOCUS_PROJECT_TYPES", "").replace("\r\n", "\n").replace("，", ",").split("\n") if p.strip()]) if "\n" in data.get("FOCUS_PROJECT_TYPES", "") else data.get("FOCUS_PROJECT_TYPES", "").strip(),
        "FOCUS_MIN_AMOUNT": str(data.get("FOCUS_MIN_AMOUNT", "")).strip(),
        "AUTO_UPLOAD_VPS": data.get("AUTO_UPLOAD_VPS", "false").strip().lower(),
        "VPS_HOST": data.get("VPS_HOST", "").strip(),
        "VPS_PORT": data.get("VPS_PORT", "22").strip(),
        "VPS_USER": data.get("VPS_USER", "root").strip(),
        "VPS_PATH": data.get("VPS_PATH", "").strip(),
        "VPS_KEY_PATH": data.get("VPS_KEY_PATH", "").strip(),
        "SITE_BASE_URL": data.get("SITE_BASE_URL", "").strip(),
        "WECHAT_APPID": data.get("WECHAT_APPID", "").strip(),
        "WECHAT_APPSECRET": data.get("WECHAT_APPSECRET", "").strip(),
        "WECHAT_TOUSER": data.get("WECHAT_TOUSER", "").strip(),
        "WECHAT_ADMIN_TOUSER": data.get("WECHAT_ADMIN_TOUSER", "").strip(),
        "WECHAT_TEMPLATE_ID": data.get("WECHAT_TEMPLATE_ID", "").strip(),
        "WECHAT_ALERT_TEMPLATE_ID": data.get("WECHAT_ALERT_TEMPLATE_ID", "").strip(),
        "PUSH_TRIGGER_MODE": data.get("PUSH_TRIGGER_MODE", "any_complete").strip().lower(),
        "PUSH_ALERT_FOCUS": data.get("PUSH_ALERT_FOCUS", "true").strip().lower(),
        "PUSH_MIN_COUNT": str(data.get("PUSH_MIN_COUNT", "1")).strip(),
        "PUSH_NOTIFY_ERROR": data.get("PUSH_NOTIFY_ERROR", "true").strip().lower(),
        "PUSH_BATCH_HOURS": data.get("PUSH_BATCH_HOURS", "08:00, 17:30").strip(),
        "PUSH_CONDITIONAL_INCREMENTAL": data.get("PUSH_CONDITIONAL_INCREMENTAL", "true").strip().lower(),
        "PUSH_TRIGGER_RULES": ",".join(data.get("PUSH_TRIGGER_RULES")) if isinstance(data.get("PUSH_TRIGGER_RULES"), list) else str(data.get("PUSH_TRIGGER_RULES", "focus,complete,error")).strip(),
        "PUSH_LARGE_AMOUNT": str(data.get("PUSH_LARGE_AMOUNT", "5000")).strip(),
        "MONITOR_START_TIME": data.get("MONITOR_START_TIME", "08:00").strip(),
        "MONITOR_END_TIME": data.get("MONITOR_END_TIME", "20:00").strip(),
        "MONITOR_INTERVAL_MINUTES": str(data.get("MONITOR_INTERVAL_MINUTES", "10")).strip(),
        "ENABLE_YESTERDAY_FINAL": data.get("ENABLE_YESTERDAY_FINAL", "true").strip().lower(),
        "YESTERDAY_FINAL_TIME": data.get("YESTERDAY_FINAL_TIME", "00:10").strip(),
        "BACKSCAN_ENABLED": "1" if str(data.get("BACKSCAN_ENABLED", "true")).strip().lower() in ("true", "1") else "0",
        "BACKSCAN_DAYS": str(data.get("BACKSCAN_DAYS", "30")).strip(),
        "BACKSCAN_MIN_DELAY": str(data.get("BACKSCAN_MIN_DELAY", "2")).strip(),
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


    # 若在 Windows 环境，尝试同步更新系统计划任务触发间隔
    try:
        interval_min = int(data.get("MONITOR_INTERVAL_MINUTES", 10))
        if sys.platform.startswith("win"):
            ps_cmd = f"$t = Get-ScheduledTask -TaskName 'ZtbCollector_Sync' -ErrorAction SilentlyContinue; if ($t) {{ $t.Triggers[0].Repetition.Interval = 'PT{interval_min}M'; $t | Set-ScheduledTask | Out-Null }}"
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], check=False)
    except Exception:
        pass

    # 重新载入 config
    if config:
        import importlib
        if hasattr(config, "_ENV"):
            config._ENV = config.load_env_file()
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

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path == "/" or path == "/index.html":
            index_file = STATIC_DIR / "index.html"
            if not index_file.exists():
                for cand in [Path(__file__).resolve().parent / "static" / "index.html", ROOT_DIR / "manager" / "static" / "index.html"]:
                    if cand.exists():
                        index_file = cand
                        break
            if index_file.exists():
                content = index_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                fallback_html = f"<html><head><meta charset=\"utf-8\"><title>招投标监控服务运行中</title></head><body style=\"font-family:sans-serif;padding:30px;\"><h2 style=\"color:#1677ff;\">招投标系统控制面板已成功运行</h2><p>未找到静态 index.html 资源文件 (尝试路径: {STATIC_DIR})</p><p>后台 API 正常可用：</p><ul><li><a href=\"/api/status\">/api/status (系统状态)</a></li><li><a href=\"/api/task_state\">/api/task_state (运行进度)</a></li><li><a href=\"/preview\">/preview (大屏展示)</a></li></ul></body></html>"
                c = fallback_html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(c)))
                self.end_headers()
                self.wfile.write(c)
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

        if path == "/api/baseline_status":
            try:
                from scripts.scan_record_db import get_scan_record_db
                db = get_scan_record_db()
                stats = db.get_baseline_stats()
                self.send_json({"ok": True, "stats": stats})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e), "stats": {}})
            return

        if path == "/api/runs":
            # 获取运行历史记录与任务日志
            query = parse_qs(parsed.query)
            limit = min(int(query.get("limit", ["30"])[0]), 100)
            offset = max(int(query.get("offset", ["0"])[0]), 0)
            kind_filter = query.get("kind", [""])[0].strip()
            day_filter = query.get("day", [""])[0].strip()

            sql = "SELECT run_id, day, kind, started_at, finished_at, total, ok, failed, skipped, aborted, payload FROM runs"
            params = []
            where_clauses = []
            if kind_filter:
                where_clauses.append("kind = ?")
                params.append(kind_filter)
            if day_filter:
                where_clauses.append("day = ?")
                params.append(day_filter)
            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)
            sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            runs = []
            total_count = 0
            try:
                import json as _json
                with sqlite3.connect(config.DB_PATH) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    count_sql = "SELECT COUNT(*) FROM runs"
                    if where_clauses:
                        count_sql += " WHERE " + " AND ".join(where_clauses)
                    cur.execute(count_sql, params[:-2] if where_clauses else [])
                    total_count = cur.fetchone()[0]
                    cur.execute(sql, params)
                    for r in cur.fetchall():
                        d = dict(r)
                        if d.get("payload"):
                            try:
                                d["payload"] = _json.loads(d["payload"])
                            except Exception:
                                pass
                        runs.append(d)
            except Exception as e:
                runs = []
            self.send_json({"ok": True, "total": total_count, "runs": runs})
            return

        # 预览静态 dist / preview 中的展示大屏与日报文件
        is_preview = path == "/preview" or path.startswith("/preview/")
        is_dist = path == "/dist" or path.startswith("/dist/")
        is_assets = path.startswith("/assets/")

        if is_preview or is_dist or is_assets:
            site_dir = get_site_dir()
            if is_preview:
                rel = path[8:].lstrip("/") if path.startswith("/preview/") else "index.html"
            elif is_dist:
                rel = path[5:].lstrip("/") if path.startswith("/dist/") else "index.html"
            else:
                rel = path.lstrip("/")

            if not rel:
                rel = "index.html"

            try:
                target = (site_dir / rel).resolve()
                if str(target).startswith(str(site_dir.resolve())) and target.is_file():
                    mime = "text/html; charset=utf-8" if target.suffix.lower() == ".html" else                            "text/css; charset=utf-8" if target.suffix.lower() == ".css" else                            "application/javascript; charset=utf-8" if target.suffix.lower() == ".js" else                            mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                    data = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            except Exception:
                pass

            if rel.endswith(".html") or not rel or "." not in rel:
                tips_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>页面尚未生成</title><meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}}
.card{{background:#fff;padding:32px 40px;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,0.06);max-width:480px;text-align:center;}}
h2{{margin-top:0;color:#1e293b;font-size:20px;}}p{{color:#64748b;font-size:14px;line-height:1.6;margin:16px 0 24px;}}
.btn{{display:inline-block;padding:10px 20px;background:#0284c7;color:#fff;text-decoration:none;border-radius:6px;font-size:14px;font-weight:500;}}
.btn:hover{{background:#0369a1;}}</style></head>
<body><div class="card"><h2>展示页面暂未生成</h2><p>找不到目标文件 <code>{rel}</code>。<br>请先返回控制面板，点击【立即采集一次】以生成今日大屏与日报数据。</p><a class="btn" href="/">返回控制台</a></div></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(tips_html.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(tips_html.encode("utf-8"))
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
                cmd = [py_exe, "-u", str(ROOT_DIR / "run_daily.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"全流程日报流水线 ({target_date})", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_yesterday_final":
            target_date = post_data.get("date", "").strip()
            if not target_date:
                target_date = (date.today() - timedelta(days=1)).isoformat()
            if collector_exe:
                cmd = [str(collector_exe), "--date", target_date, "--final", "--force"]
            else:
                cmd = [py_exe, "-u", str(ROOT_DIR / "run_daily.py"), "--date", target_date, "--final", "--force"]
            ok, msg = PROC_MGR.start_task(f"昨日标讯最终版扫描与封存流水线 ({target_date})", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_upgrade":
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "upgrade_app.py")]
            ok, msg = PROC_MGR.start_task("同步拉取 GitHub 最新版本代码", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_collect":
            target_date = post_data.get("date", "").strip() or date.today().isoformat()
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "collect.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"单步采集公告 ({target_date})", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_collect_cz":
            target_date = post_data.get("date", "").strip() or date.today().isoformat()
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "collect_cz_ygcg.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"单采崇左阳光采购 ({target_date})", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_build":
            target_date = post_data.get("date", "").strip() or date.today().isoformat()
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "build_daily_page.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"重新构建静态页面 ({target_date})", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_batch_scan":
            start_date = post_data.get("start_date", "").strip()
            end_date = post_data.get("end_date", "").strip()
            if not start_date or not end_date:
                self.send_json({"ok": False, "msg": "请同时指定起始补扫日期与截止补扫日期！"})
                return
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "batch_scan.py"), "--start-date", start_date, "--end-date", end_date]
            ok, msg = PROC_MGR.start_task(f"历史区间数据补扫 ({start_date} 至 {end_date})", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_backscan":
            days = int(post_data.get("days", getattr(config, "BACKSCAN_DAYS", 30)))
            min_delay = int(post_data.get("min_delay", getattr(config, "BACKSCAN_MIN_DELAY", 2)))
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "backscan_delayed.py"), "--days", str(days), "--min-delay", str(min_delay)]
            ok, msg = PROC_MGR.start_task(f"历史公告回扫比对 (排查过去 {days} 天滞后公开公告)", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/rebuild_baseline":
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "scan_record_db.py"), "--rebuild-baseline"]
            ok, msg = PROC_MGR.start_task("同步与校准底边扫描数据库基线", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/delete_date":
            # 支持批量删除：可接收 dates 列表、或 date 单一日期、或 start_date / end_date 区间
            target_dates = []
            if isinstance(post_data.get("dates"), list):
                for d in post_data["dates"]:
                    d = str(d).strip()
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", d) and d not in target_dates:
                        target_dates.append(d)

            single_date = post_data.get("date", "").strip()
            if single_date and re.match(r"^\d{4}-\d{2}-\d{2}$", single_date) and single_date not in target_dates:
                target_dates.append(single_date)

            start_date = post_data.get("start_date", "").strip()
            end_date = post_data.get("end_date", "").strip()
            if start_date and end_date and re.match(r"^\d{4}-\d{2}-\d{2}$", start_date) and re.match(r"^\d{4}-\d{2}-\d{2}$", end_date):
                from datetime import datetime, timedelta
                try:
                    dt_s = datetime.strptime(start_date, "%Y-%m-%d")
                    dt_e = datetime.strptime(end_date, "%Y-%m-%d")
                    if dt_s > dt_e:
                        dt_s, dt_e = dt_e, dt_s
                    cur = dt_s
                    while cur <= dt_e:
                        d_str = cur.strftime("%Y-%m-%d")
                        if d_str not in target_dates:
                            target_dates.append(d_str)
                        cur += timedelta(days=1)
                except Exception:
                    pass

            if not target_dates:
                self.send_json({"ok": False, "msg": "请提供有效的删除日期 (YYYY-MM-DD) 或日期列表/区间"})
                return

            deleted_files_total = []
            site_dir = get_site_dir()
            db_file = getattr(config, "DB_PATH", ROOT_DIR / "data" / "gxzb.sqlite3")

            for target_date in target_dates:
                # 1. 删除 HTML 文件
                for html_cand in [site_dir / f"{target_date}.html", ROOT_DIR / "dist" / f"{target_date}.html"]:
                    if html_cand.exists():
                        try:
                            html_cand.unlink()
                            deleted_files_total.append(html_cand.name)
                        except Exception:
                            pass

                # 2. 删除 JSON 数据文件
                for p in [
                    getattr(config, "COLLECT_DIR", ROOT_DIR / "data" / "collect") / f"{target_date}.json",
                    getattr(config, "DAILY_DIR", ROOT_DIR / "data" / "daily") / f"{target_date}.json",
                    getattr(config, "DAILY_DIR", ROOT_DIR / "data" / "daily") / f"{target_date}.meta.json",
                    ROOT_DIR / "data" / f"{target_date}.json",
                    ROOT_DIR / "dist" / "logs" / f"{target_date}.jsonl",
                    ROOT_DIR / "dist" / "reports" / f"collect-reconcile-{target_date}.json",
                ]:
                    if p.exists():
                        try:
                            p.unlink()
                            deleted_files_total.append(str(p.name))
                        except Exception:
                            pass

                # 3. 清理数据库记录
                if db_file.exists():
                    try:
                        import sqlite3
                        conn = sqlite3.connect(db_file)
                        cur = conn.cursor()
                        # 清理 notice_fields
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notice_fields'")
                        if cur.fetchone():
                            cur.execute("DELETE FROM notice_fields WHERE infoid IN (SELECT infoid FROM notices WHERE date = ? OR pub_time LIKE ?)", (target_date, f"{target_date}%"))
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notices'")
                        if cur.fetchone():
                            cur.execute("DELETE FROM notices WHERE date = ? OR pub_time LIKE ?", (target_date, f"{target_date}%"))
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'")
                        if cur.fetchone():
                            cur.execute("DELETE FROM runs WHERE day = ?", (target_date,))
                        conn.commit()
                        conn.close()
                    except Exception as e_db:
                        print(f"[警告] 删除数据库记录异常: {e_db}")

            # 4. 重新构建历史归档总索引 (自动剔除已删除日期并重构 index.html 与 archive.json)
            try:
                build_archive = ROOT_DIR / "scripts" / "build_archive_page.py"
                if build_archive.exists():
                    subprocess.run([py_exe, "-u", str(build_archive)], capture_output=True)
            except Exception as e_arc:
                print(f"[警告] 重建归档总索引异常: {e_arc}")

            # 5. 自动同步 VPS 云端站点（通过 rsync --delete 彻底清理云端已删除文件并更新首页）
            vps_synced = False
            if getattr(config, "AUTO_UPLOAD_VPS", True):
                try:
                    upload_script = ROOT_DIR / "scripts" / "upload_vps.py"
                    if upload_script.exists():
                        res_up = subprocess.run([py_exe, "-u", str(upload_script)], capture_output=True, text=True)
                        vps_synced = (res_up.returncode == 0)
                except Exception as e_vps:
                    print(f"[警告] 同步 VPS 异常: {e_vps}")

            vps_text = "，并已彻底同步清理 VPS 云端站点" if vps_synced else ""
            target_desc = f"{len(target_dates)} 个日期 ({', '.join(target_dates[:3])}{'...' if len(target_dates) > 3 else ''})"
            self.send_json({
                "ok": True,
                "msg": f"已彻底删除 {target_desc} 的全部数据与日报页面，首页归档已自动同步刷新{vps_text}！",
                "deleted_dates": target_dates,
                "deleted_files": deleted_files_total
            })
            return

        if path == "/api/refresh_index":
            try:
                build_archive = ROOT_DIR / "scripts" / "build_archive_page.py"
                res_arc = subprocess.run([py_exe, "-u", str(build_archive)], capture_output=True, text=True)
                if res_arc.returncode != 0:
                    self.send_json({"ok": False, "msg": f"重新构建首页索引失败: {res_arc.stderr}"})
                    return

                vps_msg = ""
                if getattr(config, "AUTO_UPLOAD_VPS", True):
                    upload_script = ROOT_DIR / "scripts" / "upload_vps.py"
                    if upload_script.exists():
                        res_vps = subprocess.run([py_exe, "-u", str(upload_script)], capture_output=True, text=True)
                        if res_vps.returncode == 0:
                            vps_msg = "，并已同步发布至 VPS 云端站点"
                        else:
                            vps_msg = f" (VPS 同步输出: {res_vps.stderr or res_vps.stdout})"

                self.send_json({"ok": True, "msg": f"首页归档索引已重新计算生成{vps_msg}！"})
            except Exception as e:
                self.send_json({"ok": False, "msg": f"刷新首页异常: {str(e)}"})
            return

        if path == "/api/run_reapply":
            target_date = post_data.get("date", "").strip()
            reapply_all = post_data.get("all", False)
            if reapply_all or not target_date:
                cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "reapply_rules.py"), "--all"]
                task_title = "按最新重点规则重新标注并重建所有历史数据"
            else:
                cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "reapply_rules.py"), "--date", target_date]
                task_title = f"按最新重点规则重新标注历史数据 ({target_date})"
            ok, msg = PROC_MGR.start_task(task_title, cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_upload":
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "upload_vps.py")]
            ok, msg = PROC_MGR.start_task("同步上传静态文件至 VPS", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/test_ssh":
            host = post_data.get("host", "").strip() or getattr(config, "VPS_HOST", "217.142.149.2")
            port = str(post_data.get("port", "").strip() or getattr(config, "VPS_PORT", "22"))
            user = post_data.get("user", "").strip() or getattr(config, "VPS_USER", "root")
            key_path = post_data.get("key_path", "").strip() or getattr(config, "VPS_KEY_PATH", "")

            ssh_cmd = ["ssh", "-p", port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no"]
            if not key_path:
                local_key1 = ROOT_DIR / "data" / "id_rsa"
                local_key2 = ROOT_DIR / "data" / "vps_key.pem"
                if local_key1.is_file():
                    key_path = str(local_key1)
                elif local_key2.is_file():
                    key_path = str(local_key2)
            if key_path and Path(key_path).expanduser().is_file():
                ssh_cmd.extend(["-i", str(Path(key_path).expanduser().resolve())])
            ssh_cmd.extend([f"{user}@{host}", "echo SSH_TEST_OK"])

            try:
                res = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=12)
                if res.returncode == 0 and "SSH_TEST_OK" in res.stdout:
                    self.send_json({"ok": True, "msg": f"SSH 连通成功！已成功登录 {user}@{host}:{port}。"})
                else:
                    err_msg = res.stderr.strip() or res.stdout.strip() or "登录鉴权失败"
                    def_key = str(Path.home() / ".ssh" / "id_rsa")
                    self.send_json({
                        "ok": False,
                        "msg": f"SSH 连接失败: {err_msg}。\n\n【排查建议】\n1. 请检查私钥是否放置于：{def_key}\n2. 或在下方【指定私钥路径】填入私钥文件的绝对路径\n3. 确保云端服务器 ~/.ssh/authorized_keys 中已添加对应公钥。"
                    })
            except Exception as e:
                self.send_json({"ok": False, "msg": f"测试执行异常: {str(e)}"})
            return

        if path == "/api/test_wechat":
            cmd = [py_exe, "-u", "-c", "import sys; sys.path.insert(0, '.'); from scripts.notify_wechat import test_push; sys.exit(0 if test_push() else 1)"]
            ok, msg = PROC_MGR.start_task("测试微信服务号推送", cmd)
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/monitor_control":
            action = post_data.get("action", "").strip()
            if sys.platform != "win32":
                self.send_json({"ok": True, "msg": f"当前系统非 Windows，已模拟设置监控状态为: {action}"})
                return
            try:
                if action == "enable":
                    cp = subprocess.run(["schtasks", "/change", "/tn", "ZtbCollector_Sync", "/enable"], capture_output=True, timeout=5)
                    if cp.returncode == 0:
                        self.send_json({"ok": True, "msg": "自动化监控已成功开启！系统将按周期自动轮询采集。"})
                    else:
                        err = cp.stderr.decode("gbk", errors="ignore") or cp.stdout.decode("gbk", errors="ignore")
                        self.send_json({"ok": False, "msg": f"开启监控失败: {err}"})
                elif action == "disable":
                    cp = subprocess.run(["schtasks", "/change", "/tn", "ZtbCollector_Sync", "/disable"], capture_output=True, timeout=5)
                    if cp.returncode == 0:
                        self.send_json({"ok": True, "msg": "自动化监控已暂停。定时任务已停止触发。"})
                    else:
                        err = cp.stderr.decode("gbk", errors="ignore") or cp.stdout.decode("gbk", errors="ignore")
                        self.send_json({"ok": False, "msg": f"暂停监控失败: {err}"})
                else:
                    self.send_json({"ok": False, "msg": "未知操作类型"})
            except Exception as e:
                self.send_json({"ok": False, "msg": f"执行失败: {str(e)}"}, status=500)
            return

        if path == "/api/open_screen":
            try:
                target_url = f"http://127.0.0.1:{PORT}/preview/"
                if sys.platform == "win32":
                    edge_paths = [
                        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
                    ]
                    launched = False
                    for p in edge_paths:
                        if Path(p).exists():
                            subprocess.Popen([p, f"--app={target_url}", "--start-maximized"])
                            launched = True
                            break
                    if not launched:
                        os.startfile(target_url)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", target_url])
                else:
                    subprocess.Popen(["xdg-open", target_url])
                self.send_json({"ok": True, "msg": "已在大屏展厅窗口打开实时大屏！"})
            except Exception as e:
                self.send_json({"ok": False, "msg": f"呼起大屏窗口失败: {str(e)}"}, status=500)
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


def run_server(host="0.0.0.0", port=PORT):
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer((host, port), ManagerHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass

def main():
    port = PORT
    if len(sys.argv) > 1:
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
            elif arg.isdigit():
                port = int(arg)
    print(f"==================================================")
    print(f"招投标数据中心控制台 (Tender Manager) 启动中...")
    print(f"本地访问地址: http://127.0.0.1:{port}")
    print(f"局域网访问:   http://0.0.0.0:{port}")
    print(f"按 Ctrl+C 可停止控制台服务")
    print(f"==================================================")
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(("0.0.0.0", port), ManagerHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n控制台服务已停止。")

if __name__ == "__main__":
    main()
