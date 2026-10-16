# -*- coding: utf-8 -*-
"""
用户中心与关注数据管理模块 (SQLite)
提供普通用户注册、登录校验、登录审计日志、重点项目与业主关注管理，以及管理员视图审计查询。
"""

import hmac
import hashlib
import json
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

        CREATE TABLE IF NOT EXISTS portal_user_configs (
            user_id INTEGER PRIMARY KEY,
            alert_min_amount REAL DEFAULT 1000.0,
            alert_keywords_json TEXT DEFAULT '[]',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES portal_users(id)
        );

        CREATE TABLE IF NOT EXISTS portal_user_config_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            snapshot_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            trigger TEXT DEFAULT 'manual',
            focus_projects_count INTEGER DEFAULT 0,
            focus_owners_count INTEGER DEFAULT 0,
            alert_keywords_count INTEGER DEFAULT 0,
            alert_min_amount REAL DEFAULT 1000.0,
            config_json TEXT NOT NULL,
            note TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES portal_users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_users_username ON portal_users(username);
        CREATE INDEX IF NOT EXISTS idx_logins_user_time ON portal_user_logins(user_id, login_time);
        CREATE INDEX IF NOT EXISTS idx_fav_user_type ON portal_user_favorites(user_id, target_type);
        CREATE INDEX IF NOT EXISTS idx_user_snapshots ON portal_user_config_snapshots(user_id, created_at);
        """)


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """生成加盐密码哈希"""
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode("utf-8")).hexdigest()
    return hashed, salt


def register_user(username: str, password: str, display_name: str = "", ip: str = "", user_agent: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
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
                INSERT INTO portal_users (username, password_hash, salt, display_name, role, status, created_at, last_login_at, login_count)
                VALUES (?, ?, ?, ?, 'user', 'active', ?, ?, 1)
            """, (username, pwd_hash, salt, display_name, now_str, now_str))
            user_id = cur.lastrowid

            # 记录注册即自动登录的首次审计日志
            conn.execute("""
                INSERT INTO portal_user_logins (user_id, username, login_time, ip_address, user_agent)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, now_str, ip[:64], user_agent[:256]))

            return True, "注册成功", {
                "id": user_id,
                "username": username,
                "display_name": display_name,
                "role": "user",
                "created_at": now_str,
                "last_login_at": now_str,
                "login_count": 1
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
            ORDER BY id ASC
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

    with get_connection() as conn:
        cfg_rows = conn.execute("SELECT user_id, alert_min_amount, alert_keywords_json, updated_at FROM portal_user_configs").fetchall()
        snap_counts = conn.execute("SELECT user_id, COUNT(1) as cnt, MAX(created_at) as last_snap FROM portal_user_config_snapshots GROUP BY user_id").fetchall()

    cfg_map = {r["user_id"]: r for r in cfg_rows}
    snap_map = {r["user_id"]: r for r in snap_counts}

    result = []
    for u in users:
        uid = u["id"]
        user_favs = fav_map.get(uid, {"projects": [], "owners": []})
        ucfg = cfg_map.get(uid)
        usnap = snap_map.get(uid)
        kw_cnt = 0
        if ucfg and ucfg["alert_keywords_json"]:
            try:
                kw_cnt = len(json.loads(ucfg["alert_keywords_json"]))
            except Exception:
                kw_cnt = 0
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
            "favorite_owners_count": len(user_favs["owners"]),
            "alert_min_amount": ucfg["alert_min_amount"] if ucfg else 1000.0,
            "alert_keywords_count": kw_cnt,
            "snapshots_count": usnap["cnt"] if usnap else 0,
            "last_snapshot_at": usnap["last_snap"] if usnap else ""
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


def get_user_full_config(user_id: int) -> Dict[str, Any]:
    """获取用户的完整独立跟踪配置（重点项目、重点业主、预警关键词、激活金额门槛）"""
    init_user_db()
    favs = get_user_favorites(user_id)
    p_names = [p["name"] for p in favs.get("projects", [])]
    o_names = [o["name"] for o in favs.get("owners", [])]

    with get_connection() as conn:
        cfg_row = conn.execute("""
            SELECT alert_min_amount, alert_keywords_json, updated_at
            FROM portal_user_configs
            WHERE user_id = ?
        """, (user_id,)).fetchone()

    if cfg_row:
        alert_min_amount = float(cfg_row["alert_min_amount"]) if cfg_row["alert_min_amount"] is not None else 1000.0
        try:
            alert_keywords = json.loads(cfg_row["alert_keywords_json"]) if cfg_row["alert_keywords_json"] else []
        except Exception:
            alert_keywords = []
        updated_at = cfg_row["updated_at"] or ""
        has_custom = True
    else:
        # 若用户尚未配置过个性化预警规则，但有单独收藏项目/业主，则视为主观定制
        has_custom = bool(p_names or o_names)
        alert_min_amount = 1000.0
        alert_keywords = ["EPC", "设计施工总承包", "特许经营", "产业园", "智慧", "全过程工程咨询"]
        updated_at = ""
        # 若完全未定制，默认将系统基准规则作为参考模板呈现给用户
        if not has_custom:
            try:
                import config
                p_names = list(getattr(config, "FOCUS_PROJECTS", []))
                o_names = list(getattr(config, "FOCUS_OWNERS", []))
            except Exception:
                pass

    return {
        "user_id": user_id,
        "has_custom_config": has_custom,
        "focus_projects": p_names,
        "focus_owners": o_names,
        "alert_keywords": alert_keywords,
        "alert_min_amount": alert_min_amount,
        "focus_projects_count": len(p_names),
        "focus_owners_count": len(o_names),
        "alert_keywords_count": len(alert_keywords),
        "updated_at": updated_at
    }


def save_user_full_config(
    user_id: int,
    username: str,
    config_data: Dict[str, Any],
    trigger: str = "manual",
    note: str = ""
) -> Dict[str, Any]:
    """保存用户专属的独立重点配置并自动生成历史配置快照"""
    init_user_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _clean_list(raw_val: Any) -> List[str]:
        if isinstance(raw_val, list):
            items = [str(x).strip() for x in raw_val if str(x).strip()]
        elif isinstance(raw_val, str):
            raw_val = raw_val.replace("\r\n", "\n").replace("，", ",")
            if "\n" in raw_val:
                items = [x.strip() for x in raw_val.split("\n") if x.strip()]
            else:
                items = [x.strip() for x in raw_val.split(",") if x.strip()]
        else:
            items = []
        # 保留原有顺序去重
        seen = set()
        deduped = []
        for item in items:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    p_list = _clean_list(config_data.get("focus_projects", []))
    o_list = _clean_list(config_data.get("focus_owners", []))
    kw_list = _clean_list(config_data.get("alert_keywords", []))

    try:
        min_amt = float(config_data.get("alert_min_amount", 1000.0))
        if min_amt < 0:
            min_amt = 0.0
    except (ValueError, TypeError):
        min_amt = 1000.0

    snapshot_id = f"snap_u{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    snapshot_payload = {
        "focus_projects": p_list,
        "focus_owners": o_list,
        "alert_keywords": kw_list,
        "alert_min_amount": min_amt,
        "saved_at": now_str
    }
    snapshot_json = json.dumps(snapshot_payload, ensure_ascii=False)

    with get_connection() as conn:
        # 1. 刷新 portal_user_favorites（全量同步，保证项目与业主独立强规则生效）
        conn.execute("DELETE FROM portal_user_favorites WHERE user_id = ?", (user_id,))
        for p in p_list:
            conn.execute("""
                INSERT OR IGNORE INTO portal_user_favorites (user_id, username, target_type, target_name, tags, created_at)
                VALUES (?, ?, 'project', ?, '', ?)
            """, (user_id, username, p, now_str))
        for o in o_list:
            conn.execute("""
                INSERT OR IGNORE INTO portal_user_favorites (user_id, username, target_type, target_name, tags, created_at)
                VALUES (?, ?, 'owner', ?, '', ?)
            """, (user_id, username, o, now_str))

        # 2. 更新 portal_user_configs 预警词与金额门槛
        conn.execute("""
            INSERT INTO portal_user_configs (user_id, alert_min_amount, alert_keywords_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                alert_min_amount = excluded.alert_min_amount,
                alert_keywords_json = excluded.alert_keywords_json,
                updated_at = excluded.updated_at
        """, (user_id, min_amt, json.dumps(kw_list, ensure_ascii=False), now_str))

        # 3. 产生历史配置快照
        conn.execute("""
            INSERT INTO portal_user_config_snapshots (
                user_id, snapshot_id, created_at, trigger,
                focus_projects_count, focus_owners_count, alert_keywords_count, alert_min_amount,
                config_json, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, snapshot_id, now_str, trigger,
            len(p_list), len(o_list), len(kw_list), min_amt,
            snapshot_json, note
        ))

    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "updated_at": now_str,
        "focus_projects_count": len(p_list),
        "focus_owners_count": len(o_list),
        "alert_keywords_count": len(kw_list),
        "alert_min_amount": min_amt,
        "msg": "用户个性化规则已成功保存并生成历史配置快照"
    }


