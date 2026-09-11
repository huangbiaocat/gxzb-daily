# -*- coding: utf-8 -*-
"""SQLite 入库层：notices / notice_fields / projects / runs 四张表 + 状态机。

设计要点（与 docs/pipeline.md、docs/extraction-rules.md 保持一致）:
- **主键**：`notices.infoid`（官方唯一识别码），全链路同键，重复跑只更新不新增；
- **状态机**：`pending`（已采集待抽正文）→ `extracted`（已抽取）→ `pushed`（已推送下游）；
  失败条目状态回退并累加 `retry_count`、记录 `last_error`，便于熔断后重跑只补失败项；
- **字段行存**：长表 `notice_fields(infoid, field, value_text, value_num, value_unit, value_json, value_type)`，
  金额保留数值 + 单位（费率型数值即百分比，不换算成元），列表类存 JSON 字符串；
- **项目归集**：`projects.project_id` 由 rules.build_project_id 生成，同一项目的多阶段公告聚合到一行；
- 只写不删：重跑同一 `--date` 不会删除历史数据（上游条目消失时仅标记，不做 DELETE）。

用法:
    python scripts/store.py --init                     # 建库建表（幂等）
    python scripts/store.py --status [--date ...]      # 查看状态机与字段覆盖率
    python scripts/store.py --rebuild-projects [--date ...]
    python scripts/store.py --mark-pushed --infoid <id> [--infoid <id> ...] / --all-extracted
"""
import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_REPO / "extractors"))
import config      # noqa: E402
import normalize   # noqa: E402

STATUS_PENDING = "pending"
STATUS_EXTRACTED = "extracted"
STATUS_PUSHED = "pushed"
STATUS_FAILED = "failed"

STATUS_FLOW = [STATUS_PENDING, STATUS_EXTRACTED, STATUS_PUSHED]

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    infoid          TEXT PRIMARY KEY,
    categorynum     TEXT,
    industry        TEXT,
    stage           TEXT,
    stage_key       TEXT,
    title           TEXT,
    project_name    TEXT,
    region          TEXT,
    pub_time        TEXT,
    link            TEXT,
    detail_url      TEXT,
    body_len        INTEGER DEFAULT 0,
    detail_status   TEXT,          -- ok / empty / missing / failed
    parse_mode      TEXT,          -- container / fallback
    extract_status  TEXT,          -- ok / empty / failed
    missing_required INTEGER DEFAULT 0,
    missing_optional INTEGER DEFAULT 0,
    project_id      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    retry_count     INTEGER DEFAULT 0,
    last_error      TEXT,
    created_at      TEXT,
    extracted_at    TEXT,
    pushed_at       TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_notices_status  ON notices(status);
CREATE INDEX IF NOT EXISTS idx_notices_pub     ON notices(pub_time);
CREATE INDEX IF NOT EXISTS idx_notices_project ON notices(project_id);

CREATE TABLE IF NOT EXISTS notice_fields (
    infoid      TEXT NOT NULL,
    field       TEXT NOT NULL,
    label       TEXT,
    value_text  TEXT,
    value_num   REAL,
    value_unit  TEXT,
    value_json  TEXT,
    value_type  TEXT,               -- text / money / duration / date / list
    created_at  TEXT,
    updated_at  TEXT,
    PRIMARY KEY (infoid, field)
);
CREATE INDEX IF NOT EXISTS idx_fields_field ON notice_fields(field);

