# -*- coding: utf-8 -*-
"""
用户中心与关注数据管理模块 (SQLite)
提供普通用户注册、登录校验、登录审计日志、重点项目与业主关注管理，以及管理员视图审计查询。
"""

import hmac
import hashlib
import sqlite3
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import config

DB_PATH = getattr(config, "DB_PATH", Path(__file__).resolve().parent.parent / "data" / "gxzb.sqlite3")
USER_TOKEN_SECRET = getattr(config, "ADMIN_PASSWORD", "portal_user_secret_key_2026")


def get_connection() -> sqlite3.Connection:
    """获取数据库连接并开启 WAL 模式以支持并发读写"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_user_db() -> None:
    """初始化多用户鉴权、关注及审计相关表"""
    with get_connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS portal_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            role TEXT DEFAULT 'user',
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            last_login_at TEXT DEFAULT '',
            login_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS portal_user_logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            login_time TEXT NOT NULL,
            ip_address TEXT DEFAULT '',
            user_agent TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS portal_user_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            target_type TEXT NOT NULL, -- 'project' 或 'owner'
            target_name TEXT NOT NULL,
            tags TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(user_id, target_type, target_name)
        );

        CREATE INDEX IF NOT EXISTS idx_users_username ON portal_users(username);
        CREATE INDEX IF NOT EXISTS idx_logins_user_time ON portal_user_logins(user_id, login_time);
        CREATE INDEX IF NOT EXISTS idx_fav_user_type ON portal_user_favorites(user_id, target_type);
        """)


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """生成加盐密码哈希"""
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode("utf-8")).hexdigest()
    return hashed, salt


