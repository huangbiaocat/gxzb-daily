# -*- coding: utf-8 -*-
"""规则抽取 + 入库编排：采集结果 × 正文缓存 → 结构化字段 → SQLite。

用法:
    python scripts/extract.py --date 2026-09-10                 # 全量抽取并入库
    python scripts/extract.py --date 2026-09-10 --limit 50      # 只处理前 50 条（试跑）
    python scripts/extract.py --date 2026-09-10 --source daily   # 以已入库清单为源
    python scripts/extract.py --date 2026-09-10 --dry-run        # 只抽取不入库（看命中率）
    python scripts/extract.py --status                           # 查看库内状态机统计

流程:
    collect(<date>.json) + details/<date>/<infoid>.json
        → extractors.rules.extract_notice(正文, 上下文)
        → store：notices 主表 + notice_fields 长表 + projects 归集
        → 报告 report/extract-report-<date>.{json,md}

口径说明:
- **只有拿到正文（detail_status=ok）的公告才进入抽取**；没正文的保持 `pending`，等 fetch_detail 补抓后重跑；
- 抽取成功的条目状态置 `extracted`；字段缺失只做统计（missing_required / missing_optional），**不入 failed、不影响状态机**，
  因为"公告本身没写这项"与"抽取失败"是两件事；
- 抽取抛异常的条目状态置 `failed` 并累加 retry_count、记录 last_error，可单独重跑；
- 成功率 = 抽取成功条数 / 有正文可抽取条数；异常条目 = 无正文 + 正文字段为空 + 抽取异常。

退出码: 0 正常；1 全部抽取失败；4 没有任何可抽取条目（正文缓存为空）。
"""
import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_REPO / "extractors"))
import config        # noqa: E402
import fetch_detail  # noqa: E402
import store         # noqa: E402
import rules         # noqa: E402

FIELD_ORDER = ["project_name", "tenderee", "agency", "bid_method", "budget", "estimate",
               "control_price", "contract_price", "bid_price", "open_time", "open_place",
               "period", "candidates", "winner", "members", "contact_name", "contact_phone",
               "qualification", "scale", "lots", "region", "stage"]


def _load_rows(day, source):
    path = (config.DAILY_DIR if source == "daily" else config.COLLECT_DIR) / ("%s.json" % day)
    if not path.exists():
        return [], path
    try:
        return json.loads(path.read_text(encoding="utf-8")), path
    except (OSError, json.JSONDecodeError):
        return [], path