CREATE TABLE IF NOT EXISTS projects (
    project_id      TEXT PRIMARY KEY,
    project_name    TEXT,
    tenderee        TEXT,
    region          TEXT,
    notice_count    INTEGER DEFAULT 0,
    first_pub_time  TEXT,
    last_pub_time   TEXT,
    stages          TEXT,           -- JSON 数组
    latest_stage    TEXT,
    latest_infoid   TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    day         TEXT,
    kind        TEXT,
    started_at  TEXT,
    finished_at TEXT,
    total       INTEGER DEFAULT 0,
    ok          INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    skipped     INTEGER DEFAULT 0,
    aborted     INTEGER DEFAULT 0,
    payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_day ON runs(day);
"""


# ------------------------------------------------------------------ 连接 / 建表
def connect(db_path=None):
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn=None):
    own = conn is None
    conn = conn or connect()
    conn.executescript(SCHEMA)
    conn.commit()
    if own:
        conn.close()


def _now():
    return config.now_stamp()


# ------------------------------------------------------------------ 字段序列化
def serialize_field(field, value, label=""):
    """把抽取结果里的一个字段值转成 (value_text, value_num, value_unit, value_json, value_type)。

    金额字段口径：`value_num` 一律为标准化后的数值（元；费率型为百分比数值），
    `value_unit` 取标准化单位（`元` / `pct`），原始单位与原始串存 `value_json` 便于溯源；
    `value_text` 为人类可读串（如 `1113.68万元`、`99.6%`）。
    """
    if value is None:
        return "", None, "", None, "text"
    if field in ("budget", "estimate", "control_price", "contract_price", "bid_price"):
        if isinstance(value, dict):
            num, kind = value.get("value"), value.get("kind")
            raw = value.get("raw") or ""
            if kind == "rate" and num is not None:
                text, unit = ("%g%%" % num), "pct"
            elif num is None:
                text, unit = raw, ""
            else:
                text, unit = normalize.format_money(num), "元"
            meta = json.dumps({"kind": kind or "", "raw": raw,
                               "unit_inferred": bool(value.get("unit_inferred"))}, ensure_ascii=False)
            return text, num, unit, meta, "money"
        return str(value), None, "", None, "money"
    if field == "period":
        if isinstance(value, dict):
            days = value.get("days")
            return value.get("raw") or "", days, ("天" if days is not None else ""), None, "duration"
        return str(value), None, "", None, "duration"
    if isinstance(value, (list, tuple)):
        items = [x for x in value if str(x).strip()]
        if not items:
            return "", None, "", None, "list"
        return "、".join(str(x) for x in items), None, "", json.dumps(items, ensure_ascii=False), "list"
    if isinstance(value, dict):
        if not value:
            return "", None, "", None, "json"
        return json.dumps(value, ensure_ascii=False), None, "", json.dumps(value, ensure_ascii=False), "json"
    return str(value), None, "", None, ("date" if field in ("open_time",) else "text")


# ------------------------------------------------------------------ 写入
def upsert_notice(conn, row, detail=None, extraction=None, status=None):
    """写入/更新一条公告。row 来自 collect 产出的规范化记录，detail/extraction 可选。"""
    infoid = str(row.get("infoid"))
    detail = detail or {}
    extraction = extraction or {}
    fields = extraction.get("fields") or {}
    now = _now()
    cur = conn.execute("SELECT status, created_at, retry_count FROM notices WHERE infoid=?", (infoid,))
    old = cur.fetchone()
    payload = {
        "infoid": infoid,
        "categorynum": row.get("categorynum", ""),
        "industry": row.get("industry", ""),
        "stage": row.get("stage", "") or fields.get("stage", ""),
        "stage_key": row.get("stage_key", "") or fields.get("stage_key", ""),
        "title": row.get("title", ""),
        "project_name": fields.get("project_name") or row.get("title", ""),
        "region": row.get("areaname", "") or fields.get("region", ""),
        "pub_time": row.get("pub_time", "") or fields.get("pub_time", ""),
        "link": row.get("link", ""),
        "detail_url": row.get("detail_url") or detail.get("url", ""),
        "body_len": int(detail.get("body_len") or 0),
        "detail_status": detail.get("status") or ("missing" if not detail else ""),
        "parse_mode": detail.get("parse_mode", ""),
        "extract_status": extraction.get("status", ""),
        "missing_required": len(extraction.get("missing_required") or []),
        "missing_optional": len(extraction.get("missing_optional") or []),
        "project_id": extraction.get("project_id", ""),
        "status": status or (old["status"] if old else STATUS_PENDING),
        "last_error": extraction.get("error", "") if extraction.get("status") == "failed" else "",
        "created_at": (old["created_at"] if old else now) or now,
        "updated_at": now,
    }
    payload["extracted_at"] = now if extraction and extraction.get("status") in ("ok", "empty") else None
    cols = list(payload.keys())
    placeholders = ",".join("?" for _ in cols)
    update = ",".join("%s=excluded.%s" % (c, c) for c in cols if c not in ("infoid", "created_at"))
    # extracted_at 为 None 时不覆盖已有值
    update = update.replace("extracted_at=excluded.extracted_at",
                            "extracted_at=COALESCE(excluded.extracted_at, notices.extracted_at)")
    conn.execute("INSERT INTO notices (%s) VALUES (%s) ON CONFLICT(infoid) DO UPDATE SET %s"
                 % (",".join(cols), placeholders, update), [payload[c] for c in cols])
    if extraction.get("fields"):
        save_fields(conn, infoid, extraction)
    return infoid


def save_fields(conn, infoid, extraction):
    """把 extraction['fields'] 展开为 notice_fields 长表（先删该条旧字段再写，保证不残留）。"""
    fields = extraction.get("fields") or {}
    evidence = extraction.get("evidence") or {}
    now = _now()
    conn.execute("DELETE FROM notice_fields WHERE infoid=?", (infoid,))
    for field, value in fields.items():
        text, num, unit, js, vtype = serialize_field(field, value, label=evidence.get(field, ""))
        if text == "" and num is None and js is None:
            continue
        conn.execute(
            "INSERT INTO notice_fields (infoid, field, label, value_text, value_num, value_unit,"
            " value_json, value_type, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (infoid, field, evidence.get(field, ""), text, num, unit, js, vtype, now, now))


def set_status(conn, infoid, status, error=None, inc_retry=False):
    conn.execute(
        "UPDATE notices SET status=?, last_error=?, retry_count=retry_count+?, updated_at=?,"
        " pushed_at=CASE WHEN ?='pushed' THEN ? ELSE pushed_at END WHERE infoid=?",
        (status, error, 1 if inc_retry else 0, _now(), status, _now(), infoid))


def mark_pushed(conn, infoids=None, all_extracted=False):
    """推送后置为 pushed（无下游推送时本状态可不用）。"""
    now = _now()
    if all_extracted:
        cur = conn.execute("UPDATE notices SET status=?, pushed_at=?, updated_at=? WHERE status=?",
                           (STATUS_PUSHED, now, now, STATUS_EXTRACTED))
    else:
        cur = conn.execute(
            "UPDATE notices SET status=?, pushed_at=?, updated_at=? WHERE infoid IN (%s)"
            % ",".join("?" for _ in (infoids or [])),
            (STATUS_PUSHED, now, now) + tuple(infoids or []))
    conn.commit()
    return cur.rowcount


# ------------------------------------------------------------------ 项目归集
def rebuild_projects(conn, day=None):
    """按 project_id 重算 projects 表（幂等全量重建，不删除 notices）。"""
    sql = ("SELECT infoid, project_name, project_id, pub_time, stage, region,"
           " (SELECT value_text FROM notice_fields f WHERE f.infoid=n.infoid AND f.field='tenderee') AS tenderee"
           " FROM notices n")
    params = ()
    if day:
        sql += " WHERE pub_time LIKE ?"
        params = ("%s%%" % day,)
    groups = {}
    for r in conn.execute(sql, params):
        pid = r["project_id"]
        if not pid:
            continue
        g = groups.setdefault(pid, {"names": [], "tenderee": "", "region": "", "rows": []})
        g["rows"].append((r["pub_time"] or "", r["infoid"], r["stage"] or ""))
        if r["project_name"]:
            g["names"].append(r["project_name"])
        if r["tenderee"] and not g["tenderee"]:
            g["tenderee"] = r["tenderee"]
        if r["region"] and not g["region"]:
            g["region"] = r["region"]
    now = _now()
    if day:
        # 幂等收尾：当天最后一条落在 day、但已不在本次归并结果里的 project 行属陈旧行，删除之
        stale = [r["project_id"] for r in conn.execute(
            "SELECT project_id FROM projects WHERE last_pub_time LIKE ?", ("%s%%" % day,))
            if r["project_id"] not in groups]
        for pid in stale:
            conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
    for pid, g in groups.items():
        rows = sorted(g["rows"])
        stages = normalize.dedup_keep_order([s for _, _, s in rows if s])
        name = max(g["names"], key=len) if g["names"] else ""
        conn.execute(
            "INSERT INTO projects (project_id, project_name, tenderee, region, notice_count,"
            " first_pub_time, last_pub_time, stages, latest_stage, latest_infoid, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(project_id) DO UPDATE SET project_name=excluded.project_name,"
            " tenderee=excluded.tenderee, region=excluded.region, notice_count=excluded.notice_count,"
            " first_pub_time=excluded.first_pub_time, last_pub_time=excluded.last_pub_time,"
            " stages=excluded.stages, latest_stage=excluded.latest_stage,"
            " latest_infoid=excluded.latest_infoid, updated_at=excluded.updated_at",
            (pid, name, g["tenderee"], g["region"], len(rows), rows[0][0], rows[-1][0],
             json.dumps(stages, ensure_ascii=False), rows[-1][2], rows[-1][1], now, now))
    conn.commit()
    return len(groups)


def record_run(conn, day, kind, stats):
    """记录一次运行（抽取/入库/推送），供 run_daily 与监控查询。

    `ok` 缺省时回退到 `extracted`（extract 阶段统计用 extracted 计数成功条数）。
    """
    run_id = uuid.uuid4().hex[:16]
    ok = stats.get("ok")
    if ok is None:
        ok = stats.get("extracted") or stats.get("unique_rows") or 0
    conn.execute(
        "INSERT INTO runs (run_id, day, kind, started_at, finished_at, total, ok, failed, skipped,"
        " aborted, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, day, kind, stats.get("started_at", ""), stats.get("finished_at", _now()),
         int(stats.get("total") or 0), int(ok), int(stats.get("failed") or 0),
         int(stats.get("skipped") or 0), 1 if stats.get("aborted") else 0,
         json.dumps(stats, ensure_ascii=False)))
    conn.commit()
    return run_id


# ------------------------------------------------------------------ 查询
def fetch_notices(conn, status=None, day=None, limit=None):
    sql = "SELECT * FROM notices"
    where, params = [], []
    if status:
        where.append("status=?")
        params.append(status)
    if day:
        where.append("pub_time LIKE ?")
        params.append("%s%%" % day)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pub_time, infoid"
    if limit:
        sql += " LIMIT %d" % int(limit)
    return [dict(r) for r in conn.execute(sql, params)]


def get_fields(conn, infoid, value_type=None):
    sql = "SELECT * FROM notice_fields WHERE infoid=?"
    params = [infoid]
    if value_type:
        sql += " AND value_type=?"
        params.append(value_type)
    return [dict(r) for r in conn.execute(sql, params)]


def stats(conn, day=None):
    out = {}
    where, params = ("WHERE pub_time LIKE ?", ["%s%%" % day]) if day else ("", [])
    out["status"] = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM notices %s GROUP BY status" % where, params)}
    out["detail_status"] = {r["detail_status"]: r["n"] for r in conn.execute(
        "SELECT detail_status, COUNT(*) n FROM notices %s GROUP BY detail_status" % where, params)}
    out["notices"] = conn.execute("SELECT COUNT(*) n FROM notices %s" % where, params).fetchone()["n"]
    out["fields"] = conn.execute("SELECT COUNT(*) n FROM notice_fields").fetchone()["n"]
    out["projects"] = conn.execute("SELECT COUNT(*) n FROM projects").fetchone()["n"]
    out["runs"] = conn.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="gxzb-daily SQLite 入库")
    ap.add_argument("--init", action="store_true", help="建库建表（幂等）")
    ap.add_argument("--status", action="store_true", help="查看状态机与统计")
    ap.add_argument("--date", default=None)
    ap.add_argument("--rebuild-projects", action="store_true")
    ap.add_argument("--mark-pushed", action="store_true")
    ap.add_argument("--all-extracted", action="store_true")
    ap.add_argument("--infoid", action="append", default=None)
    ap.add_argument("--db", default=None, help="覆盖数据库路径")
    args = ap.parse_args(argv)

    conn = connect(args.db)
    init_db(conn)
    if args.init:
        print(json.dumps({"db": str(Path(args.db or config.DB_PATH)), "ok": True}, ensure_ascii=False))
        return 0
    if args.rebuild_projects:
        n = rebuild_projects(conn, args.date)
        print(json.dumps({"projects": n}, ensure_ascii=False))
        return 0
    if args.mark_pushed:
        n = mark_pushed(conn, args.infoid, all_extracted=args.all_extracted)
        print(json.dumps({"pushed": n}, ensure_ascii=False))
        return 0
    print(json.dumps({"db": str(Path(args.db or config.DB_PATH)), **stats(conn, args.date)},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