def register_user(username: str, password: str, display_name: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """注册新用户"""
    username = (username or "").strip()
    password = (password or "").strip()
    display_name = (display_name or "").strip() or username

    if not username:
        return False, "用户名不能为空", None
    if len(username) < 3 or len(username) > 32:
        return False, "用户名长度需在 3 到 32 个字符之间", None
    if not password or len(password) < 6:
        return False, "密码长度不能少于 6 位", None

    pwd_hash, salt = hash_password(password)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO portal_users (username, password_hash, salt, display_name, role, status, created_at)
                VALUES (?, ?, ?, ?, 'user', 'active', ?)
            """, (username, pwd_hash, salt, display_name, now_str))
            user_id = cur.lastrowid
            return True, "注册成功", {
                "id": user_id,
                "username": username,
                "display_name": display_name,
                "role": "user",
                "created_at": now_str
            }
    except sqlite3.IntegrityError:
        return False, "该用户名已被注册，请直接登录或换一个", None
    except Exception as e:
        return False, f"注册异常: {str(e)}", None


def verify_user_login(username: str, password: str, ip: str = "", user_agent: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """校验用户登录，并记录登录审计日志"""
    username = (username or "").strip()
    password = (password or "").strip()

    if not username or not password:
        return False, "用户名和密码不能为空", None

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM portal_users WHERE username = ?", (username,)).fetchone()
        if not row:
            return False, "用户不存在或密码错误", None

        user_data = dict(row)
        if user_data.get("status") != "active":
            return False, "该账户已被停用，请联系系统管理员", None

        expected_hash, _ = hash_password(password, user_data["salt"])
        if expected_hash != user_data["password_hash"]:
            return False, "用户不存在或密码错误", None

        # 记录登录审计日志并累加登录计数
        conn.execute("""
            INSERT INTO portal_user_logins (user_id, username, login_time, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?)
        """, (user_data["id"], username, now_str, ip[:64], user_agent[:256]))

        conn.execute("""
            UPDATE portal_users
            SET last_login_at = ?, login_count = login_count + 1
            WHERE id = ?
        """, (now_str, user_data["id"]))

    user_info = {
        "id": user_data["id"],
        "username": user_data["username"],
        "display_name": user_data["display_name"] or user_data["username"],
        "role": user_data["role"],
        "created_at": user_data["created_at"],
        "last_login_at": now_str
    }
    return True, "登录成功", user_info


def generate_user_token(user_id: int, username: str) -> str:
    """生成带有签名的用户登录 Token (有效期 7 天)"""
    ts = int(time.time())
    sig_raw = f"user:{user_id}:{username}:{ts}:{USER_TOKEN_SECRET}"
    sig = hmac.new(USER_TOKEN_SECRET.encode("utf-8"), sig_raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{user_id}:{username}:{ts}:{sig}"


def verify_user_token(token: str, max_age: int = 86400 * 7) -> Optional[Dict[str, Any]]:
    """验证用户 Token，成功返回用户信息字典，失败返回 None"""
    if not token or ":" not in token:
        return None
    parts = token.split(":")
    if len(parts) != 4:
        return None
    uid_str, username, ts_str, sig = parts
    try:
        user_id = int(uid_str)
        ts = int(ts_str)
    except ValueError:
        return None

    now = int(time.time())
    if now - ts > max_age:
        return None

    sig_raw = f"user:{user_id}:{username}:{ts}:{USER_TOKEN_SECRET}"
    expected_sig = hmac.new(USER_TOKEN_SECRET.encode("utf-8"), sig_raw.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    # 从数据库确认用户仍然有效
    with get_connection() as conn:
        row = conn.execute("SELECT id, username, display_name, role, status, created_at, last_login_at FROM portal_users WHERE id = ? AND username = ?", (user_id, username)).fetchone()
        if not row:
            return None
        res = dict(row)
        if res.get("status") != "active":
            return None
        return res


def get_user_favorites(user_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """获取指定用户关注的重点项目和重点业主列表"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, target_type, target_name, tags, created_at
            FROM portal_user_favorites
            WHERE user_id = ?
            ORDER BY id DESC
        """, (user_id,)).fetchall()

    projects = []
    owners = []
    for r in rows:
        item = {
            "id": r["id"],
            "name": r["target_name"],
            "tags": r["tags"],
            "created_at": r["created_at"]
        }
        if r["target_type"] == "project":
            projects.append(item)
        elif r["target_type"] == "owner":
            owners.append(item)

    return {"projects": projects, "owners": owners}


def add_user_favorite(user_id: int, username: str, target_type: str, target_name: str, tags: str = "") -> Tuple[bool, str]:
    """添加用户重点项目或业主关注"""
    target_type = (target_type or "").strip().lower()
    target_name = (target_name or "").strip()
    tags = (tags or "").strip()

    if target_type not in ("project", "owner"):
        return False, "关注类型必须为 project (重点项目) 或 owner (重点业主)"
    if not target_name:
        return False, "关注名称/关键词不能为空"
    if len(target_name) > 120:
        return False, "关注名称不能超过 120 个字符"

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO portal_user_favorites (user_id, username, target_type, target_name, tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, username, target_type, target_name, tags, now_str))
            return True, "关注添加成功"
    except sqlite3.IntegrityError:
        return False, "您已经关注过该项目或业主，无需重复添加"
    except Exception as e:
        return False, f"添加关注失败: {str(e)}"


def remove_user_favorite(user_id: int, favorite_id: Optional[int] = None, target_type: str = "", target_name: str = "") -> Tuple[bool, str]:
    """取消关注"""
    with get_connection() as conn:
        if favorite_id is not None and favorite_id > 0:
            cur = conn.execute("DELETE FROM portal_user_favorites WHERE id = ? AND user_id = ?", (favorite_id, user_id))
        elif target_type and target_name:
            cur = conn.execute("DELETE FROM portal_user_favorites WHERE user_id = ? AND target_type = ? AND target_name = ?", (user_id, target_type, target_name.strip()))
        else:
            return False, "缺少删除关注的目标参数"

        if cur.rowcount > 0:
            return True, "已取消关注"
        return False, "未找到对应的关注记录"


def get_all_users_for_admin() -> List[Dict[str, Any]]:
    """管理员视图：查询所有用户列表及其各自关注的重点项目与重点业主，包含登录频次与最后登录时间"""
    with get_connection() as conn:
        users = conn.execute("""
            SELECT id, username, display_name, role, status, created_at, last_login_at, login_count
            FROM portal_users
            ORDER BY id ASC
        """).fetchall()

        fav_rows = conn.execute("""
            SELECT user_id, target_type, target_name, tags, created_at
            FROM portal_user_favorites
            ORDER BY id ASC
        """).fetchall()

    fav_map: Dict[int, Dict[str, List[Dict[str, str]]]] = {}
    for f in fav_rows:
        uid = f["user_id"]
        if uid not in fav_map:
            fav_map[uid] = {"projects": [], "owners": []}
        item = {"name": f["target_name"], "tags": f["tags"], "created_at": f["created_at"]}
        if f["target_type"] == "project":
            fav_map[uid]["projects"].append(item)
        elif f["target_type"] == "owner":
            fav_map[uid]["owners"].append(item)

    result = []
    for u in users:
        uid = u["id"]
        user_favs = fav_map.get(uid, {"projects": [], "owners": []})
        result.append({
            "id": uid,
            "username": u["username"],
            "display_name": u["display_name"] or u["username"],
            "role": u["role"],
            "status": u["status"],
            "created_at": u["created_at"],
            "last_login_at": u["last_login_at"],
            "login_count": u["login_count"],
            "favorite_projects": user_favs["projects"],
            "favorite_owners": user_favs["owners"],
            "favorite_projects_count": len(user_favs["projects"]),
            "favorite_owners_count": len(user_favs["owners"])
        })
    return result


def get_login_history_for_admin(limit: int = 100) -> List[Dict[str, Any]]:
    """管理员视图：查询用户登录审计流水历史"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT l.id, l.user_id, l.username, l.login_time, l.ip_address, l.user_agent, u.display_name
            FROM portal_user_logins l
            LEFT JOIN portal_users u ON l.user_id = u.id
            ORDER BY l.id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    return [
        {
            "id": r["id"],
            "user_id": r["user_id"],
            "username": r["username"],
            "display_name": r["display_name"] or r["username"],
            "login_time": r["login_time"],
            "ip_address": r["ip_address"],
            "user_agent": r["user_agent"]
        }
        for r in rows
    ]
