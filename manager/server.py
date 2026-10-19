# -*- coding: utf-8 -*-
"""
招投标数据采集监控与控制面板 (Tender Web Manager)
内置原生 HTTP 服务，无需安装 Flask/FastAPI 即可运行。
"""
import http.server
import hashlib
import hmac
import json
import logging
import logging.handlers
import mimetypes
import os
import random
import re
import shutil
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
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

try:
    from manager.user_db import (
        init_user_db, register_user, verify_user_login,
        generate_user_token, verify_user_token,
        get_user_favorites, add_user_favorite, remove_user_favorite,
        get_all_users_for_admin, get_login_history_for_admin,
        get_user_full_config, save_user_full_config,
        get_user_config_snapshots, get_user_config_snapshot_detail,
        rollback_user_config_snapshot, delete_user_config_snapshot
    )
    init_user_db()
except Exception as _e:
    pass

# 全盘检索索引缓存（供全站搜索与日历重点个性化聚合加速使用）
_SEARCH_INDEX_CACHE = {"mtime": 0, "path": "", "items": []}

def _get_cached_search_items() -> list:
    global _SEARCH_INDEX_CACHE
    p1 = Path(config.DATA_DIR) / "search_index.json"
    p2 = ROOT_DIR / "dist" / "search_index.json"
    target = p1 if p1.is_file() else (p2 if p2.is_file() else None)
    if not target:
        return []
    try:
        mtime = target.stat().st_mtime
        if _SEARCH_INDEX_CACHE["items"] and _SEARCH_INDEX_CACHE["mtime"] == mtime and _SEARCH_INDEX_CACHE["path"] == str(target):
            return _SEARCH_INDEX_CACHE["items"]
        with open(target, "r", encoding="utf-8") as f:
            items = json.load(f)
        _SEARCH_INDEX_CACHE["mtime"] = mtime
        _SEARCH_INDEX_CACHE["path"] = str(target)
        _SEARCH_INDEX_CACHE["items"] = items
        return items
    except Exception:
        return []

PORT = int(getattr(config, "MANAGER_PORT", os.environ.get("MANAGER_PORT", 8089)))

# ---------------------------------------------------------------------------
# 日志系统 ── 访问日志(access) + 操作审计(audit)
# 严格满足要求：访问 IP、请求时间、访问接口、UA、操作记录，保存 >= 6 个月，出事可溯源
# ---------------------------------------------------------------------------
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_DB_PATH = LOG_DIR / "web_logs.sqlite3"

