# -*- coding: utf-8 -*-
"""运行日志与 infoid 台账（唯一识别码 = 官方接口返回的 infoid）。

- 每日日志：<LOG_DIR>/YYYY-MM-DD.jsonl，一行一条 JSON，主键为 infoid。
  ① 重跑判重：同一天重复执行不会把同一条公告重复计数；
  ② 失败可重查：status != collected 的条目可按 infoid 重取；
  ③ 溯源：记录该公告在页面中的标题 / 地市 / 环节 / 链接与最后更新时间。
- 全局台账：<STATE_DIR>/index.json，infoid -> {date, status, title, first_seen, last_seen}。
  跨日判重：避免同一公告在次日被当成新公告重复入库 / 重复推送。

不再有任何自造编号（历史 GX 编号已废弃）。
"""
import collections
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
import config  # noqa: E402

STATUS_COLLECTED = "collected"   # 已采集并已入库（页面可见）
LOG_FIELDS = ["infoid", "title", "areaname", "industry", "stage", "pub_time", "link", "status", "updated_at"]


# ------------------------------------------------------------------ 每日日志
def log_path(day):
    return config.LOG_DIR / ("%s.jsonl" % day)


def read_log(day="", path=None):
    """读取日志 -> OrderedDict[infoid, record]（按文件顺序）。"""
    p = Path(path) if path else (log_path(day) if day else None)
    records = collections.OrderedDict()
    if not p or not p.exists():
        return records
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = str(rec.get("infoid") or "")
        if key:
            records[key] = rec
    return records


def row_to_record(row, stamp, status=None):
    rec = collections.OrderedDict()
    rec["infoid"] = str(row.get("infoid") or row.get("id") or "")
    rec["title"] = row.get("title", "")
    rec["areaname"] = row.get("areaname", "")
    rec["industry"] = row.get("industry", "")
    rec["stage"] = row.get("stage", "")
    rec["pub_time"] = row.get("pub_time", "")
    rec["link"] = row.get("link", "")
    rec["status"] = status or row.get("status") or STATUS_COLLECTED
    rec["updated_at"] = stamp
    return rec


def write_log(day, rows, stamp, status_default=STATUS_COLLECTED):
    """写当日日志：本次入库条目刷新状态，历史条目原样保留（便于按 infoid 找回）。

    返回 (日志总行数, 待重取条目列表)。
    """
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    prev = read_log(day)
    lines, seen = [], set()
    for row in rows:
        rec = row_to_record(row, stamp, status=row.get("status") or status_default)
        if not rec["infoid"]:
            raise ValueError("条目缺少 infoid，拒绝写入日志：%r" % (row.get("title", "")[:40],))
        seen.add(rec["infoid"])
        lines.append(json.dumps(rec, ensure_ascii=False))
    for infoid, rec in prev.items():
        if infoid not in seen:
            lines.append(json.dumps(rec, ensure_ascii=False))
    log_path(day).write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = [json.loads(l) for l in lines if l.strip()]
    return len(out), [r for r in out if r.get("status") != STATUS_COLLECTED]


def pending_refetch(day=""):
    """日志中 status 非 collected 的条目（供上游按 infoid 重取）。"""
    return [rec for rec in read_log(day).values() if rec.get("status") != STATUS_COLLECTED]


# ------------------------------------------------------------------ 全局台账
def index_path():
    return config.STATE_DIR / "index.json"


def load_index():
    p = index_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("items", {})
    except (json.JSONDecodeError, AttributeError):
        return {}


def save_index(items, stamp):
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "updated_at": stamp, "count": len(items), "items": items}
    index_path().write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def update_index(rows, day, stamp):
    """把本次入库条目登记进全局台账（已存在则续写 last_seen）。"""
    items = load_index()
    for row in rows:
        infoid = str(row.get("infoid") or row.get("id") or "")
        if not infoid:
            continue
        old = items.get(infoid) or {}
        items[infoid] = {
            "date": old.get("date") or day,
            "title": row.get("title", old.get("title", "")),
            "status": row.get("status") or STATUS_COLLECTED,
            "first_seen": old.get("first_seen") or stamp,
            "last_seen": stamp,
        }
    save_index(items, stamp)
    return items


def unseen(infoids):
    """跨日判重：返回台账中从未出现过的 infoid（即真正的新公告）。"""
    known = load_index()
    return [i for i in infoids if i not in known]