def run_extract(day, source="collect", limit=None, dry_run=False, quiet=False):
    config.ensure_dirs()
    rows, source_path = _load_rows(day, source)
    details = fetch_detail.load_details(day)
    if limit:
        rows = rows[:int(limit)]

    conn = None
    if not dry_run:
        conn = store.connect()
        store.init_db(conn)

    stats = {
        "date": day, "source": source, "source_path": str(source_path),
        "total": len(rows), "with_detail": 0, "no_detail": 0, "body_empty": 0,
        "extracted": 0, "failed": 0, "skipped": 0,
        "field_hits": {f: 0 for f in FIELD_ORDER},
        "missing_required_total": 0, "missing_optional_total": 0,
        "missing_required_counter": {},
        "project_ids": set(), "errors": [], "started_at": config.now_stamp(),
        "dry_run": bool(dry_run),
    }

    for row in rows:
        infoid = str(row.get("infoid") or "")
        if not infoid:
            stats["skipped"] += 1
            continue
        detail = details.get(infoid)
        if not detail or detail.get("status") != "ok":
            stats["no_detail"] += 1
            stats["skipped"] += 1
            if not dry_run and conn is not None:
                store.upsert_notice(conn, row, detail=detail or {},
                                    extraction={"status": "empty" if detail else "missing"})
                store.set_status(conn, infoid, store.STATUS_PENDING,
                                 error="" if detail else "未抓取正文", inc_retry=False)
            continue
        stats["with_detail"] += 1
        if not (detail.get("body") or "").strip():
            stats["body_empty"] += 1
            stats["skipped"] += 1
            continue
        ctx = {"title": row.get("title"), "stage": row.get("stage"), "stage_key": row.get("stage_key"),
               "areaname": row.get("areaname"), "pub_time": row.get("pub_time")}
        try:
            result = rules.extract_notice(detail.get("body") or "", ctx)
            result["status"] = "ok"
        except Exception as exc:                                    # noqa: BLE001
            stats["failed"] += 1
            stats["errors"].append({"infoid": infoid, "error": "%s: %s" % (type(exc).__name__, exc)})
            if not dry_run and conn is not None:
                store.upsert_notice(conn, row, detail=detail,
                                    extraction={"status": "failed", "error": str(exc)})
                store.set_status(conn, infoid, store.STATUS_PENDING, error=str(exc), inc_retry=True)
            continue

        fields = result["fields"]
        hit = [f for f in FIELD_ORDER if _present(fields.get(f))]
        stats["extracted"] += 1
        stats["project_ids"].add(result["project_id"])
        stats["missing_required_total"] += len(result["missing_required"])
        stats["missing_optional_total"] += len(result["missing_optional"])
        for f in hit:
            stats["field_hits"][f] = stats["field_hits"].get(f, 0) + 1
        for f in result["missing_required"]:
            stats["missing_required_counter"][f] = stats["missing_required_counter"].get(f, 0) + 1

        if not dry_run and conn is not None:
            store.upsert_notice(conn, row, detail=detail, extraction=result,
                                status=store.STATUS_EXTRACTED)
            store.set_status(conn, infoid, store.STATUS_EXTRACTED)
        if not quiet and stats["extracted"] % 20 == 0:
            print("  ... 已抽取 %d 条（最近：%s）" % (stats["extracted"], infoid[:8]), flush=True)

    if conn is not None:
        stats["projects_rebuilt"] = store.rebuild_projects(conn, day)
        conn.commit()
        try:
            store.record_run(conn, day, "extract", {k: v for k, v in stats.items()
                                                    if k != "project_ids"})
        except Exception:                                          # noqa: BLE001
            pass
        stats["db"] = str(config.DB_PATH)
        conn.close()

    attemptable = stats["with_detail"] - stats["body_empty"]
    stats["attemptable"] = attemptable
    stats["success_rate"] = round(stats["extracted"] / attemptable, 4) if attemptable else 0.0
    stats["abnormal_count"] = stats["no_detail"] + stats["body_empty"] + stats["failed"]
    stats["field_coverage"] = {f: (round(stats["field_hits"].get(f, 0) / stats["extracted"], 4)
                                  if stats["extracted"] else 0.0) for f in FIELD_ORDER}
    stats["project_id_count"] = len(stats["project_ids"])
    stats["finished_at"] = config.now_stamp()
    stats["errors"] = stats["errors"][:50]
    report = _write_report(day, stats)
    stats["report_json"] = str(report[0])
    stats["report_md"] = str(report[1])
    stats.pop("project_ids", None)
    if not quiet:
        _print_summary(stats)
    return stats


def _present(value):
    if value is None:
        return False
    if isinstance(value, dict):
        return value.get("value") is not None or bool((value.get("raw") or "").strip())
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return bool(str(value).strip())