def get_user_config_snapshots(user_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    """获取用户的历史配置快照列表"""
    init_user_db()
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT snapshot_id, created_at, trigger,
                   focus_projects_count, focus_owners_count, alert_keywords_count, alert_min_amount,
                   note
            FROM portal_user_config_snapshots
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()

    return [
        {
            "snapshot_id": r["snapshot_id"],
            "created_at": r["created_at"],
            "trigger": r["trigger"],
            "focus_projects_count": r["focus_projects_count"],
            "focus_owners_count": r["focus_owners_count"],
            "alert_keywords_count": r["alert_keywords_count"],
            "alert_min_amount": r["alert_min_amount"],
            "note": r["note"] or ""
        }
        for r in rows
    ]


def get_user_config_snapshot_detail(user_id: int, snapshot_id: str) -> Optional[Dict[str, Any]]:
    """获取指定历史快照的详细内容"""
    init_user_db()
    with get_connection() as conn:
        row = conn.execute("""
            SELECT snapshot_id, created_at, trigger,
                   focus_projects_count, focus_owners_count, alert_keywords_count, alert_min_amount,
                   config_json, note
            FROM portal_user_config_snapshots
            WHERE user_id = ? AND snapshot_id = ?
        """, (user_id, snapshot_id)).fetchone()

    if not row:
        return None

    try:
        config_payload = json.loads(row["config_json"])
    except Exception:
        config_payload = {}

    return {
        "snapshot_id": row["snapshot_id"],
        "created_at": row["created_at"],
        "trigger": row["trigger"],
        "focus_projects_count": row["focus_projects_count"],
        "focus_owners_count": row["focus_owners_count"],
        "alert_keywords_count": row["alert_keywords_count"],
        "alert_min_amount": row["alert_min_amount"],
        "note": row["note"] or "",
        "config": config_payload
    }


def rollback_user_config_snapshot(user_id: int, username: str, snapshot_id: str) -> Tuple[bool, str]:
    """将用户配置安全回滚至指定历史快照"""
    detail = get_user_config_snapshot_detail(user_id, snapshot_id)
    if not detail or "config" not in detail:
        return False, f"未找到快照 {snapshot_id} 或快照数据损坏"

    target_cfg = detail["config"]
    note_text = f"回滚自快照 [{snapshot_id}] ({detail.get('created_at', '')})"
    save_user_full_config(user_id, username, target_cfg, trigger="rollback", note=note_text)
    return True, f"已成功将配置回滚至快照 {snapshot_id}"