def _make_logger(name: str, filename: str, fmt: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.handlers.TimedRotatingFileHandler(
            str(LOG_DIR / filename),
            when="midnight",
            interval=1,
            backupCount=185,  # 至少保存 185 天 (>= 6 个月)
            encoding="utf-8",
        )
        handler.suffix = "%Y-%m-%d"
        handler.setFormatter(logging.Formatter(fmt))
        logger.addHandler(handler)
    return logger

# 访问日志: [时间] IP=... METHOD=... PATH=... STATUS=... DURATION=...ms UA="..."
access_logger = _make_logger(
    "manager.access",
    "access.log",
    "%(message)s",
)

# 审计日志: [时间] IP=... ACTION=... STATUS=... PATH=... DETAIL="..." UA="..."
audit_logger = _make_logger(
    "manager.audit",
    "audit.log",
    "%(message)s",
)

_log_db_lock = threading.Lock()

def init_log_db():
    """初始化可溯源轻量级日志库，用于后台快速检索、过滤与统计"""
    try:
        with _log_db_lock:
            with sqlite3.connect(str(LOG_DB_PATH), timeout=10.0) as conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS web_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_type TEXT NOT NULL,        -- 'access' 或 'audit'
                    ip TEXT NOT NULL,              -- 访问 IP
                    method TEXT NOT NULL,          -- GET / POST 等
                    path TEXT NOT NULL,            -- 访问接口/路径
                    status TEXT NOT NULL,          -- 状态码或 SUCCESS / FAILED
                    action TEXT NOT NULL,          -- 操作类型 (LOGIN, SAVE_CONFIG, START_TASK 等)
                    detail TEXT NOT NULL,          -- 操作详情记录
                    ua TEXT NOT NULL,              -- 客户端 User-Agent
                    duration_ms INTEGER DEFAULT 0, -- 请求耗时(毫秒)
                    created_at TEXT NOT NULL       -- 请求时间 (YYYY-MM-DD HH:MM:SS)
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_web_logs_created ON web_logs(created_at);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_web_logs_type ON web_logs(log_type);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_web_logs_action ON web_logs(action);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_web_logs_ip ON web_logs(ip);")
    except Exception as e:
        print(f"[Warn] init_log_db error: {e}")

init_log_db()

def record_web_log(log_type: str, ip: str, method: str, path: str, status: str, action: str, detail: str, ua: str, duration_ms: int = 0, created_at: str = None):
    """双写落盘 SQLite，配合日志文件实现双重备份，超期自动清理"""
    if not created_at:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _log_db_lock:
            with sqlite3.connect(str(LOG_DB_PATH), timeout=5.0) as conn:
                conn.execute(
                    "INSERT INTO web_logs (log_type, ip, method, path, status, action, detail, ua, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (log_type, ip, method, path, str(status), action, detail, ua, duration_ms, created_at)
                )
                # 维持保存 >= 6 个月(185天)策略：每隔一定频率自动清理超期记录
                if random.random() < 0.01:
                    cutoff = (datetime.now() - timedelta(days=185)).strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("DELETE FROM web_logs WHERE created_at < ?", (cutoff,))
    except Exception:
        pass

def get_client_ip(handler) -> str:
    """提取客户端真实 IP，兼容反代/隧道头部 (X-Forwarded-For, X-Real-IP)"""
    xff = handler.headers.get("X-Forwarded-For")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[0]
    x_real = handler.headers.get("X-Real-IP")
    if x_real and x_real.strip():
        return x_real.strip()
    if getattr(handler, "client_address", None):
        return str(handler.client_address[0])
    return "127.0.0.1"

def get_user_agent(handler) -> str:
    """提取客户端 UA"""
    return handler.headers.get("User-Agent", "-")

def record_access_log(ip: str, method: str, path: str, status: int, duration_ms: int, ua: str):
    """记录 HTTP 访问日志"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now_str}] IP={ip} METHOD={method} PATH={path} STATUS={status} DURATION={duration_ms}ms UA=\"{ua}\""
    access_logger.info(line)
    record_web_log(
        log_type="access",
        ip=ip,
        method=method,
        path=path,
        status=str(status),
        action="-",
        detail=f"{method} {path} -> {status} ({duration_ms}ms)",
        ua=ua,
        duration_ms=duration_ms,
        created_at=now_str
    )

def record_audit_log(ip: str, action: str, status: str, path: str, detail: str, ua: str):
    """记录关键管理操作审计日志"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now_str}] IP={ip} ACTION={action} STATUS={status} PATH={path} DETAIL=\"{detail}\" UA=\"{ua}\""
    audit_logger.info(line)
    record_web_log(
        log_type="audit",
        ip=ip,
        method="POST" if not path.startswith("GET") else "GET",
        path=path,
        status=status,
        action=action,
        detail=detail,
        ua=ua,
        duration_ms=0,
        created_at=now_str
    )

def query_web_logs(log_type: str = "audit", keyword: str = "", action: str = "", date_start: str = "", date_end: str = "", limit: int = 50, offset: int = 0):
    """供后台查询与导出安全审计及访问日志"""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    where_clauses = []
    params = []

    if log_type and log_type != "all":
        where_clauses.append("log_type = ?")
        params.append(log_type)
    if action:
        where_clauses.append("action = ?")
        params.append(action)
    if keyword:
        where_clauses.append("(ip LIKE ? OR action LIKE ? OR path LIKE ? OR detail LIKE ? OR ua LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw, kw, kw])
    if date_start:
        where_clauses.append("created_at >= ?")
        params.append(f"{date_start} 00:00:00")
    if date_end:
        where_clauses.append("created_at <= ?")
        params.append(f"{date_end} 23:59:59")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    logs = []
    total = 0
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_requests = 0
    today_audits = 0
    today_failed = 0

    try:
        with _log_db_lock:
            with sqlite3.connect(str(LOG_DB_PATH), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                count_cur = conn.execute(f"SELECT COUNT(*) FROM web_logs {where_sql}", params)
                total = count_cur.fetchone()[0]

                cur = conn.execute(
                    f"SELECT id, log_type, ip, method, path, status, action, detail, ua, duration_ms, created_at FROM web_logs {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                    params + [limit, offset]
                )
                for r in cur.fetchall():
                    logs.append({
                        "id": r["id"],
                        "log_type": r["log_type"],
                        "ip": r["ip"],
                        "method": r["method"],
                        "path": r["path"],
                        "status": r["status"],
                        "action": r["action"],
                        "detail": r["detail"],
                        "ua": r["ua"],
                        "duration_ms": r["duration_ms"],
                        "created_at": r["created_at"],
                    })

                # 统计今日概览指标
                stat_cur = conn.execute("""
                    SELECT 
                        SUM(CASE WHEN log_type='access' THEN 1 ELSE 0 END) as req_cnt,
                        SUM(CASE WHEN log_type='audit' THEN 1 ELSE 0 END) as audit_cnt,
                        SUM(CASE WHEN status='FAILED' OR status LIKE '4%' OR status LIKE '5%' THEN 1 ELSE 0 END) as fail_cnt
                    FROM web_logs WHERE created_at >= ?
                """, (f"{today_str} 00:00:00",))
                row = stat_cur.fetchone()
                if row:
                    today_requests = row[0] or 0
                    today_audits = row[1] or 0
                    today_failed = row[2] or 0
    except Exception as e:
        print(f"[Warn] query_web_logs error: {e}")

    return {
        "total": total,
        "logs": logs,
        "limit": limit,
        "offset": offset,
        "summary": {
            "today_requests": today_requests,
            "today_audits": today_audits,
            "today_failed": today_failed,
            "retention_days": 185
        }
    }

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

def generate_auth_token(password: str) -> str:
    """生成基于时间戳与密码的 HMAC 鉴权 Token"""
    ts = int(time.time())
    salt = f"{password}:gxzb_secure_salt_2026"
    sig = hmac.new(salt.encode("utf-8"), f"admin:{ts}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{sig}.{ts}"

def verify_auth_token(token: str, password: str, max_age: int = 7 * 86400) -> bool:
    """校验 Token 的签名与有效期（默认 7 天）"""
    if not token or "." not in token:
        return False
    sig, ts_str = token.split(".", 1)
    try:
        ts = int(ts_str)
    except (ValueError, TypeError):
        return False
    if time.time() - ts > max_age:
        return False
    salt = f"{password}:gxzb_secure_salt_2026"
    expected_sig = hmac.new(salt.encode("utf-8"), f"admin:{ts}".encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected_sig)

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
        self.current_event_id = None

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
            # 自动提取关联的 event_id
            event_id = None
            if "--event-id" in cmd:
                try:
                    idx = cmd.index("--event-id")
                    if idx + 1 < len(cmd):
                        event_id = cmd[idx + 1].strip()
                except Exception:
                    pass
            self.current_event_id = event_id

        def _worker():
            eid = event_id
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
                    self.current_event_id = None
                self.append_log("-" * 50 + f"\n[{self.end_time}] 任务结束，退出代码: {rc}\n")
                if rc != 0 and eid:
                    try:
                        from scripts import task_event_db
                        task_event_db.heal_stale_running_events()
                    except Exception:
                        pass
            except Exception as e:
                with self.lock:
                    self.status = "error"
                    self.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.current_event_id = None
                self.append_log(f"\n[执行异常]: {str(e)}\n")
                if eid:
                    try:
                        from scripts import task_event_db
                        task_event_db.heal_stale_running_events()
                    except Exception:
                        pass

        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        return True, "任务已启动"

    def stop_task(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                    self.status = "error"
                    eid = self.current_event_id
                    self.current_event_id = None
                    self.append_log("\n[用户手动终止任务]\n")
                    if eid:
                        try:
                            from scripts import task_event_db
                            task_event_db.finish_event(eid, error_msg="用户手动终止任务")
                            task_event_db.heal_stale_running_events()
                        except Exception:
                            pass
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
                if self.current_event_id:
                    try:
                        from scripts import task_event_db
                        task_event_db.heal_stale_running_events()
                    except Exception:
                        pass
                    self.current_event_id = None
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

def find_git_cmd():
    import shutil
    cmd = shutil.which("git")
    if cmd:
        return cmd
    candidates = [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        r"D:\Program Files\Git\cmd\git.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return "git"

def get_git_info():
    """获取本地 Git 版本信息"""
    git_cmd = find_git_cmd()
    try:
        res = subprocess.run(
            [git_cmd, "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0:
            commit = res.stdout.strip()
            app_ver = getattr(config, "APP_VERSION", "v0.1.0")
            return {"commit": commit, "version": f"{app_ver} (#{commit})"}
    except Exception:
        pass
    return {"commit": "release", "version": getattr(config, "APP_VERSION", "v0.1.0")}

def check_app_upgrade():
    """
    检查当前本地版本与 GitHub 远程版本，获取差异提交列表与更新内容说明。
    """
    try:
        import importlib
        importlib.reload(config)
    except Exception:
        pass

    local_version = getattr(config, "APP_VERSION", "v0.1.0")
    version_file = ROOT_DIR / "version.json"
    if version_file.exists():
        try:
            with open(version_file, "r", encoding="utf-8") as vf:
                vdata = json.load(vf)
                if vdata.get("version"):
                    local_version = vdata.get("version")
        except Exception:
            pass

    local_info = {
        "version": local_version,
        "commit": "",
        "date": "",
        "message": "",
        "is_git": False
    }
    remote_info = {
        "version": local_version,
        "commit": "",
        "date": "",
        "message": ""
    }
    commits = []
    has_update = False
    behind_count = 0
    connected = False
    git_cmd = find_git_cmd()

    # 1. 本地 Git 版本与提交信息
    try:
        res = subprocess.run(
            [git_cmd, "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=4
        )
        if res.returncode == 0:
            local_info["commit"] = res.stdout.strip()
            local_info["is_git"] = True
            log_res = subprocess.run(
                [git_cmd, "log", "-1", "--pretty=format:%ad\t%s", "--date=short"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=4
            )
            if log_res.returncode == 0 and "\t" in log_res.stdout:
                parts = log_res.stdout.strip().split("\t", 1)
                local_info["date"] = parts[0]
                local_info["message"] = parts[1]
    except Exception:
        pass

    # 1.2 若非 Git 仓库或无法读取 Git，回退读取构建发布的 version.json
    if not local_info["commit"]:
        version_file = ROOT_DIR / "version.json"
        if version_file.exists():
            try:
                with open(version_file, "r", encoding="utf-8") as vf:
                    vdata = json.load(vf)
                    local_info["commit"] = vdata.get("commit", "")
                    local_info["date"] = vdata.get("date", "")
                    local_info["message"] = vdata.get("message", "")
                    local_info["version"] = vdata.get("version", local_info["version"])
            except Exception:
                pass

    # 2. 探测远程 origin/main 分支
    if local_info["is_git"]:
        try:
            f_res = subprocess.run(
                [git_cmd, "fetch", "origin", "main", "--quiet"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=6
            )
            if f_res.returncode == 0:
                connected = True
                r_commit_res = subprocess.run(
                    [git_cmd, "rev-parse", "--short", "origin/main"],
                    cwd=str(ROOT_DIR),
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                if r_commit_res.returncode == 0:
                    remote_info["commit"] = r_commit_res.stdout.strip()

                r_log_res = subprocess.run(
                    [git_cmd, "log", "-1", "origin/main", "--pretty=format:%ad\t%s", "--date=short"],
                    cwd=str(ROOT_DIR),
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                if r_log_res.returncode == 0 and "\t" in r_log_res.stdout:
                    parts = r_log_res.stdout.strip().split("\t", 1)
                    remote_info["date"] = parts[0]
                    remote_info["message"] = parts[1]

                # 尝试从 origin/main:version.json 或 origin/main:config.py 获取远程 APP_VERSION
                try:
                    v_res = subprocess.run(
                        [git_cmd, "show", "origin/main:version.json"],
                        cwd=str(ROOT_DIR),
                        capture_output=True,
                        text=True,
                        timeout=3
                    )
                    if v_res.returncode == 0:
                        v_dict = json.loads(v_res.stdout)
                        if v_dict.get("version"):
                            remote_info["version"] = v_dict["version"]
                    else:
                        c_res = subprocess.run(
                            [git_cmd, "show", "origin/main:config.py"],
                            cwd=str(ROOT_DIR),
                            capture_output=True,
                            text=True,
                            timeout=3
                        )
                        if c_res.returncode == 0:
                            m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', c_res.stdout)
                            if m:
                                remote_info["version"] = m.group(1)
                except Exception:
                    pass

                cnt_res = subprocess.run(
                    [git_cmd, "rev-list", "HEAD..origin/main", "--count"],
                    cwd=str(ROOT_DIR),
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                if cnt_res.returncode == 0:
                    behind_count = int(cnt_res.stdout.strip() or "0")

                if behind_count > 0:
                    has_update = True
                    diff_res = subprocess.run(
                        [git_cmd, "log", "HEAD..origin/main", "--pretty=format:%h\t%an\t%ad\t%s", "--date=short"],
                        cwd=str(ROOT_DIR),
                        capture_output=True,
                        text=True,
                        timeout=4
                    )
                    if diff_res.returncode == 0 and diff_res.stdout.strip():
                        for line in diff_res.stdout.strip().split("\n"):
                            p = line.split("\t")
                            if len(p) >= 4:
                                commits.append({"hash": p[0], "author": p[1], "date": p[2], "message": p[3]})
                else:
                    recent_res = subprocess.run(
                        [git_cmd, "log", "-5", "--pretty=format:%h\t%an\t%ad\t%s", "--date=short"],
                        cwd=str(ROOT_DIR),
                        capture_output=True,
                        text=True,
                        timeout=4
                    )
                    if recent_res.returncode == 0 and recent_res.stdout.strip():
                        for line in recent_res.stdout.strip().split("\n"):
                            p = line.split("\t")
                            if len(p) >= 4:
                                commits.append({"hash": p[0], "author": p[1], "date": p[2], "message": p[3]})
        except Exception:
            pass

    # 3. 若 git fetch 未成功（网络或非 git 模式），优先尝试国内专线云镜像 (ztb.139771.xyz)
    if not connected:
        import urllib.request
        try:
            req = urllib.request.Request(
                f"https://ztb.139771.xyz/version.json?t={int(time.time())}",
                headers={"User-Agent": "GXZB-Updater/1.0"}
            )
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                if resp.status == 200:
                    v_remote = json.loads(resp.read().decode("utf-8"))
                    if isinstance(v_remote, dict) and v_remote.get("commit"):
                        connected = True
                        remote_info["source"] = "专线云镜像"
                        remote_info["commit"] = v_remote.get("commit", "")
                        remote_info["version"] = v_remote.get("version", remote_info["version"])
                        remote_info["date"] = v_remote.get("date", "")
                        remote_info["message"] = v_remote.get("message", "")
                        
                        if local_info["commit"] and remote_info["commit"] != local_info["commit"]:
                            has_update = True
                        elif not local_info["commit"]:
                            has_update = True
                        
                        r_commits = v_remote.get("commits", [])
                        if r_commits:
                            commits = []
                            for item in r_commits:
                                commits.append({
                                    "hash": item.get("commit", item.get("hash", ""))[:7],
                                    "author": item.get("author", "发布团队"),
                                    "date": item.get("date", ""),
                                    "message": item.get("message", "")
                                })
        except Exception:
            pass

    # 4. 若专线镜像未连通，备用尝试 GitHub API
    if not connected:
        import urllib.request
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/huangbiaocat/gxzb-daily/commits?per_page=6",
                headers={"User-Agent": "GXZB-Updater/1.0", "Accept": "application/vnd.github.v3+json"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    gh_data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(gh_data, list) and len(gh_data) > 0:
                        connected = True
                        remote_info["source"] = "GitHub"
                        top = gh_data[0]
                        remote_info["commit"] = top.get("sha", "")[:7]
                        remote_info["message"] = top.get("commit", {}).get("message", "").split("\n")[0]
                        remote_info["date"] = top.get("commit", {}).get("author", {}).get("date", "")[:10]

                        # 尝试从提交列表中提取最新版本号 (如 "feat(release): 升级至 v0.1.0")
                        for item in gh_data:
                            c_msg = item.get("commit", {}).get("message", "")
                            m = re.search(r'(v\d+\.\d+\.\d+)', c_msg)
                            if m:
                                remote_info["version"] = m.group(1)
                                break

                        # 尝试通过 jsdelivr 或 raw 快速读取云端最新 version.json
                        try:
                            req_v = urllib.request.Request(
                                "https://cdn.jsdelivr.net/gh/huangbiaocat/gxzb-daily@main/version.json",
                                headers={"User-Agent": "GXZB-Updater/1.0"}
                            )
                            with urllib.request.urlopen(req_v, timeout=2.5) as r_v:
                                if r_v.status == 200:
                                    v_parsed = json.loads(r_v.read().decode("utf-8"))
                                    if v_parsed.get("version"):
                                        remote_info["version"] = v_parsed["version"]
                        except Exception:
                            pass
                        
                        if local_info["commit"] and remote_info["commit"] != local_info["commit"]:
                            has_update = True
                        elif not local_info["commit"]:
                            has_update = True
                        
                        commits = []
                        for item in gh_data:
                            commits.append({
                                "hash": item.get("sha", "")[:7],
                                "author": item.get("commit", {}).get("author", {}).get("name", ""),
                                "date": item.get("commit", {}).get("author", {}).get("date", "")[:10],
                                "message": item.get("commit", {}).get("message", "").split("\n")[0]
                            })
        except Exception:
            pass

    # 生成状态提示语
    if connected:
        src_name = remote_info.get("source", "升级服务源")
        if has_update:
            msg = f"检测到 {src_name} 有新版本可用（最新 #{remote_info['commit']}），建议升级。"
        else:
            msg = f"本地运行版本已与 {src_name} 保持完全一致，当前无需升级。"
    else:
        remote_info["commit"] = "未知"
        remote_info["message"] = "无法连接 GitHub 检查最新提交（网络超时或未联网）"
        msg = "无法连接 GitHub 检查最新版本（网络超时或未联网）。如需同步，可尝试强制拉取。"

    return {
        "ok": True,
        "connected": connected,
        "has_update": has_update,
        "behind_count": behind_count,
        "current_version": local_info,
        "remote_version": remote_info,
        "commits": commits,
        "message": msg
    }

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
        if sys.platform.startswith("linux"):
            try:
                cp = subprocess.run(["systemctl", "show", "gxzb-daily.timer", "--property=NextElapseUSecRealtime,ActiveState,UnitFileState"], capture_output=True, text=True, timeout=2)
                if cp.returncode == 0:
                    info_dict = {}
                    for line in cp.stdout.splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            info_dict[k.strip()] = v.strip()
                    is_active = info_dict.get("ActiveState") == "active"
                    next_run_raw = info_dict.get("NextElapseUSecRealtime", "")
                    next_run_std = ""
                    match = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", next_run_raw)
                    if match:
                        next_run_std = match.group(1)
                    return {
                        "exists": True,
                        "enabled": is_active,
                        "state": "就绪 (监控中)" if is_active else "已暂停 / 未启用",
                        "next_run": next_run_std or next_run_raw or "--",
                        "next_run_standard": next_run_std,
                        "yesterday_task_exists": True,
                        "yesterday_task_enabled": True
                    }
            except Exception:
                pass
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

def get_live_server_metrics(log_source: str = "active", log_lines: int = 120) -> dict:
    """实时采集服务器系统底层指标、活跃进程与业务状态以及多源日志"""
    now = time.time()

    # 1. 运行时间与开机时间
    uptime_sec = 0.0
    if Path("/proc/uptime").exists():
        try:
            with open("/proc/uptime", "r", encoding="utf-8") as f:
                uptime_sec = float(f.read().split()[0])
        except Exception:
            pass
    if not uptime_sec:
        try:
            out = subprocess.check_output(["sysctl", "-n", "kern.boottime"], timeout=1).decode("utf-8", errors="ignore")
            sec = int(out.split("sec =")[1].split(",")[0].strip())
            uptime_sec = max(0.0, now - sec)
        except Exception:
            uptime_sec = 3600.0

    boot_dt = datetime.now() - timedelta(seconds=uptime_sec)
    boot_time_str = boot_dt.strftime("%Y-%m-%d %H:%M:%S")
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    mins = int((uptime_sec % 3600) // 60)
    secs = int(uptime_sec % 60)
    if days > 0:
        uptime_str = f"{days}天{hours}小时{mins}分"
    elif hours > 0:
        uptime_str = f"{hours}小时{mins}分"
    else:
        uptime_str = f"{mins}分{secs}秒"

    is_recently_booted = uptime_sec < 900  # 15分钟内刚开机或刚重启

    # 2. 负载与 CPU
    load_avg = [round(x, 2) for x in os.getloadavg()]
    cpu_cores = os.cpu_count() or 1
    # 估算 CPU 综合负载百分比 (基于 1 分钟 load / 核心数)
    cpu_percent = min(100.0, round((load_avg[0] / max(1, cpu_cores)) * 100.0, 1))

    # 3. 内存与 Swap
    mem_info = {"total_mb": 0, "used_mb": 0, "avail_mb": 0, "percent": 0.0}
    swap_info = {"total_mb": 0, "used_mb": 0, "percent": 0.0}
    if Path("/proc/meminfo").exists():
        try:
            mem = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    p = line.split(":")
                    if len(p) == 2:
                        mem[p[0].strip()] = int(p[1].strip().split()[0])
            t = mem.get("MemTotal", 0)
            a = mem.get("MemAvailable", mem.get("MemFree", 0))
            u = max(0, t - a)
            mem_info = {
                "total_mb": t // 1024,
                "used_mb": u // 1024,
                "avail_mb": a // 1024,
                "percent": round(u / max(1, t) * 100.0, 1)
            }
            st = mem.get("SwapTotal", 0)
            sf = mem.get("SwapFree", 0)
            su = max(0, st - sf)
            swap_info = {
                "total_mb": st // 1024,
                "used_mb": su // 1024,
                "percent": round(su / max(1, st) * 100.0, 1) if st else 0.0
            }
        except Exception:
            pass
    if mem_info["total_mb"] == 0:
        mem_info = {"total_mb": 2048, "used_mb": 512, "avail_mb": 1536, "percent": 25.0}

    # 4. 磁盘
    try:
        du = shutil.disk_usage(get_site_dir())
        disk_info = {
            "total_gb": round(du.total / (1024**3), 1),
            "used_gb": round(du.used / (1024**3), 1),
            "free_gb": round(du.free / (1024**3), 1),
            "percent": round(du.used / max(1, du.total) * 100.0, 1)
        }
    except Exception:
        disk_info = {"total_gb": 40.0, "used_gb": 10.0, "free_gb": 30.0, "percent": 25.0}

    # 5. 核心活跃进程采集与业务识别
    procs = []
    top_active_action = None
    try:
        cmd = ['ps', '-eo', 'pid,%cpu,%mem,etime,stat,comm,args', '--sort=-%cpu'] if sys.platform != 'darwin' else ['ps', '-A', '-o', 'pid,%cpu,%mem,etime,state,comm,command', '-r']
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2)
        text = p.stdout.decode('utf-8', errors='ignore')
        lines = text.strip().split('\n')
        for line in lines[1:25]:
            parts = line.split(None, 6)
            if len(parts) >= 7:
                pid, cpu, mem, etime, stat, comm, args = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]

                role = "other"
                tag = ""
                is_core = False
                color = "slate"

                args_lower = args.lower()
                if "run_daily.py" in args_lower:
                    role = "daily_task"
                    tag = "每日定时全流程采集"
                    is_core = True
                    color = "emerald"
                elif "collect.py" in args_lower:
                    role = "collector"
                    tag = "多源数据爬取中"
                    is_core = True
                    color = "sky"
                elif "build_archive_page.py" in args_lower:
                    role = "archive_builder"
                    tag = "全量日历归档构建"
                    is_core = True
                    color = "amber"
                elif "build_search_index.py" in args_lower:
                    role = "search_indexer"
                    tag = "全文检索索引生成"
                    is_core = True
                    color = "indigo"
                elif "build_search_page.py" in args_lower:
                    role = "search_builder"
                    tag = "全盘检索页面渲染"
                    is_core = True
                    color = "indigo"
                elif "build_daily_page.py" in args_lower:
                    role = "daily_builder"
                    tag = "每日静态快报渲染"
                    is_core = True
                    color = "cyan"
                elif "manager/server.py" in args_lower or "manager" in args_lower:
                    role = "manager"
                    tag = "控制台与终端监控API"
                    color = "violet"
                elif "nginx" in comm.lower() or "nginx" in args_lower:
                    role = "nginx"
                    tag = "Web静态服务与反代"
                    color = "teal"
                elif "cloudflared" in comm.lower() or "cloudflared" in args_lower:
                    role = "cloudflared"
                    tag = "安全穿透隧道 / CDN"
                    color = "orange"
                elif "1panel" in args_lower or "1panel" in comm.lower():
                    role = "1panel"
                    tag = "1Panel 容器运维面板"
                    color = "blue"

                if is_core and not top_active_action:
                    top_active_action = {
                        "tag": tag,
                        "pid": pid,
                        "cpu": cpu,
                        "mem": mem,
                        "time": etime,
                        "cmd": args
                    }

                procs.append({
                    "pid": pid,
                    "cpu": cpu,
                    "mem": mem,
                    "time": etime,
                    "stat": stat,
                    "comm": comm,
                    "args": args,
                    "role": role,
                    "tag": tag,
                    "is_core": is_core,
                    "color": color
                })
    except Exception:
        pass

    # 6. 提炼“服务器正在干什么”总结状态
    task_state = PROC_MGR.get_state() if 'PROC_MGR' in globals() else {"is_running": False}
    is_busy = False
    activity_summary = ""

    if top_active_action:
        is_busy = True
        activity_summary = f"🚀 正在执行【{top_active_action['tag']}】(PID: {top_active_action['pid']}, CPU: {top_active_action['cpu']}%, 已运行: {top_active_action['time']})"
    elif task_state.get("is_running") or task_state.get("is_busy"):
        is_busy = True
        cmd_text = task_state.get("running_command") or "后台任务执行中"
        activity_summary = f"⚡ 控制台正在运行任务: {cmd_text} (已耗时 {task_state.get('elapsed_seconds', 0)}秒)"
    else:
        activity_summary = "🟢 系统处于空闲待机状态，核心守护进程平稳运行中"

    # 7. 多源日志采集 (Live Log Streams)
    log_content = ""
    log_lines = min(max(int(log_lines), 20), 300)

    if log_source == "active":
        if task_state.get("is_running") or task_state.get("is_busy"):
            log_content = "\n".join(task_state.get("logs", []))
        else:
            try:
                cp = subprocess.run(["journalctl", "-u", "gxzb-daily", "-n", str(log_lines), "--no-pager"], capture_output=True, timeout=2)
                if cp.returncode == 0 and cp.stdout:
                    log_content = cp.stdout.decode("utf-8", errors="ignore")
            except Exception:
                pass
            if not log_content.strip():
                log_candidates = [ROOT_DIR / "run.log", ROOT_DIR / "logs" / "run_daily.log", ROOT_DIR / "logs" / "access.log"]
                for cand in log_candidates:
                    if cand.exists():
                        try:
                            lines = cand.read_text(encoding="utf-8", errors="ignore").splitlines()
                            log_content = "\n".join(lines[-log_lines:])
                            break
                        except Exception:
                            pass
    elif log_source == "daily":
        try:
            cp = subprocess.run(["journalctl", "-u", "gxzb-daily", "-n", str(log_lines), "--no-pager"], capture_output=True, timeout=2)
            if cp.returncode == 0 and cp.stdout:
                log_content = cp.stdout.decode("utf-8", errors="ignore")
        except Exception:
            pass
        if not log_content.strip():
            log_f = ROOT_DIR / "logs" / "run_daily.log"
            if log_f.exists():
                lines = log_f.read_text(encoding="utf-8", errors="ignore").splitlines()
                log_content = "\n".join(lines[-log_lines:])
    elif log_source == "service":
        try:
            cp = subprocess.run(["journalctl", "-u", "gxzb-web", "-n", str(log_lines), "--no-pager"], capture_output=True, timeout=2)
            if cp.returncode == 0 and cp.stdout:
                log_content = cp.stdout.decode("utf-8", errors="ignore")
        except Exception:
            pass
        if not log_content.strip():
            acc_f = ROOT_DIR / "logs" / "access.log"
            if acc_f.exists():
                lines = acc_f.read_text(encoding="utf-8", errors="ignore").splitlines()
                log_content = "\n".join(lines[-log_lines:])
    elif log_source == "access":
        acc_f = ROOT_DIR / "logs" / "access.log"
        if acc_f.exists():
            try:
                lines = acc_f.read_text(encoding="utf-8", errors="ignore").splitlines()
                log_content = "\n".join(lines[-log_lines:])
            except Exception:
                pass
    elif log_source == "system":
        try:
            cp = subprocess.run(["dmesg", "-T"], capture_output=True, timeout=2)
            if cp.returncode == 0 and cp.stdout:
                lines = cp.stdout.decode("utf-8", errors="ignore").splitlines()
                log_content = "\n".join(lines[-log_lines:])
        except Exception:
            pass

    if not log_content.strip():
        log_content = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 暂无对应日志输出或日志文件为空。"

    return {
        "ok": True,
        "timestamp": now,
        "time_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "system": {
            "uptime_sec": round(uptime_sec, 1),
            "uptime_str": uptime_str,
            "boot_time": boot_time_str,
            "is_recently_booted": is_recently_booted,
            "load_avg": load_avg,
            "cpu_cores": cpu_cores,
            "cpu_percent": cpu_percent,
            "memory": mem_info,
            "swap": swap_info,
            "disk": disk_info
        },
        "activity": {
            "is_busy": is_busy,
            "summary": activity_summary,
            "top_action": top_active_action
        },
        "processes": procs[:15],
        "log": {
            "source": log_source,
            "lines": log_lines,
            "content": log_content
        }
    }

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
        "is_on_vps": getattr(config, "IS_ON_VPS", False),
        "vps_host": getattr(config, "VPS_HOST", "8.134.69.22"),
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
        "focus_stats": {
            "projects_count": len(getattr(config, "FOCUS_PROJECTS", [])),
            "owners_count": len(getattr(config, "FOCUS_OWNERS", [])),
            "master_exists": (ROOT_DIR / "config" / "focus_rules_master.json").exists()
        },
        "vps": {
            "host": getattr(config, "VPS_HOST", "8.134.69.22"),
            "auto_upload": getattr(config, "AUTO_UPLOAD_VPS", False),
            "path": getattr(config, "VPS_PATH", "/opt/1panel/www/tender_site/"),
            "last_sync_time": (json.loads((ROOT_DIR / "data" / "last_vps_sync.json").read_text(encoding="utf-8")).get("last_sync_time", "") if (ROOT_DIR / "data" / "last_vps_sync.json").exists() else ""),
            "last_sync_status": (json.loads((ROOT_DIR / "data" / "last_vps_sync.json").read_text(encoding="utf-8")).get("status", "") if (ROOT_DIR / "data" / "last_vps_sync.json").exists() else ""),
            "last_sync_method": (json.loads((ROOT_DIR / "data" / "last_vps_sync.json").read_text(encoding="utf-8")).get("method", "") if (ROOT_DIR / "data" / "last_vps_sync.json").exists() else "")
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
                raw_env[k.strip()] = v.strip()

    res = {
        "FOCUS_KEYWORDS": getattr(config, "FOCUS_KEYWORDS", []),
        "FOCUS_KEYWORDS_STR": ",".join(getattr(config, "FOCUS_KEYWORDS", [])) if getattr(config, "FOCUS_KEYWORDS", None) else "",
        "FOCUS_PROJECTS": "\n".join(getattr(config, "FOCUS_PROJECTS", [])) if getattr(config, "FOCUS_PROJECTS", None) else "",
        "FOCUS_OWNERS": "\n".join(getattr(config, "FOCUS_OWNERS", [])) if getattr(config, "FOCUS_OWNERS", None) else "",
        "FOCUS_PROJECTS_COUNT": len(getattr(config, "FOCUS_PROJECTS", [])),
        "FOCUS_OWNERS_COUNT": len(getattr(config, "FOCUS_OWNERS", [])),
        "RULES_MASTER_AVAILABLE": (ROOT_DIR / "config" / "focus_rules_master.json").exists(),
        "FOCUS_PROJECT_TYPES": "\n".join(getattr(config, "FOCUS_PROJECT_TYPES", [])) if getattr(config, "FOCUS_PROJECT_TYPES", None) else "",
        "FOCUS_MIN_AMOUNT": getattr(config, "FOCUS_MIN_AMOUNT_RAW", ""),
        "AUTO_UPLOAD_VPS": "true" if getattr(config, "AUTO_UPLOAD_VPS", False) else "false",
        "VPS_HOST": getattr(config, "VPS_HOST", "8.134.69.22"),
        "VPS_PORT": getattr(config, "VPS_PORT", "22"),
        "VPS_USER": getattr(config, "VPS_USER", "root"),
        "VPS_PATH": getattr(config, "VPS_PATH", "/opt/1panel/www/tender_site/"),
        "HAS_ADMIN_PASSWORD": bool(getattr(config, "ADMIN_PASSWORD", "")),
        "ADMIN_PASSWORD": "",
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

def list_config_snapshots():
    backup_dir = ROOT_DIR / "data" / "backup" / "config"
    snapshots = []
    if backup_dir.exists():
        for f in sorted(backup_dir.glob("env_*.env"), reverse=True):
            snapshots.append({
                "filename": f.name,
                "time": f.stat().st_mtime,
                "time_str": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size": f.stat().st_size
            })
    return snapshots

def restore_config_snapshot(filename):
    backup_dir = ROOT_DIR / "data" / "backup" / "config"
    target = backup_dir / filename
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"快照文件不存在: {filename}")
    env_file = ROOT_DIR / ".env"
    shutil.copy2(target, env_file)
    rule_snap = backup_dir / filename.replace("env_", "rules_").replace(".env", ".json")
    if rule_snap.exists():
        shutil.copy2(rule_snap, ROOT_DIR / "data" / "focus_rules.json")
    
    if config:
        import importlib
        if hasattr(config, "_ENV"):
            config._ENV = config.load_env_file()
        importlib.reload(config)
    return True

def create_config_snapshot():
    backup_dir = ROOT_DIR / "data" / "backup" / "config"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        raise FileNotFoundError("未找到 .env 配置文件，无法创建快照")
    snap_name = f"env_{ts}.env"
    shutil.copy2(env_file, backup_dir / snap_name)
    rules_path = ROOT_DIR / "data" / "focus_rules.json"
    if rules_path.exists():
        shutil.copy2(rules_path, backup_dir / f"rules_{ts}.json")
    return snap_name

def delete_config_snapshot(filename):
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError("非法的快照文件名")
    if not filename.startswith("env_") or not filename.endswith(".env"):
        raise ValueError("仅允许删除 env_*.env 格式的配置快照")
    backup_dir = ROOT_DIR / "data" / "backup" / "config"
    target = backup_dir / filename
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"快照文件不存在: {filename}")
    target.unlink()
    rule_snap = backup_dir / filename.replace("env_", "rules_").replace(".env", ".json")
    if rule_snap.exists() and rule_snap.is_file():
        rule_snap.unlink()
    return True

def restore_master_focus_rules():
    master_path = ROOT_DIR / "config" / "focus_rules_master.json"
    if not master_path.exists():
        raise FileNotFoundError("未找到规则母库文件 config/focus_rules_master.json")
    master_data = json.loads(master_path.read_text(encoding="utf-8"))
    
    rules_path = ROOT_DIR / "data" / "focus_rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(json.dumps(master_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    prjs = [p["name"] for p in master_data.get("projects", []) if isinstance(p, dict) and p.get("name")]
    owns = [o["name"] for o in master_data.get("owners", []) if isinstance(o, dict) and o.get("name")]
    
    # 读取现有 env
    env_file = ROOT_DIR / ".env"
    env_text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    p_val = ",".join(prjs)
    o_val = ",".join(owns)
    if "FOCUS_PROJECTS=" in env_text:
        env_text = re.sub(r"^FOCUS_PROJECTS=.*$", f"FOCUS_PROJECTS={p_val}", env_text, flags=re.MULTILINE)
    else:
        env_text += f"\nFOCUS_PROJECTS={p_val}\n"
    if "FOCUS_OWNERS=" in env_text:
        env_text = re.sub(r"^FOCUS_OWNERS=.*$", f"FOCUS_OWNERS={o_val}", env_text, flags=re.MULTILINE)
    else:
        env_text += f"\nFOCUS_OWNERS={o_val}\n"
    
    backup_dir = ROOT_DIR / "data" / "backup" / "config"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if env_file.exists():
        shutil.copy2(env_file, backup_dir / f"env_{ts}.env")
        shutil.copy2(env_file, ROOT_DIR / ".env.bak")
    env_file.write_text(env_text, encoding="utf-8")
    
    if config:
        import importlib
        if hasattr(config, "_ENV"):
            config._ENV = config.load_env_file()
        importlib.reload(config)
        
    return {
        "project_count": len(prjs),
        "owner_count": len(owns),
        "message": f"已成功从规则母库恢复 {len(prjs)} 项重点工程与 {len(owns)} 家重点业主！"
    }

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

    # 1. 自动快照备份：修改前自动归档当前 .env 和 rules
    backup_dir = ROOT_DIR / "data" / "backup" / "config"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if env_file.exists():
        shutil.copy2(env_file, backup_dir / f"env_{ts}.env")
        shutil.copy2(env_file, ROOT_DIR / ".env.bak")

    # 解析重点项目与重点业主，支持换行、中英文逗号
    raw_p = data.get("FOCUS_PROJECTS", "").replace("\r\n", "\n").replace("，", ",")
    if "\n" in raw_p:
        p_list = [p.strip() for p in raw_p.split("\n") if p.strip()]
    else:
        p_list = [p.strip() for p in raw_p.split(",") if p.strip()]

    raw_o = data.get("FOCUS_OWNERS", "").replace("\r\n", "\n").replace("，", ",")
    if "\n" in raw_o:
        o_list = [o.strip() for o in raw_o.split("\n") if o.strip()]
    else:
        o_list = [o.strip() for o in raw_o.split(",") if o.strip()]

    # 防意外骤减熔断拦截保护
    curr_prjs = getattr(config, "FOCUS_PROJECTS", []) if config else []
    if len(curr_prjs) >= 10 and len(p_list) < 5 and not data.get("confirm_shrink"):
        raise ValueError(f"安全熔断拦截：当前已有 {len(curr_prjs)} 项重点项目，提交的新项目仅 {len(p_list)} 项，疑似表单误清空。如确认缩减，请勾选确认缩减。")

    # 同步维护持久化底库 data/focus_rules.json
    rules_path = ROOT_DIR / "data" / "focus_rules.json"
    master_path = ROOT_DIR / "config" / "focus_rules_master.json"
    base_meta = {}
    if rules_path.exists():
        try:
            base_meta = json.loads(rules_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    elif master_path.exists():
        try:
            base_meta = json.loads(master_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    existing_prj_map = {p["name"]: p for p in base_meta.get("projects", []) if isinstance(p, dict) and "name" in p}
    new_projects_meta = []
    for p_name in p_list:
        if p_name in existing_prj_map:
            new_projects_meta.append(existing_prj_map[p_name])
        else:
            new_projects_meta.append({
                "name": p_name,
                "aliases": [p_name],
                "category": "自定义重点",
                "priority": "P1",
                "dept": "用户配置"
            })

    existing_own_map = {o["name"]: o for o in base_meta.get("owners", []) if isinstance(o, dict) and "name" in o}
    new_owners_meta = []
    for o_name in o_list:
        if o_name in existing_own_map:
            new_owners_meta.append(existing_own_map[o_name])
        else:
            new_owners_meta.append({
                "name": o_name,
                "aliases": [o_name],
                "category": "自定义业主",
                "priority": "P1"
            })

    base_meta["projects"] = new_projects_meta
    base_meta["owners"] = new_owners_meta
    base_meta["project_count"] = len(new_projects_meta)
    base_meta["owner_count"] = len(new_owners_meta)
    base_meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(json.dumps(base_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(rules_path, backup_dir / f"rules_{ts}.json")

    keys_written = set()
    new_lines = []
    
    mapping = {
        "FOCUS_KEYWORDS": data.get("FOCUS_KEYWORDS_STR", "").strip(),
        "FOCUS_PROJECTS": ",".join(p_list),
        "FOCUS_OWNERS": ",".join(o_list),
        "FOCUS_PROJECT_TYPES": ",".join([p.strip() for p in data.get("FOCUS_PROJECT_TYPES", "").replace("\r\n", "\n").replace("，", ",").split("\n") if p.strip()]) if "\n" in data.get("FOCUS_PROJECT_TYPES", "") else data.get("FOCUS_PROJECT_TYPES", "").strip(),
        "FOCUS_MIN_AMOUNT": str(data.get("FOCUS_MIN_AMOUNT", "")).strip(),
        "AUTO_UPLOAD_VPS": str(data.get("AUTO_UPLOAD_VPS", getattr(config, "AUTO_UPLOAD_VPS", False))).strip().lower(),
        "VPS_HOST": data.get("VPS_HOST", getattr(config, "VPS_HOST", "8.134.69.22")).strip() or "8.134.69.22",
        "VPS_PORT": str(data.get("VPS_PORT", getattr(config, "VPS_PORT", "22"))).strip() or "22",
        "VPS_USER": data.get("VPS_USER", getattr(config, "VPS_USER", "root")).strip() or "root",
        "VPS_PATH": data.get("VPS_PATH", getattr(config, "VPS_PATH", "/opt/1panel/www/tender_site/")).strip() or "/opt/1panel/www/tender_site/",
        "VPS_KEY_PATH": data.get("VPS_KEY_PATH", getattr(config, "VPS_KEY_PATH", "")).strip(),
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

    admin_pw_input = str(data.get("ADMIN_PASSWORD", "")).strip()
    if admin_pw_input:
        mapping["ADMIN_PASSWORD"] = admin_pw_input

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
        elif sys.platform.startswith("linux"):
            timer_file = "/etc/systemd/system/gxzb-daily.timer"
            if os.path.exists(timer_file):
                content = f"""[Unit]
Description=Run GXZB Daily Task Periodically

[Timer]
OnCalendar=*-*-* 07..22:00/{interval_min}:00
Persistent=true

[Install]
WantedBy=timers.target
"""
                with open(timer_file, "w", encoding="utf-8") as tf:
                    tf.write(content)
                subprocess.run(["systemctl", "daemon-reload"], check=False)
                subprocess.run(["systemctl", "restart", "gxzb-daily.timer"], check=False)
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
    def handle_one_request(self):
        self._start_time = time.time()
        super().handle_one_request()

    def get_client_ip(self) -> str:
        return get_client_ip(self)

    def get_user_agent(self) -> str:
        return get_user_agent(self)

    def _audit(self, action: str, status: str, detail: str, path: str = None):
        p = path or getattr(self, "path", "-")
        record_audit_log(self.get_client_ip(), action, status, p, detail, self.get_user_agent())

    def log_request(self, code='-', size='-'):
        try:
            ip = self.get_client_ip()
            ua = self.get_user_agent()
            method = getattr(self, "command", "-")
            path = getattr(self, "path", "-")
            duration_ms = int((time.time() - getattr(self, "_start_time", time.time())) * 1000)
            status_code = int(code) if str(code).isdigit() else 200
            record_access_log(ip, method, path, status_code, duration_ms, ua)
        except Exception:
            pass

    def log_message(self, format, *args):
        # 静默控制台 stderr 刷屏，统一交由 access_logger 与 audit_logger 记录
        pass

    def log_error(self, format, *args):
        try:
            ip = self.get_client_ip()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            access_logger.error(f"[{now_str}] IP={ip} ERROR: {format % args}")
        except Exception:
            pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Auth-Token")
        super().end_headers()

    def is_authenticated(self) -> bool:
        """检查请求是否通过管理员鉴权（Cookie / Authorization / X-Auth-Token）"""
        admin_pw = getattr(config, "ADMIN_PASSWORD", "").strip() if config else ""
        if not admin_pw:
            return True
        # 1. Check Cookie
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            try:
                cookie = SimpleCookie()
                cookie.load(cookie_header)
                if "auth_token" in cookie:
                    token = cookie["auth_token"].value
                    if verify_auth_token(token, admin_pw):
                        return True
            except Exception:
                pass
        # 2. Check Authorization Header (Bearer <token>)
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
            if verify_auth_token(token, admin_pw):
                return True
        # 3. Check X-Auth-Token
        x_token = self.headers.get("X-Auth-Token", "").strip()
        if x_token and verify_auth_token(x_token, admin_pw):
            return True
        return False

    def get_current_user(self):
        """检查请求是否通过普通用户鉴权 (Cookie: user_token 或 Header: X-User-Token / Authorization)"""
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            try:
                cookie = SimpleCookie()
                cookie.load(cookie_header)
                if "user_token" in cookie:
                    u = verify_user_token(cookie["user_token"].value)
                    if u:
                        return u
            except Exception:
                pass
        x_tok = self.headers.get("X-User-Token", "").strip()
        if x_tok:
            u = verify_user_token(x_tok)
            if u:
                return u
        auth_hdr = self.headers.get("Authorization", "").strip()
        if auth_hdr.startswith("UserBearer "):
            u = verify_user_token(auth_hdr[11:].strip())
            if u:
                return u
        return None

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

    def serve_file(self, target, mime=None):
        """通用静态文件传输方法，支持自动探测 MIME 类型和 UTF-8 编码"""
        try:
            target_path = Path(target).resolve()
            if not target_path.is_file():
                self.send_error(404, f"File not found: {target_path.name}")
                return
            if not mime:
                ext = target_path.suffix.lower()
                if ext == ".html":
                    mime = "text/html; charset=utf-8"
                elif ext == ".css":
                    mime = "text/css; charset=utf-8"
                elif ext == ".js":
                    mime = "application/javascript; charset=utf-8"
                elif ext == ".json":
                    mime = "application/json; charset=utf-8"
                else:
                    mime = mimetypes.guess_type(str(target_path))[0] or "application/octet-stream"
            data = target_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, f"Error reading file: {str(e)}")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # -------------------------------------------------------------
        # Single Domain Portal 路由分流与管理后台入口
        # -------------------------------------------------------------
        if path == "/admin":
            self.send_response(301)
            self.send_header("Location", "/admin/")
            self.end_headers()
            return

        if path == "/admin/" or path == "/admin/index.html":
            mgr_index = STATIC_DIR / "index.html"
            if not mgr_index.exists():
                for cand in [Path(__file__).resolve().parent / "static" / "index.html", ROOT_DIR / "manager" / "static" / "index.html"]:
                    if cand.exists():
                        mgr_index = cand
                        break
            if mgr_index.exists():
                self.serve_file(mgr_index, "text/html")
            else:
                self.send_error(404, "Admin index.html not found")
            return

        if path in ("/terminal", "/terminal.html", "/admin/terminal", "/admin/terminal.html"):
            term_page = STATIC_DIR / "terminal.html"
            if not term_page.exists():
                for cand in [Path(__file__).resolve().parent / "static" / "terminal.html", ROOT_DIR / "manager" / "static" / "terminal.html"]:
                    if cand.exists():
                        term_page = cand
                        break
            if term_page.exists():
                self.serve_file(term_page, "text/html")
            else:
                self.send_error(404, "Terminal page not found")
            return

        if path.startswith("/admin/"):
            # 管理后台静态资源映射（例如 /admin/static/... 或 /admin/style.css）
            rel = path[len("/admin/"):]
            target = STATIC_DIR / rel
            if not target.exists() and rel.startswith("static/"):
                target = STATIC_DIR / rel[len("static/"):]
            if target.exists() and target.is_file():
                self.serve_file(target)
                return
            cand = STATIC_DIR / rel
            if cand.exists() and cand.is_file():
                self.serve_file(cand)
                return
            self.send_error(404, f"Admin asset not found: {rel}")
            return

        if path == "/" or path == "/index.html":
            # 控制台首页：默认优先提供管理控制台界面
            mgr_index = STATIC_DIR / "index.html"
            if not mgr_index.exists():
                for cand in [Path(__file__).resolve().parent / "static" / "index.html", ROOT_DIR / "manager" / "static" / "index.html"]:
                    if cand.exists():
                        mgr_index = cand
                        break
            if mgr_index.exists():
                self.serve_file(mgr_index, "text/html")
                return

            # 若无管理后台静态资源，兜底尝试情报大屏
            site_index = get_site_dir() / "index.html"
            if site_index.exists() and site_index.is_file():
                self.serve_file(site_index, "text/html")
                return
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

        if path == "/api/check_auth":
            admin_pw = getattr(config, "ADMIN_PASSWORD", "").strip() if config else ""
            self.send_json({"authenticated": self.is_authenticated(), "has_password": bool(admin_pw)})
            return

        # 普通用户鉴权与关注 API
        if path == "/api/user/me":
            user = self.get_current_user()
            if not user:
                self.send_json({"authenticated": False, "user": None})
            else:
                favs = get_user_favorites(user["id"])
                self.send_json({
                    "authenticated": True,
                    "user": user,
                    "favorites_count": {
                        "projects": len(favs["projects"]),
                        "owners": len(favs["owners"])
                    }
                })
            return

        if path == "/api/user/favorites":
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            favs = get_user_favorites(user["id"])
            self.send_json({"ok": True, "favorites": favs})
            return

        # 普通用户个性化跟踪规则（重点项目、重点业主、通用预警关键词、激活金额门槛）
        if path == "/api/user/config":
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            cfg = get_user_full_config(user["id"])
            self.send_json({"ok": True, "config": cfg})
            return

        # 普通用户定制日历重点聚合接口
        if path == "/api/user/calendar_focus":
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            cfg = get_user_full_config(user["id"])
            focus_projects = [p.strip() for p in cfg.get("focus_projects", []) if p and p.strip()]
            focus_owners = [o.strip() for o in cfg.get("focus_owners", []) if o and o.strip()]

            # 如果用户把关注都删了（或新用户从未设置过重点关注）
            if not focus_projects and not focus_owners:
                self.send_json({
                    "ok": True,
                    "empty": True,
                    "focus_projects_count": 0,
                    "focus_owners_count": 0,
                    "by_date": {},
                    "by_year": {},
                    "by_month": {},
                    "total_focus": 0
                })
                return

            # 如果用户有关注项，通过内存索引进行全库匹配
            items = _get_cached_search_items()
            proj_lower = [p.lower() for p in focus_projects]
            owner_lower = [o.lower() for o in focus_owners]

            by_date = {}
            by_year = {}
            by_month = {}
            total_focus = 0

            for it in items:
                title = (it.get("title") or "").lower()
                hit = False
                for p in proj_lower:
                    if p in title:
                        hit = True
                        break
                if not hit:
                    for o in owner_lower:
                        if o in title:
                            hit = True
                            break
                if hit:
                    d = it.get("date") or ""
                    if d and len(d) >= 10:
                        by_date[d] = by_date.get(d, 0) + 1
                        yr = d[:4]
                        mo = d[:7]
                        by_year[yr] = by_year.get(yr, 0) + 1
                        by_month[mo] = by_month.get(mo, 0) + 1
                        total_focus += 1

            self.send_json({
                "ok": True,
                "empty": False,
                "focus_projects_count": len(focus_projects),
                "focus_owners_count": len(focus_owners),
                "by_date": by_date,
                "by_year": by_year,
                "by_month": by_month,
                "total_focus": total_focus
            })
            return

        # 普通用户历史配置快照列表
        if path == "/api/user/config_snapshots":
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            snapshots = get_user_config_snapshots(user["id"])
            self.send_json({"ok": True, "snapshots": snapshots})
            return

        # 普通用户历史配置快照详情
        if path == "/api/user/config_snapshot_detail":
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            snapshot_id = query_params.get("snapshot_id", [""])[0] or query_params.get("id", [""])[0]
            if not snapshot_id:
                self.send_json({"ok": False, "msg": "缺少快照标识 snapshot_id"}, status=400)
                return
            detail = get_user_config_snapshot_detail(user["id"], snapshot_id)
            if not detail:
                self.send_json({"ok": False, "msg": "快照不存在或无权访问"}, status=404)
                return
            self.send_json({"ok": True, "detail": detail})
            return

        # 管理员视图：查看所有用户及各自关注的重点项目/重点业主
        if path == "/api/admin/users":
            if not self.is_authenticated():
                self.send_json({"ok": False, "error": "unauthorized", "msg": "需要管理员权限"}, status=401)
                return
            users_list = get_all_users_for_admin()
            self.send_json({"ok": True, "total": len(users_list), "users": users_list})
            return

        # 管理员视图：查看用户登录审计历史记录流水
        if path == "/api/admin/user_logins":
            if not self.is_authenticated():
                self.send_json({"ok": False, "error": "unauthorized", "msg": "需要管理员权限"}, status=401)
                return
            logins = get_login_history_for_admin(limit=200)
            self.send_json({"ok": True, "total": len(logins), "logins": logins})
            return

        if path == "/api/check_upgrade":
            self.send_json(check_app_upgrade())
            return

        if path == "/api/task_state":
            self.send_json(PROC_MGR.get_state())
            return

        if path == "/api/live_monitor":
            qs = parse_qs(parsed.query)
            source = qs.get("source", ["active"])[0]
            try:
                lines = int(qs.get("lines", [120])[0])
            except (ValueError, TypeError):
                lines = 120
            metrics = get_live_server_metrics(log_source=source, log_lines=lines)
            self.send_json(metrics)
            return

        if path == "/api/config":
            self.send_json(read_config_env())
            return

        if path == "/api/config_snapshots":
            self.send_json({"ok": True, "snapshots": list_config_snapshots()})
            return

        if path == "/api/baseline_status":
            try:
                from scripts.scan_record_db import get_scan_record_db
                db = get_scan_record_db()
                stats = db.get_baseline_stats()
                # 若发现覆盖天数为 0（未固化基线），自动触发自愈校准
                if stats.get("dates_covered", 0) == 0:
                    if db.auto_bootstrap_if_needed():
                        stats = db.get_baseline_stats()
                self.send_json({"ok": True, "stats": stats})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e), "stats": {}})
            return

        if path == "/api/runs":
            # 获取运行历史记录与任务日志（仅展示系统自动定时执行流水，严格剔除按事件任务台账的内容）
            query = parse_qs(parsed.query)
            limit = min(int(query.get("limit", ["30"])[0]), 100)
            offset = max(int(query.get("offset", ["0"])[0]), 0)
            kind_filter = query.get("kind", [""])[0].strip()
            day_filter = query.get("day", [""])[0].strip()

            runs = []
            total_count = 0
            try:
                import json as _json
                with sqlite3.connect(config.DB_PATH) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    # 动态探测 runs 表结构
                    cols = []
                    try:
                        cols = [r[1] for r in cur.execute("PRAGMA table_info(runs)").fetchall()]
                        if cols and "trigger_type" not in cols:
                            conn.execute("ALTER TABLE runs ADD COLUMN trigger_type TEXT DEFAULT 'auto'")
                            conn.commit()
                            cols.append("trigger_type")
                    except Exception:
                        pass

                    where_clauses = []
                    params = []
                    # 仅在拥有 trigger_type 字段时过滤事件派生记录
                    if "trigger_type" in cols:
                        where_clauses.append("(trigger_type != 'event' OR trigger_type IS NULL)")
                    if kind_filter:
                        where_clauses.append("kind = ?")
                        params.append(kind_filter)
                    if day_filter:
                        where_clauses.append("day = ?")
                        params.append(day_filter)

                    where_str = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                    count_sql = "SELECT COUNT(*) FROM runs"
                    cur.execute(count_sql + where_str, list(params))
                    total_count = cur.fetchone()[0]

                    query_sql = f"SELECT run_id, day, kind, started_at, finished_at, total, ok, failed, skipped, aborted, payload FROM runs{where_str} ORDER BY started_at DESC LIMIT ? OFFSET ?"
                    query_params = list(params) + [limit, offset]
                    cur.execute(query_sql, query_params)
                    for r in cur.fetchall():
                        d = dict(r)
                        if d.get("payload"):
                            try:
                                d["payload"] = _json.loads(d["payload"])
                            except Exception:
                                pass
                        runs.append(d)
            except Exception as e:
                logging.warning(f"[/api/runs] 查询单次采集流水异常: {e}")
                runs = []
            self.send_json({"ok": True, "total": total_count, "runs": runs})
            return

        if path == "/api/task_events":
            # 获取事件级高层任务台账
            try:
                from scripts import task_event_db
                active_eid = PROC_MGR.current_event_id if PROC_MGR.status == "running" else None
                task_event_db.heal_stale_running_events(active_event_id=active_eid)
            except Exception:
                pass
            query = parse_qs(parsed.query)
            limit = min(int(query.get("limit", ["30"])[0]), 100)
            offset = max(int(query.get("offset", ["0"])[0]), 0)
            status_filter = query.get("status", [""])[0].strip() or None
            task_type_filter = query.get("task_type", [""])[0].strip() or None
            try:
                from scripts import task_event_db
                total, events = task_event_db.list_events(
                    limit=limit, offset=offset, status=status_filter, task_type=task_type_filter
                )
                self.send_json({"ok": True, "total": total, "events": events})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e), "total": 0, "events": []})
            return

        if path == "/api/task_event_detail":
            # 获取指定事件的全部每日执行明细与统计
            query = parse_qs(parsed.query)
            event_id = query.get("event_id", [""])[0].strip()
            if not event_id:
                self.send_json({"ok": False, "error": "缺少 event_id 参数"})
                return
            try:
                from scripts import task_event_db
                event = task_event_db.get_event(event_id)
                if not event:
                    self.send_json({"ok": False, "error": "未找到对应的任务事件"})
                    return
                unfinished = task_event_db.get_unfinished_dates(event_id)
                event["unfinished_dates"] = unfinished
                event["has_unfinished"] = len(unfinished) > 0
                self.send_json({"ok": True, "event": event})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        if path == "/api/security_logs":
            if not self.is_authenticated():
                self._audit("UNAUTHORIZED", "FAILED", "未授权访问 /api/security_logs")
                self.send_json({"ok": False, "error": "Unauthorized"}, status=401)
                return
            query = parse_qs(parsed.query)
            log_type = query.get("type", ["audit"])[0].strip()
            keyword = query.get("keyword", [""])[0].strip()
            action = query.get("action", [""])[0].strip()
            date_start = query.get("date_start", [""])[0].strip()
            date_end = query.get("date_end", [""])[0].strip()
            try:
                limit = int(query.get("limit", [50])[0])
            except ValueError:
                limit = 50
            try:
                offset = int(query.get("offset", [0])[0])
            except ValueError:
                offset = 0
            result = query_web_logs(
                log_type=log_type,
                keyword=keyword,
                action=action,
                date_start=date_start,
                date_end=date_end,
                limit=limit,
                offset=offset,
            )
            result["ok"] = True
            self.send_json(result)
            return

        if path == "/api/security_logs/export":
            if not self.is_authenticated():
                self._audit("UNAUTHORIZED", "FAILED", "未授权访问 /api/security_logs/export")
                self.send_json({"ok": False, "error": "Unauthorized"}, status=401)
                return
            query = parse_qs(parsed.query)
            target_type = query.get("type", ["audit"])[0].strip()
            log_filename = "audit.log" if target_type == "audit" else "access.log"
            log_filepath = LOG_DIR / log_filename
            if not log_filepath.exists():
                content = b""
            else:
                try:
                    with open(log_filepath, "rb") as lf:
                        content = lf.read()
                except Exception as e:
                    content = str(e).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{log_filename}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/api/search":
            # 全盘检索历史所有公告数据，按发布时间倒序排列
            query = parse_qs(parsed.query)
            q = (query.get("q", [""])[0]).strip().lower()
            region = (query.get("region", [""])[0]).strip()
            stage = (query.get("stage", [""])[0]).strip()
            industry = (query.get("industry", [""])[0]).strip()
            delayed_only = query.get("delayed_only", ["0"])[0] in ("1", "true", "True")
            overtime_only = query.get("overtime_only", ["0"])[0] in ("1", "true", "True")
            project_only = query.get("project_only", ["0"])[0] in ("1", "true", "True")
            owner_only = query.get("owner_only", ["0"])[0] in ("1", "true", "True")
            limit = min(int(query.get("limit", ["100"])[0]), 1000)
            offset = max(int(query.get("offset", ["0"])[0]), 0)

            idx_file = Path(config.DATA_DIR) / "search_index.json"
            if not idx_file.is_file():
                try:
                    from scripts.build_search_index import generate_search_index
                    generate_search_index()
                except Exception:
                    pass

            items = []
            if idx_file.is_file():
                try:
                    items = json.loads(idx_file.read_text(encoding="utf-8"))
                except Exception:
                    items = []

            filtered = []
            for it in items:
                if q:
                    t = (it.get("title") or "").lower()
                    a = (it.get("areaname") or "").lower()
                    s = (it.get("stage") or "").lower()
                    i = (it.get("industry") or "").lower()
                    if q not in t and q not in a and q not in s and q not in i:
                        continue
                if delayed_only and not it.get("is_delayed"):
                    continue
                if overtime_only and not it.get("is_overtime"):
                    continue
                if project_only and "重点项目" not in (it.get("focus_tags") or []):
                    continue
                if owner_only and "重点业主" not in (it.get("focus_tags") or []):
                    continue
                if region and region not in (it.get("areaname") or ""):
                    continue
                if stage and stage not in (it.get("stage") or ""):
                    continue
                if industry and industry not in (it.get("industry") or ""):
                    continue
                filtered.append(it)

            # 如果勾选了仅看滞后，额外从 scan_records 表补充以防 search_index.json 尚未同步
            if delayed_only:
                try:
                    from scripts.scan_record_db import get_scan_record_db
                    db = get_scan_record_db()
                    with db._connection() as conn:
                        conn.row_factory = sqlite3.Row
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT infoid, title, pub_time, pub_date as date,
                                  delay_days, delay_hours, delay_label, is_delayed,
                                  center as areaname, link, evidence_text as delayed_reason
                            FROM scan_records
                            WHERE is_delayed = 1
                        """)
                        for dr in [dict(r) for r in cur.fetchall()]:
                            if not any(f.get("infoid") == dr.get("infoid") for f in filtered):
                                if q and q not in (dr.get("title") or "").lower():
                                   continue
                                filtered.append(dr)
                except Exception:
                    pass

            # 严格时间倒序
            filtered.sort(key=lambda r: str(r.get("pub_time") or r.get("date") or ""), reverse=True)
            total = len(filtered)
            page_items = filtered[offset:offset+limit]

            self.send_json({"ok": True, "total": total, "items": page_items})
            return

        if path == "/api/delayed_list":
            # 查询当前所有标记为滞后的公告条目
            try:
                from scripts.scan_record_db import get_scan_record_db
                db = get_scan_record_db()
                with db._connection() as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT infoid, title, pub_time, pub_date, first_scan_time, first_scan_date,
                                delay_days, delay_hours, delay_label, is_delayed, is_baseline,
                                center, link, evidence_text, notes
                        FROM scan_records
                        WHERE is_delayed = 1
                        ORDER BY pub_time DESC
                    """)
                    records = [dict(r) for r in cur.fetchall()]

                # 兼容性检查：若 search_index.json 中包含 is_delayed 标记，一并汇总
                seen_ids = {r.get("infoid") for r in records if r.get("infoid")}
                idx_file = Path("data/search_index.json")
                if not idx_file.is_file():
                    idx_file = Path("dist/search_index.json")
                if idx_file.is_file():
                    try:
                        idx_items = json.loads(idx_file.read_text(encoding="utf-8"))
                        for it in idx_items:
                            if it.get("is_delayed") and it.get("infoid") not in seen_ids:
                                seen_ids.add(it.get("infoid"))
                                records.append({
                                    "infoid": it.get("infoid"),
                                    "title": it.get("title", ""),
                                    "pub_time": it.get("pub_time", ""),
                                    "pub_date": it.get("date", ""),
                                    "first_scan_time": "",
                                    "first_scan_date": "",
                                    "delay_days": it.get("delay_days", 0),
                                    "delay_hours": it.get("delay_hours", 0),
                                    "delay_label": it.get("delay_label", "滞后公开"),
                                    "is_delayed": 1,
                                    "is_baseline": 0,
                                    "center": it.get("areaname", ""),
                                    "link": it.get("link", ""),
                                    "evidence_text": it.get("delayed_reason", "在全盘检索索引中标记为滞后公开"),
                                    "notes": ""
                                })
                    except Exception:
                        pass
                self.send_json({"ok": True, "total": len(records), "records": records})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e), "records": []})
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

        # 根路径通用静态文件查找（优先从公开情报 site_dir 获取，其次从管理后台 STATIC_DIR 获取）
        rel_path = path.lstrip("/")
        if rel_path:
            site_dir = get_site_dir()
            try:
                target = (site_dir / rel_path).resolve()
                if str(target).startswith(str(site_dir.resolve())) and target.is_file():
                    mime = "text/html; charset=utf-8" if target.suffix.lower() == ".html" else \
                           "text/css; charset=utf-8" if target.suffix.lower() == ".css" else \
                           "application/javascript; charset=utf-8" if target.suffix.lower() == ".js" else \
                           mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                    data = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            except Exception:
                pass

            try:
                target = (STATIC_DIR / rel_path).resolve()
                if str(target).startswith(str(STATIC_DIR.resolve())) and target.is_file():
                    self.serve_file(target)
                    return
            except Exception:
                pass

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

        if path == "/api/login":
            req_password = post_data.get("password", "").strip()
            admin_pw = getattr(config, "ADMIN_PASSWORD", "").strip() if config else ""
            if not admin_pw or req_password == admin_pw:
                self._audit("LOGIN", "SUCCESS", "管理员密码验证成功，颁发会话令牌")
                token = generate_auth_token(admin_pw)
                cookie_val = f"auth_token={token}; Path=/; Max-Age=604800; SameSite=Lax"
                body = json.dumps({"ok": True, "token": token, "msg": "登录成功"}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Set-Cookie", cookie_val)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            else:
                self._audit("LOGIN", "FAILED", "管理员密码错误，登录失败")
                self.send_json({"ok": False, "msg": "管理员密码错误，请重新输入"})
                return

        if path == "/api/logout":
            self._audit("LOGOUT", "SUCCESS", "管理员退出登录")
            cookie_val = "auth_token=; Path=/; Max-Age=0; SameSite=Lax"
            body = json.dumps({"ok": True, "msg": "已安全退出登录"}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Set-Cookie", cookie_val)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # -------------------------------------------------------------
        # 普通用户注册、登录、注销与重点关注 API
        # -------------------------------------------------------------
        if path == "/api/user/register":
            username = post_data.get("username", "").strip()
            password = post_data.get("password", "").strip()
            display_name = post_data.get("display_name", "").strip()
            client_ip = self.client_address[0] if self.client_address else ""
            ua = self.headers.get("User-Agent", "")
            ok, msg, user_info = register_user(username, password, display_name, ip=client_ip, user_agent=ua)
            if not ok:
                self._audit("USER_REGISTER", "FAILED", f"普通用户注册失败: {username} ({msg})")
                self.send_json({"ok": False, "msg": msg})
                return
            self._audit("USER_REGISTER", "SUCCESS", f"普通用户注册成功: {username}")
            token = generate_user_token(user_info["id"], user_info["username"])
            cookie_val = f"user_token={token}; Path=/; Max-Age=604800; SameSite=Lax"
            body = json.dumps({"ok": True, "msg": "注册成功并已自动登录", "token": token, "user": user_info}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Set-Cookie", cookie_val)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/user/login":
            username = post_data.get("username", "").strip()
            password = post_data.get("password", "").strip()
            client_ip = self.client_address[0] if self.client_address else ""
            ua = self.headers.get("User-Agent", "")
            ok, msg, user_info = verify_user_login(username, password, ip=client_ip, user_agent=ua)
            if not ok:
                self._audit("USER_LOGIN", "FAILED", f"普通用户登录失败: {username} ({msg})")
                self.send_json({"ok": False, "msg": msg})
                return
            self._audit("USER_LOGIN", "SUCCESS", f"普通用户登录成功: {username}")
            token = generate_user_token(user_info["id"], user_info["username"])
            cookie_val = f"user_token={token}; Path=/; Max-Age=604800; SameSite=Lax"
            body = json.dumps({"ok": True, "msg": "登录成功", "token": token, "user": user_info}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Set-Cookie", cookie_val)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/user/logout":
            self._audit("USER_LOGOUT", "SUCCESS", "普通用户退出登录")
            cookie_val = "user_token=; Path=/; Max-Age=0; SameSite=Lax"
            body = json.dumps({"ok": True, "msg": "已退出登录"}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Set-Cookie", cookie_val)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/user/favorites/add":
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            target_type = post_data.get("target_type", "")
            target_name = post_data.get("target_name", "")
            tags = post_data.get("tags", "")
            ok, msg = add_user_favorite(user["id"], user["username"], target_type, target_name, tags)
            favs = get_user_favorites(user["id"])
            self.send_json({"ok": ok, "msg": msg, "favorites": favs})
            return

        if path == "/api/user/favorites/delete":
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            fav_id = post_data.get("id")
            target_type = post_data.get("target_type", "")
            target_name = post_data.get("target_name", "")
            ok, msg = remove_user_favorite(user["id"], favorite_id=fav_id, target_type=target_type, target_name=target_name)
            favs = get_user_favorites(user["id"])
            self.send_json({"ok": ok, "msg": msg, "favorites": favs})
            return

        # 保存普通用户个性化配置（包含重点项目、重点业主、预警词、金额门槛），并自动创建快照
        if path == "/api/user/config":
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            note = post_data.get("note", "用户自主调整配置")
            res = save_user_full_config(
                user_id=user["id"],
                username=user["username"],
                config_data=post_data,
                trigger="manual",
                note=note
            )
            self._audit("USER_CONFIG_UPDATE", "SUCCESS", f"用户 {user['username']} 更新个性化跟踪规则并生成快照 {res.get('snapshot_id')}")
            self.send_json(res)
            return

        # 普通用户配置快照安全回滚
        if path in ("/api/user/config_snapshots/rollback", "/api/user/rollback_config"):
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            snapshot_id = post_data.get("snapshot_id") or post_data.get("id")
            if not snapshot_id:
                self.send_json({"ok": False, "msg": "缺少快照标识 snapshot_id"}, status=400)
                return
            ok, msg = rollback_user_config_snapshot(user["id"], user["username"], snapshot_id)
            if ok:
                self._audit("USER_CONFIG_ROLLBACK", "SUCCESS", f"用户 {user['username']} 回滚配置至快照 {snapshot_id}")
            self.send_json({"ok": ok, "msg": msg})
            return

        # 普通用户历史配置快照删除
        if path in ("/api/user/config_snapshots/delete", "/api/user/delete_config_snapshot"):
            user = self.get_current_user()
            if not user:
                self.send_json({"ok": False, "error": "unauthorized", "msg": "请先登录普通用户账号"}, status=401)
                return
            snapshot_id = post_data.get("snapshot_id") or post_data.get("id")
            if not snapshot_id:
                self.send_json({"ok": False, "msg": "缺少快照标识 snapshot_id"}, status=400)
                return
            ok, msg = delete_user_config_snapshot(user["id"], snapshot_id)
            if ok:
                self._audit("USER_CONFIG_SNAPSHOT_DELETE", "SUCCESS", f"用户 {user['username']} 删除了历史快照 {snapshot_id}")
                snapshots = get_user_config_snapshots(user["id"])
                self.send_json({"ok": True, "msg": msg, "snapshots": snapshots})
            else:
                self.send_json({"ok": False, "msg": msg}, status=400)
            return

        # 敏感写操作与流水线触发鉴权拦截
        protected_paths = {
            "/api/abort_task", "/api/stop_task", "/api/save_config", "/api/rollback_config",
            "/api/delete_date", "/api/monitor_control", "/api/save_rules", "/api/save_master_rules",
            "/api/restore_master_rules", "/api/restore_snapshot", "/api/unmark_delayed",
            "/api/refresh_index", "/api/fix_tunnel", "/api/test_ssh", "/api/test_wechat",
            "/api/test_jjt", "/api/save_jjt_config", "/api/resume_task_event", "/api/open_screen"
        }
        if path.startswith("/api/run_") or path in protected_paths:
            if not self.is_authenticated():
                self._audit("UNAUTHORIZED", "FAILED", f"未授权尝试访问受保护接口: {path}")
                self.send_json({"ok": False, "error": "unauthorized", "msg": "未登录或登录已过期，请重新登录管理员账号"}, status=401)
                return

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
            self._audit("START_TASK", "SUCCESS" if ok else "FAILED", f"启动全流程日报流水线 ({target_date})")
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
            self._audit("START_TASK", "SUCCESS" if ok else "FAILED", f"启动昨日标讯最终版流水线 ({target_date})")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_upgrade":
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "upgrade_app.py")]
            ok, msg = PROC_MGR.start_task("同步拉取 GitHub 最新版本代码", cmd)
            self._audit("RUN_UPGRADE", "SUCCESS" if ok else "FAILED", "启动系统在线升级同步代码")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_collect":
            target_date = post_data.get("date", "").strip() or date.today().isoformat()
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "collect.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"单步采集公告 ({target_date})", cmd)
            self._audit("START_TASK", "SUCCESS" if ok else "FAILED", f"启动单步采集公告 ({target_date})")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_collect_cz":
            target_date = post_data.get("date", "").strip() or date.today().isoformat()
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "collect_cz_ygcg.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"单采崇左阳光采购 ({target_date})", cmd)
            self._audit("START_TASK", "SUCCESS" if ok else "FAILED", f"启动单采崇左阳光采购 ({target_date})")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_build":
            target_date = post_data.get("date", "").strip() or date.today().isoformat()
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "build_daily_page.py"), "--date", target_date]
            ok, msg = PROC_MGR.start_task(f"重新构建静态页面 ({target_date})", cmd)
            self._audit("START_TASK", "SUCCESS" if ok else "FAILED", f"启动重新构建静态页面 ({target_date})")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_batch_scan":
            start_date = post_data.get("start_date", "").strip()
            end_date = post_data.get("end_date", "").strip()
            custom_title = post_data.get("title", "").strip()
            if not start_date or not end_date:
                self.send_json({"ok": False, "msg": "请同时指定起始补扫日期与截止补扫日期！"})
                return

            event_id = ""
            event_title = custom_title
            try:
                from scripts import task_event_db
                from datetime import datetime as _dt, timedelta as _td
                d_s = _dt.strptime(start_date, "%Y-%m-%d").date()
                d_e = _dt.strptime(end_date, "%Y-%m-%d").date()
                if d_s > d_e:
                    d_s, d_e = d_e, d_s
                target_dates = []
                cur_d = d_s
                while cur_d <= d_e:
                    target_dates.append(cur_d.isoformat())
                    cur_d += _td(days=1)
                
                if not event_title:
                    event_title = task_event_db.generate_title(target_dates, "batch_scan")

                event_id = task_event_db.create_event(
                    task_type="batch_scan",
                    target_dates=target_dates,
                    title=event_title,
                    params={"start_date": start_date, "end_date": end_date}
                )
            except Exception as e_evt:
                print(f"[警告] 创建任务事件失败: {e_evt}")

            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "batch_scan.py"), "--start-date", start_date, "--end-date", end_date]
            if event_id:
                cmd.extend(["--event-id", event_id])
            if event_title:
                cmd.extend(["--title", event_title])

            task_name = f"批量扫描事件: {event_title}" if event_title else f"历史区间数据补扫 ({start_date} 至 {end_date})"
            ok, msg = PROC_MGR.start_task(task_name, cmd)
            self._audit("START_TASK", "SUCCESS" if ok else "FAILED", f"启动区间批量补扫 ({start_date} ~ {end_date}, event_id={event_id})")
            self.send_json({"ok": ok, "msg": msg, "event_id": event_id})
            return

        if path == "/api/resume_task_event":
            # 断点续跑：只对该事件下未完成或失败的日期进行补扫
            event_id = post_data.get("event_id", "").strip()
            if not event_id:
                self.send_json({"ok": False, "msg": "缺少事件 ID (event_id)！"})
                return

            if PROC_MGR.status == "running":
                self.send_json({"ok": False, "msg": f"当前已有任务正在运行: {PROC_MGR.task_name}，请等待其完成或先终止当前任务再续跑！"})
                return

            try:
                from scripts import task_event_db
                event = task_event_db.get_event(event_id)
                if not event:
                    self.send_json({"ok": False, "msg": f"未找到 ID 为 {event_id} 的任务事件！"})
                    return
                unfinished = task_event_db.get_unfinished_dates(event_id)
                if not unfinished:
                    self.send_json({"ok": False, "msg": "该事件包含的所有日期均已成功完成，无需断点续跑！"})
                    return

                cmd = [
                    py_exe, "-u", str(ROOT_DIR / "scripts" / "batch_scan.py"),
                    "--event-id", event_id,
                    "--resume",
                    "--title", event.get("title", "")
                ]
                task_name = f"断点续跑: {event.get('title', '')} (剩余 {len(unfinished)} 天)"
                ok, msg = PROC_MGR.start_task(task_name, cmd)
                if ok:
                    task_event_db.resume_event(event_id)
                    self._audit("RESUME_TASK", "SUCCESS", f"启动断点续跑事件 {event_id} (剩余 {len(unfinished)} 天)")
                    self.send_json({
                        "ok": True,
                        "msg": f"已启动断点续跑，正在继续完成剩余 {len(unfinished)} 天数据扫描: {', '.join(unfinished[:5])}{'...' if len(unfinished)>5 else ''}",
                        "unfinished_count": len(unfinished),
                        "event_id": event_id
                    })
                else:
                    self._audit("RESUME_TASK", "FAILED", f"启动断点续跑失败: {msg}")
                    self.send_json({"ok": False, "msg": msg})
            except Exception as e:
                self._audit("RESUME_TASK", "FAILED", f"断点续跑启动异常: {e}")
                self.send_json({"ok": False, "msg": f"断点续跑启动异常: {e}"})
            return

        if path == "/api/reset_task_event":
            # 强制重置卡死在 running 状态的事件为待续跑状态
            event_id = post_data.get("event_id", "").strip()
            if not event_id:
                self.send_json({"ok": False, "msg": "缺少事件 ID (event_id)！"})
                return
            try:
                from scripts import task_event_db
                ok = task_event_db.reset_event_to_retryable(event_id)
                if ok:
                    self._audit("RESET_TASK_EVENT", "SUCCESS", f"手动重置任务事件为可续跑: {event_id}")
                    self.send_json({"ok": True, "msg": f"事件 {event_id} 已成功重置为可续跑状态！"})
                else:
                    self.send_json({"ok": False, "msg": f"未找到事件 {event_id} 或无需重置！"})
            except Exception as e:
                self.send_json({"ok": False, "msg": f"重置事件异常: {e}"})
            return

        if path == "/api/run_backscan":
            days = int(post_data.get("days", getattr(config, "BACKSCAN_DAYS", 30)))
            min_delay = int(post_data.get("min_delay", getattr(config, "BACKSCAN_MIN_DELAY", 2)))
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "backscan_delayed.py"), "--days", str(days), "--min-delay", str(min_delay)]
            ok, msg = PROC_MGR.start_task(f"历史公告回扫比对 (排查过去 {days} 天滞后公开公告)", cmd)
            self._audit("START_TASK", "SUCCESS" if ok else "FAILED", f"启动历史公告回扫比对 (过去 {days} 天)")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/rebuild_baseline":
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "scan_record_db.py"), "--rebuild-baseline"]
            ok, msg = PROC_MGR.start_task("同步与校准底边扫描数据库基线", cmd)
            self._audit("START_TASK", "SUCCESS" if ok else "FAILED", "启动同步与校准底边扫描数据库基线")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/unmark_delayed":
            # 人工纠错：撤销滞后公开标记，并永久划入底边原始基线
            infoid = post_data.get("infoid", "").strip()
            date_val = post_data.get("date", "").strip()
            notes = post_data.get("notes", "").strip() or "人工纠错划入原始对比数据"
            if not infoid:
                self.send_json({"ok": False, "msg": "未提供有效公告 infoid！"})
                return

            try:
                from scripts.unmark_delayed import unmark_notice
                res = unmark_notice(infoid, date=date_val, notes=notes, rebuild_page=True)
                self._audit("UNMARK_DELAYED", "SUCCESS" if res.get("ok") else "FAILED", f"人工解除滞后标记 (infoid={infoid}, date={date_val})")
                self.send_json({"ok": res.get("ok", False), "msg": res.get("message", "操作完成"), "data": res})
            except Exception as e:
                self.send_json({"ok": False, "msg": f"解除滞后标记失败: {str(e)}"})
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

            # 5. 自动同步 VPS 云端站点（非 VPS 部署环境执行网络同步）
            vps_synced = False
            if getattr(config, "IS_ON_VPS", False):
                vps_text = "（VPS 本地实时生效）"
            elif getattr(config, "AUTO_UPLOAD_VPS", True):
                try:
                    upload_script = ROOT_DIR / "scripts" / "upload_vps.py"
                    if upload_script.exists():
                        res_up = subprocess.run([py_exe, "-u", str(upload_script)], capture_output=True, text=True)
                        vps_synced = (res_up.returncode == 0)
                except Exception as e_vps:
                    print(f"[警告] 同步 VPS 异常: {e_vps}")
                vps_text = "，并已彻底同步清理 VPS 云端站点" if vps_synced else ""
            else:
                vps_text = ""

            target_desc = f"{len(target_dates)} 个日期 ({', '.join(target_dates[:3])}{'...' if len(target_dates) > 3 else ''})"
            self._audit("DELETE_DATA", "SUCCESS", f"删除数据与日报页面: {target_desc}")
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
                    self._audit("REFRESH_INDEX", "FAILED", f"重新构建首页索引失败: {res_arc.stderr}")
                    self.send_json({"ok": False, "msg": f"重新构建首页索引失败: {res_arc.stderr}"})
                    return

                vps_msg = ""
                if getattr(config, "IS_ON_VPS", False):
                    vps_msg = "（当前运行于 VPS 本机，Nginx 毫秒级即时生效）"
                elif getattr(config, "AUTO_UPLOAD_VPS", True):
                    upload_script = ROOT_DIR / "scripts" / "upload_vps.py"
                    if upload_script.exists():
                        res_vps = subprocess.run([py_exe, "-u", str(upload_script)], capture_output=True, text=True)
                        if res_vps.returncode == 0:
                            vps_msg = "，并已同步发布至 VPS 云端站点"
                        else:
                            vps_msg = f" (VPS 同步输出: {res_vps.stderr or res_vps.stdout})"

                self._audit("REFRESH_INDEX", "SUCCESS", "首页归档索引已重新计算生成")
                self.send_json({"ok": True, "msg": f"首页归档索引已重新计算生成{vps_msg}！"})
            except Exception as e:
                self._audit("REFRESH_INDEX", "FAILED", f"刷新首页异常: {str(e)}")
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
            self._audit("REAPPLY_RULES", "SUCCESS" if ok else "FAILED", f"按最新规则重新标注: {task_title}")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/run_upload":
            if getattr(config, "IS_ON_VPS", False):
                self.send_json({"ok": True, "msg": "系统当前直接运行在生产 VPS 本机，静态文件已就地实时生效，无需重复进行网络上传！"})
                return
            cmd = [py_exe, "-u", str(ROOT_DIR / "scripts" / "upload_vps.py")]
            ok, msg = PROC_MGR.start_task("同步上传静态文件至 VPS", cmd)
            self._audit("UPLOAD_VPS", "SUCCESS" if ok else "FAILED", "同步上传静态文件至 VPS")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/test_ssh":
            host = post_data.get("host", "").strip() or getattr(config, "VPS_HOST", "8.134.69.22")
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
                    self._audit("TEST_SSH", "SUCCESS", f"SSH 连接测试成功: {user}@{host}:{port}")
                    self.send_json({"ok": True, "msg": f"SSH 连通成功！已成功登录 {user}@{host}:{port}。"})
                else:
                    err_msg = res.stderr.strip() or res.stdout.strip() or "登录鉴权失败"
                    def_key = str(Path.home() / ".ssh" / "id_rsa")
                    self._audit("TEST_SSH", "FAILED", f"SSH 连接测试失败: {user}@{host}:{port} ({err_msg})")
                    self.send_json({
                        "ok": False,
                        "msg": f"SSH 连接失败: {err_msg}。\n\n【排查建议】\n1. 请检查私钥是否放置于：{def_key}\n2. 或在下方【指定私钥路径】填入私钥文件的绝对路径\n3. 确保云端服务器 ~/.ssh/authorized_keys 中已添加对应公钥。"
                    })
            except Exception as e:
                self._audit("TEST_SSH", "FAILED", f"测试执行异常: {str(e)}")
                self.send_json({"ok": False, "msg": f"测试执行异常: {str(e)}"})
            return

        if path == "/api/tunnel_status":
            try:
                from scripts.tunnel_guardian import get_network_info, find_tunnel_processes
                net_info = get_network_info()
                procs = find_tunnel_processes()
                self.send_json({
                    "ok": True,
                    "running_processes": len(procs),
                    "details": [{"pid": p[0], "cmd": p[1]} for p in procs],
                    "network": net_info
                })
            except Exception as e:
                self.send_json({"ok": False, "msg": f"获取隧道状态失败: {str(e)}"})
            return

        if path == "/api/fix_tunnel":
            try:
                from scripts.tunnel_guardian import ensure_tunnel_health
                status = ensure_tunnel_health(auto_fix=True)
                self._audit("FIX_TUNNEL", "SUCCESS", f"隧道自愈修复已完成 (alive={status.get('tunnel_alive')})")
                self.send_json({
                    "ok": True,
                    "msg": f"隧道自愈修复已完成！活跃状态: {status.get('tunnel_alive')}, 发现并清理进程数: {status.get('running_ssh_processes')}",
                    "status": status
                })
            except Exception as e:
                self._audit("FIX_TUNNEL", "FAILED", f"隧道修复失败: {str(e)}")
                self.send_json({"ok": False, "msg": f"隧道修复失败: {str(e)}"})
            return

        if path == "/api/test_wechat":
            cmd = [py_exe, "-u", "-c", "import sys; sys.path.insert(0, '.'); from scripts.notify_wechat import test_push; sys.exit(0 if test_push() else 1)"]
            ok, msg = PROC_MGR.start_task("测试微信服务号推送", cmd)
            self._audit("TEST_WECHAT", "SUCCESS" if ok else "FAILED", "测试微信服务号推送")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/test_jjt":
            target_type = post_data.get("type", "appchat")
            webhook_url = post_data.get("webhook_url", "").strip()
            if target_type == "webhook":
                cmd = [py_exe, "-u", "scripts/jjt_notify.py", "--webhook", webhook_url or "default"]
                task_name = "测试交建通群机器人 Webhook 推送"
            else:
                cmd = [py_exe, "-u", "scripts/jjt_notify.py", "--test"]
                task_name = "测试交建通应用群消息推送"
            ok, msg = PROC_MGR.start_task(task_name, cmd)
            self._audit("TEST_JJT", "SUCCESS" if ok else "FAILED", f"{task_name}: {target_type}")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/jjt_status" or path == "/api/jjt_config":
            from pathlib import Path
            cfg_file = Path(__file__).resolve().parent.parent / "config_jjt.json"
            has_cfg = cfg_file.exists()
            cfg_data = {
                "ok": True,
                "configured": has_cfg,
                "corpid": "",
                "has_corpsecret": False,
                "api_base_url": "https://jjt.ccccltd.cn/cgi-bin",
                "default_chatid": "",
                "webhook_url": "",
                "site_base_url": "https://ztb.139771.xyz"
            }
            if has_cfg:
                try:
                    with open(cfg_file, "r", encoding="utf-8") as f:
                        c = json.load(f)
                        cfg_data["corpid"] = c.get("corpid", "")
                        cfg_data["has_corpsecret"] = bool(c.get("corpsecret"))
                        cfg_data["api_base_url"] = c.get("api_base_url", "https://jjt.ccccltd.cn/cgi-bin")
                        cfg_data["default_chatid"] = c.get("default_chatid", "")
                        cfg_data["webhook_url"] = c.get("webhook_url", "")
                        cfg_data["site_base_url"] = c.get("site_base_url", "https://ztb.139771.xyz")
                except Exception:
                    pass
            self.send_json(cfg_data)
            return

        if path == "/api/save_jjt_config":
            from pathlib import Path
            cfg_file = Path(__file__).resolve().parent.parent / "config_jjt.json"
            old_cfg = {}
            if cfg_file.exists():
                try:
                    with open(cfg_file, "r", encoding="utf-8") as f:
                        old_cfg = json.load(f)
                except Exception:
                    pass

            new_corpid = post_data.get("corpid", "").strip()
            new_secret = post_data.get("corpsecret", "").strip()
            new_base = post_data.get("api_base_url", "").strip() or "https://jjt.ccccltd.cn/cgi-bin"
            new_chatid = post_data.get("default_chatid", "").strip()
            new_webhook = post_data.get("webhook_url", "").strip()
            new_site_base = post_data.get("site_base_url", "").strip() or "https://ztb.139771.xyz"

            # 如果 secret 为空且之前有值，则保留旧 secret
            final_secret = new_secret if new_secret else old_cfg.get("corpsecret", "")

            updated_cfg = {
                "corpid": new_corpid,
                "corpsecret": final_secret,
                "api_base_url": new_base,
                "default_chatid": new_chatid,
                "webhook_url": new_webhook,
                "site_base_url": new_site_base
            }

            try:
                with open(cfg_file, "w", encoding="utf-8") as f:
                    json.dump(updated_cfg, f, ensure_ascii=False, indent=2)
                self._audit("SAVE_JJT_CONFIG", "SUCCESS", f"保存交建通配置成功: corpid={new_corpid}, chatid={new_chatid}")
                self.send_json({"ok": True, "msg": "交建通配置保存成功并已立即生效！"})
            except Exception as e:
                self._audit("SAVE_JJT_CONFIG", "FAILED", f"保存交建通配置异常: {e}")
                self.send_json({"ok": False, "msg": f"保存失败: {e}"})
            return

        if path == "/api/monitor_control":
            action = post_data.get("action", "").strip()
            if sys.platform != "win32":
                if sys.platform.startswith("linux"):
                    try:
                        if action == "enable":
                            subprocess.run(["systemctl", "unmask", "gxzb-daily.timer"], check=False)
                            subprocess.run(["systemctl", "enable", "gxzb-daily.timer"], check=False)
                            subprocess.run(["systemctl", "start", "gxzb-daily.timer"], check=False)
                            self._audit("MONITOR_CONTROL", "SUCCESS", "自动化监控已成功开启(Linux)")
                            self.send_json({"ok": True, "msg": "自动化监控已成功开启！系统将按周期自动轮询采集。"})
                        elif action == "disable":
                            subprocess.run(["systemctl", "stop", "gxzb-daily.timer"], check=False)
                            subprocess.run(["systemctl", "disable", "gxzb-daily.timer"], check=False)
                            self._audit("MONITOR_CONTROL", "SUCCESS", "自动化监控已暂停(Linux)")
                            self.send_json({"ok": True, "msg": "自动化监控已暂停！系统将不再按周期自动执行。"})
                        else:
                            self._audit("MONITOR_CONTROL", "FAILED", f"未知操作: {action}")
                            self.send_json({"ok": False, "msg": f"未知操作: {action}"})
                        return
                    except Exception as e:
                        self._audit("MONITOR_CONTROL", "FAILED", f"操作失败: {e}")
                        self.send_json({"ok": False, "msg": f"操作失败: {e}"})
                        return
                self._audit("MONITOR_CONTROL", "SUCCESS", f"模拟设置监控状态为: {action}")
                self.send_json({"ok": True, "msg": f"当前系统非 Windows，已模拟设置监控状态为: {action}"})
                return
            try:
                if action == "enable":
                    cp = subprocess.run(["schtasks", "/change", "/tn", "ZtbCollector_Sync", "/enable"], capture_output=True, timeout=5)
                    if cp.returncode == 0:
                        self._audit("MONITOR_CONTROL", "SUCCESS", "自动化监控已成功开启(Win)")
                        self.send_json({"ok": True, "msg": "自动化监控已成功开启！系统将按周期自动轮询采集。"})
                    else:
                        err = cp.stderr.decode("gbk", errors="ignore") or cp.stdout.decode("gbk", errors="ignore")
                        self._audit("MONITOR_CONTROL", "FAILED", f"开启监控失败: {err}")
                        self.send_json({"ok": False, "msg": f"开启监控失败: {err}"})
                elif action == "disable":
                    cp = subprocess.run(["schtasks", "/change", "/tn", "ZtbCollector_Sync", "/disable"], capture_output=True, timeout=5)
                    if cp.returncode == 0:
                        self._audit("MONITOR_CONTROL", "SUCCESS", "自动化监控已暂停(Win)")
                        self.send_json({"ok": True, "msg": "自动化监控已暂停。定时任务已停止触发。"})
                    else:
                        err = cp.stderr.decode("gbk", errors="ignore") or cp.stdout.decode("gbk", errors="ignore")
                        self._audit("MONITOR_CONTROL", "FAILED", f"暂停监控失败: {err}")
                        self.send_json({"ok": False, "msg": f"暂停监控失败: {err}"})
                else:
                    self._audit("MONITOR_CONTROL", "FAILED", f"未知操作类型: {action}")
                    self.send_json({"ok": False, "msg": "未知操作类型"})
            except Exception as e:
                self._audit("MONITOR_CONTROL", "FAILED", f"执行失败: {str(e)}")
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
                self._audit("OPEN_SCREEN", "SUCCESS", "在大屏展厅窗口打开实时大屏")
                self.send_json({"ok": True, "msg": "已在大屏展厅窗口打开实时大屏！"})
            except Exception as e:
                self._audit("OPEN_SCREEN", "FAILED", f"呼起大屏窗口失败: {str(e)}")
                self.send_json({"ok": False, "msg": f"呼起大屏窗口失败: {str(e)}"}, status=500)
            return

        if path == "/api/stop_task":
            ok, msg = PROC_MGR.stop_task()
            self._audit("STOP_TASK", "SUCCESS" if ok else "FAILED", f"终止后台任务: {msg}")
            self.send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/save_config":
            try:
                save_config_env(post_data)
                self._audit("SAVE_CONFIG", "SUCCESS", f"配置已保存并重载: {', '.join(sorted(post_data.keys()))}")
                self.send_json({"ok": True, "msg": "配置已成功保存并重新加载"})
            except Exception as exc:
                self._audit("SAVE_CONFIG", "FAILED", f"保存配置失败: {str(exc)}")
                self.send_json({"ok": False, "msg": f"保存失败: {str(exc)}"}, status=500)
            return

        if path == "/api/restore_master_rules":
            try:
                res = restore_master_focus_rules()
                self._audit("RESTORE_MASTER_RULES", "SUCCESS", "恢复 Master 预设重点规则")
                self.send_json({"ok": True, "msg": res["message"], "data": res})
            except Exception as exc:
                self._audit("RESTORE_MASTER_RULES", "FAILED", f"恢复失败: {str(exc)}")
                self.send_json({"ok": False, "msg": f"恢复失败: {str(exc)}"}, status=500)
            return

        if path == "/api/restore_snapshot":
            try:
                snap_name = post_data.get("filename", "")
                restore_config_snapshot(snap_name)
                self._audit("RESTORE_SNAPSHOT", "SUCCESS", f"从快照 {snap_name} 恢复配置")
                self.send_json({"ok": True, "msg": f"已成功从快照 {snap_name} 恢复配置！"})
            except Exception as exc:
                self._audit("RESTORE_SNAPSHOT", "FAILED", f"恢复快照失败: {str(exc)}")
                self.send_json({"ok": False, "msg": f"恢复快照失败: {str(exc)}"}, status=500)
            return

        if path == "/api/create_snapshot":
            try:
                snap_name = create_config_snapshot()
                self._audit("CREATE_SNAPSHOT", "SUCCESS", f"手动创建配置快照: {snap_name}")
                self.send_json({"ok": True, "msg": f"已成功创建快照 {snap_name}！", "filename": snap_name})
            except Exception as exc:
                self._audit("CREATE_SNAPSHOT", "FAILED", f"创建快照失败: {str(exc)}")
                self.send_json({"ok": False, "msg": f"创建快照失败: {str(exc)}"}, status=500)
            return

        if path == "/api/delete_snapshot":
            try:
                snap_name = post_data.get("filename", "")
                delete_config_snapshot(snap_name)
                self._audit("DELETE_SNAPSHOT", "SUCCESS", f"删除配置快照: {snap_name}")
                self.send_json({"ok": True, "msg": f"已成功删除快照 {snap_name}！"})
            except Exception as exc:
                self._audit("DELETE_SNAPSHOT", "FAILED", f"删除快照失败: {str(exc)}")
                self.send_json({"ok": False, "msg": f"删除快照失败: {str(exc)}"}, status=500)
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