def _write_report(day, stats):
    report_dir = config.REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    js_path = report_dir / ("extract-report-%s.json" % day)
    payload = {k: v for k, v in stats.items() if k != "project_ids"}   # project_ids 是集合，只落计数
    payload["project_id_count"] = stats.get("project_id_count", len(stats.get("project_ids") or ()))
    js_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["# 抽取入库报告 %s" % day, "",
             "- 数据源：%s" % stats["source_path"],
             "- 公告总数：%d 条" % stats["total"],
             "- 有正文可抽取：%d 条（其中正文为空 %d 条）" % (stats["with_detail"], stats["body_empty"]),
             "- 抽取成功：%d 条，抽取异常：%d 条，无正文跳过：%d 条" % (
                 stats["extracted"], stats["failed"], stats["no_detail"]),
             "- 抽取成功率：%.1f%%（成功 / 可抽取）" % (stats["success_rate"] * 100),
             "- 异常条目：%d 条（无正文 + 正文为空 + 抽取异常）" % stats["abnormal_count"],
             "- 归集项目数（project_id）：%d" % stats.get("project_id_count", 0),
             "- 必填字段缺失累计：%d 处；选填字段缺失累计：%d 处" % (
                 stats["missing_required_total"], stats["missing_optional_total"]),
             "", "## 字段命中率", "",
             "| 字段 | 命中条数 | 覆盖率 |", "| --- | --- | --- |"]
    for f in FIELD_ORDER:
        lines.append("| %s | %d | %.1f%% |" % (f, stats["field_hits"].get(f, 0),
                                               stats["field_coverage"].get(f, 0) * 100))
    lines += ["", "## 必填字段缺失分布", "", "| 字段 | 缺失条数 |", "| --- | --- |"]
    for f, n in sorted(stats["missing_required_counter"].items(), key=lambda kv: -kv[1]):
        lines.append("| %s | %d |" % (f, n))
    if stats["errors"]:
        lines += ["", "## 抽取异常明细（前 50 条）", ""]
        for e in stats["errors"]:
            lines.append("- `%s`：%s" % (e.get("infoid"), e.get("error")))
    md_path = report_dir / ("extract-report-%s.md" % day)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return js_path, md_path


def _print_summary(stats):
    print("=" * 68)
    print("抽取入库 %s" % stats["date"])
    print("公告总数        : %d（源：%s）" % (stats["total"], stats["source_path"]))
    print("有正文可抽取    : %d（正文字段为空 %d）" % (stats["with_detail"], stats["body_empty"]))
    print("抽取成功        : %d 条 | 抽取异常 %d 条 | 无正文跳过 %d 条" % (
        stats["extracted"], stats["failed"], stats["no_detail"]))
    print("抽取成功率      : %.1f%%（成功/可抽取 %d）" % (stats["success_rate"] * 100, stats["attemptable"]))
    print("异常条目        : %d 条（无正文 + 正文为空 + 抽取异常）" % stats["abnormal_count"])
    print("归集项目数      : %d" % stats.get("project_id_count", 0))
    print("必填缺失累计    : %d 处 | 选填缺失累计 %d 处" % (
        stats["missing_required_total"], stats["missing_optional_total"]))
    print("-" * 68)
    print("%-16s %-8s %s" % ("字段", "命中", "覆盖率"))
    for f in FIELD_ORDER:
        print("%-16s %-8d %.1f%%" % (f, stats["field_hits"].get(f, 0), stats["field_coverage"].get(f, 0) * 100))
    if stats["missing_required_counter"]:
        print("必填缺失分布    :", json.dumps(stats["missing_required_counter"], ensure_ascii=False))
    print("报告            : %s" % stats.get("report_json"))
    print("                : %s" % stats.get("report_md"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="规则抽取 + SQLite 入库")
    ap.add_argument("--date", default=None)
    ap.add_argument("--source", choices=["collect", "daily"], default="collect")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="只抽取不入库")
    ap.add_argument("--status", action="store_true", help="查看库内状态机统计")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    day = args.date or config.today()
    if args.status:
        conn = store.connect()
        store.init_db(conn)
        print(json.dumps(store.stats(conn, args.date), ensure_ascii=False, indent=1))
        return 0
    stats = run_extract(day, source=args.source, limit=args.limit,
                        dry_run=args.dry_run, quiet=args.quiet)
    summary = {k: v for k, v in stats.items() if k not in ("errors", "field_hits", "field_coverage",
                                                           "missing_required_counter")}
    summary["error_count"] = len(stats["errors"])
    print(json.dumps(summary, ensure_ascii=False))
    if not stats["attemptable"]:
        return 4
    if stats["failed"] == stats["attemptable"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
