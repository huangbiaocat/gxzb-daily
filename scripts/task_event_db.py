# -*- coding: utf-8 -*-
"""事件级任务台账与断点续跑数据库管理模块 (Task Event DB).

用于记录高层批处理事件（如全月重扫、区间补扫、全量回扫等）：
- 记录整体任务的生命周期、完成进度（总天数、已完成天数、失败天数、待执行天数）与总采集公告数；
- 记录该事件下每一天的子任务执行明细（状态、条数、重点数、耗时、异常信息）；
- 支持断点续跑（Resume）：一键筛选出未完成或失败的日期并自动续扫直至整体全部完成。
"""

import json
import sqlite3
import sys
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS task_events (
    event_id        TEXT PRIMARY KEY,
    task_type       TEXT NOT NULL DEFAULT 'batch_scan',
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    started_at      TEXT,
    finished_at     TEXT,
    total_days      INTEGER DEFAULT 0,
    completed_days  INTEGER DEFAULT 0,
    failed_days     INTEGER DEFAULT 0,
    pending_days    INTEGER DEFAULT 0,
    total_notices   INTEGER DEFAULT 0,
    days_detail     TEXT,
    params          TEXT,
    log_file        TEXT,
    error_msg       TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_events_status ON task_events(status);
CREATE INDEX IF NOT EXISTS idx_task_events_started ON task_events(started_at);
"""


def get_conn():
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def generate_title(target_dates: List[str], task_type: str = "batch_scan") -> str:
    """根据日期列表智能生成易读的事件标题（如：2026年02月全月扫描）。"""
    if not target_dates:
        return "数据扫描任务"
    sorted_dates = sorted(target_dates)
    first_d = sorted_dates[0]
    last_d = sorted_dates[-1]

    # 判断是否为某月全月扫描
    try:
        dt_f = datetime.strptime(first_d, "%Y-%m-%d").date()
        dt_l = datetime.strptime(last_d, "%Y-%m-%d").date()
        if dt_f.year == dt_l.year and dt_f.month == dt_l.month and dt_f.day == 1:
            # 检查 last_d 是否为该月最后一天
            import calendar
            _, max_day = calendar.monthrange(dt_f.year, dt_f.month)
            if dt_l.day == max_day and len(target_dates) == max_day:
                return f"{dt_f.year}年{dt_f.month:02d}月全月扫描 (共{len(target_dates)}天)"
    except Exception:
        pass

    if len(target_dates) == 1:
        return f"{first_d} 单日全流程扫描"
    elif first_d != last_d and len(target_dates) > 1:
        return f"{first_d} 至 {last_d} 批量扫描 (共{len(target_dates)}天)"
    else:
        return f"批量扫描任务 (共{len(target_dates)}天)"


def create_event(
    task_type: str,
    target_dates: List[str],
    title: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
) -> str:
    init_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not event_id:
        event_id = f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    if not title:
        title = generate_title(target_dates, task_type)

    days_detail = []
    for d in sorted(target_dates):
        days_detail.append({
            "day": d,
            "status": "pending",
            "count": 0,
            "focus": 0,
            "duration": 0.0,
            "error": None,
            "started_at": None,
            "finished_at": None,
        })

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO task_events (
                event_id, task_type, title, status, started_at,
                total_days, completed_days, failed_days, pending_days,
                total_notices, days_detail, params, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_type,
                title,
                "pending",
                now_str,
                len(target_dates),
                0,
                0,
                len(target_dates),
                0,
                json.dumps(days_detail, ensure_ascii=False),
                json.dumps(params or {}, ensure_ascii=False),
                now_str,
            ),
        )
    return event_id


def get_event(event_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM task_events WHERE event_id = ?", (event_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["days_detail"] = json.loads(d["days_detail"] or "[]")
        except Exception:
            d["days_detail"] = []
        try:
            d["params"] = json.loads(d["params"] or "{}")
        except Exception:
            d["params"] = {}
        return d


def update_day_progress(
    event_id: str,
    day: str,
    status: str,
    count: int = 0,
    focus: int = 0,
    duration: float = 0.0,
    error: Optional[str] = None,
) -> None:
    init_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM task_events WHERE event_id = ?", (event_id,)).fetchone()
        if not row:
            return
        
        try:
            days_detail = json.loads(row["days_detail"] or "[]")
        except Exception:
            days_detail = []

        day_found = False
        for item in days_detail:
            if item.get("day") == day:
                item["status"] = status
                if count > 0 or status == "success":
                    item["count"] = count
                if focus > 0 or status == "success":
                    item["focus"] = focus
                if duration > 0:
                    item["duration"] = round(duration, 1)
                if error:
                    item["error"] = error
                elif status == "success":
                    item["error"] = None
                if status == "running":
                    item["started_at"] = now_str
                elif status in ("success", "failed"):
                    item["finished_at"] = now_str
                day_found = True
                break

        if not day_found:
            days_detail.append({
                "day": day,
                "status": status,
                "count": count,
                "focus": focus,
                "duration": round(duration, 1),
                "error": error,
                "finished_at": now_str,
            })

        # 统计汇总
        total_days = len(days_detail)
        completed = sum(1 for x in days_detail if x.get("status") == "success")
        failed = sum(1 for x in days_detail if x.get("status") == "failed")
        pending = sum(1 for x in days_detail if x.get("status") in ("pending", "running"))
        total_notices = sum(x.get("count", 0) for x in days_detail)

        # 判定事件整体状态
        if pending > 0:
            overall_status = "running"
        elif failed == 0:
            overall_status = "success"
        elif completed > 0:
            overall_status = "partial_failed"
        else:
            overall_status = "failed"

        conn.execute(
            """
            UPDATE task_events SET
                status = ?,
                total_days = ?,
                completed_days = ?,
                failed_days = ?,
                pending_days = ?,
                total_notices = ?,
                days_detail = ?,
                updated_at = ?
            WHERE event_id = ?
            """,
            (
                overall_status,
                total_days,
                completed,
                failed,
                pending,
                total_notices,
                json.dumps(days_detail, ensure_ascii=False),
                now_str,
                event_id,
            ),
        )


def finish_event(event_id: str, error_msg: Optional[str] = None) -> None:
    init_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM task_events WHERE event_id = ?", (event_id,)).fetchone()
        if not row:
            return
        try:
            days_detail = json.loads(row["days_detail"] or "[]")
        except Exception:
            days_detail = []

        completed = sum(1 for x in days_detail if x.get("status") == "success")
        failed = sum(1 for x in days_detail if x.get("status") == "failed")
        pending = sum(1 for x in days_detail if x.get("status") in ("pending", "running"))
        total_notices = sum(x.get("count", 0) for x in days_detail)

        if failed == 0 and pending == 0:
            overall_status = "success"
        elif completed > 0:
            overall_status = "partial_failed"
        elif error_msg or failed > 0:
            overall_status = "failed"
        else:
            overall_status = "stopped"

        conn.execute(
            """
            UPDATE task_events SET
                status = ?,
                finished_at = ?,
                completed_days = ?,
                failed_days = ?,
                pending_days = ?,
                total_notices = ?,
                error_msg = coalesce(?, error_msg),
                updated_at = ?
            WHERE event_id = ?
            """,
            (
                overall_status,
                now_str,
                completed,
                failed,
                pending,
                total_notices,
                error_msg,
                now_str,
                event_id,
            ),
        )


def get_unfinished_dates(event_id: str) -> List[str]:
    """获取指定事件中所有未完成（失败或待执行）的日期列表。"""
    evt = get_event(event_id)
    if not evt:
        return []
    days = evt.get("days_detail", [])
    unfinished = [
        d["day"]
        for d in days
        if d.get("status") in ("failed", "pending", "running", None)
    ]
    return sorted(unfinished)


def resume_event(event_id: str) -> bool:
    """将事件重置为 running 状态，准备断点续跑。"""
    init_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM task_events WHERE event_id = ?", (event_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            """
            UPDATE task_events SET
                status = 'running',
                finished_at = NULL,
                updated_at = ?
            WHERE event_id = ?
            """,
            (now_str, event_id),
        )
    return True


def list_events(limit: int = 20, offset: int = 0, status: Optional[str] = None, task_type: Optional[str] = None) -> Tuple[int, List[Dict[str, Any]]]:
    init_db()
    where_clauses = []
    params = []
    if status:
        where_clauses.append("status = ?")
        params.append(status)
    if task_type:
        where_clauses.append("task_type = ?")
        params.append(task_type)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM task_events {where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM task_events {where_sql} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        events = []
        for r in rows:
            d = dict(r)
            try:
                d["days_detail"] = json.loads(d["days_detail"] or "[]")
            except Exception:
                d["days_detail"] = []
            try:
                d["params"] = json.loads(d["params"] or "{}")
            except Exception:
                d["params"] = {}
            # 计算是否支持断点续跑
            d["has_unfinished"] = (d.get("failed_days", 0) > 0 or d.get("pending_days", 0) > 0)
            # 计算完成百分比
            tot = d.get("total_days", 0) or 1
            cmp = d.get("completed_days", 0) or 0
            d["progress_pct"] = min(100, int(cmp / tot * 100))
            events.append(d)

        return total, events


if __name__ == "__main__":
    init_db()
    print("Task Events table initialized successfully.")
