# -*- coding: utf-8 -*-
"""当日报表核验：比对「当日全量采集」与「页面已入库条目」，产出缺失清单。

用法:
    python scripts/diff_missing.py                    # 核验今天
    python scripts/diff_missing.py --date 2026-09-10
    python scripts/diff_missing.py --date 2026-09-10 --strict   # 有缺失时退出码非 0

产物:
    <REPORT_DIR>/missing-YYYY-MM-DD.json   机器可读（供后续自动补采 / 推送消费）
    <REPORT_DIR>/missing-YYYY-MM-DD.md     人工可读清单

判定口径（以官方 infoid 为唯一识别码）:
    缺失   = 全量采集 - 页面
    页面独有 = 页面 - 全量采集（可能为历史遗留 / 跨日公告，需人工确认）

缺失再分两类（判据基于「页面已覆盖发布时间窗」，可复现、不依赖人工记忆）:
    stale_missed 早前漏采：发布时间 ≤ 页面最新条目时间 → 页面快照生成时该公告
                 在官方接口已存在却未入页，属采集遗漏，报告中特别标注
    pending      快照后新增：发布时间 > 页面最新条目时间 → 属页面生成后新发布，
                 等待下一次流水线采集入库，非漏采
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402


def page_infoids(day):
    """从页面 RAW_DATA 中提取 infoid（页面不存在时返回 None）。"""
    page = config.SITE_DIR / ("%s.html" % day)
    if not page.exists():
        return None, page
    html = page.read_text(encoding="utf-8")
    if "const RAW_DATA = [" in html:
        block = html.split("const RAW_DATA = [", 1)[1].split("\n];", 1)[0]
    else:
        block = html
    return set(re.findall(r'"infoid":\s*"([^"]+)"', block)), page


def daily_rows(day):
    """读取当日入库数据（页面数据源），返回 (rows, path)。"""
    p = config.DAILY_DIR / ("%s.json" % day)
    if not p.exists():
        return None, p
    return json.loads(p.read_text(encoding="utf-8")), p


def page_window(rows):
    """页面已覆盖的发布时间窗 (最早, 最晚)；无数据返回 (None, None)。"""
    ts = sorted(str(r.get("pub_time") or "") for r in (rows or []))
    ts = [t for t in ts if t]
    return (ts[0], ts[-1]) if ts else (None, None)


STALE_REASON = ("发布时间落在页面已覆盖时间窗内（≤ 页面最新条目时间），"
                "页面快照生成时该公告在官方接口已存在却未入页，属早前采集遗漏")
STALE_EARLY_REASON = ("发布时间早于页面覆盖起点，可能为跨日遗留公告，"
                      "同属早前漏采，需人工确认")
PENDING_REASON = ("发布时间晚于页面最新条目时间，属页面快照生成后新发布，"
                  "等待下一次流水线采集入库")


def classify_missing(row, win, page_rows):
    """缺失条目归类：stale_missed（早前漏采）/ pending（快照后新增）/ unknown。"""
    if not page_rows:
        return "unknown", "页面/入库数据缺失，无法判定，需人工确认"
    lo, hi = win
    t = str(row.get("pub_time") or "")
    if not t:
        return "unknown", "缺失发布时间，无法判定，需人工确认"
    if lo and t < lo:
        return "stale_missed", STALE_EARLY_REASON
    if hi and t <= hi:
        return "stale_missed", STALE_REASON
    return "pending", PENDING_REASON


def collect_rows(day):
    p = config.COLLECT_DIR / ("%s.json" % day)
    if not p.exists():
        return [], p
    return json.loads(p.read_text(encoding="utf-8")), p


def diff(day, write_report=True):
    rows, collect_path = collect_rows(day)
    infos, page_path = page_infoids(day)
    daily, daily_path = daily_rows(day)
    daily_ids = [str(r.get("infoid") or r.get("id") or "") for r in (daily or [])]
    if infos is None:                       # 页面还没生成时，以入库数据为口径
        infos = set(daily_ids)
    collect_ids = [str(r.get("infoid") or "") for r in rows]
    collect_set = set(collect_ids)

    missing_ids = [i for i in collect_ids if i not in infos]
    extra_ids = [i for i in infos if i not in collect_set]
    by_id = {str(r.get("infoid")): r for r in rows}

    win = page_window(daily)
    missing = []
    for i in missing_ids:
        row = dict(by_id[i])
        row["miss_type"], row["miss_reason"] = classify_missing(row, win, daily)
        missing.append(row)
    missing.sort(key=lambda r: str(r.get("pub_time") or ""))
    stale_missing = [r for r in missing if r["miss_type"] == "stale_missed"]

    result = {
        "date": day,
        "generated_at": config.now_stamp(),
        "collect_file": str(collect_path),
        "page_file": str(page_path),
        "daily_file": str(daily_path),
        "collect_total": len(collect_ids),
        "collect_unique": len(collect_set),
        "page_total": len(infos),
        "daily_total": len(daily_ids),
        "page_window": {"start": win[0], "end": win[1]},
        "missing_count": len(missing),
        "stale_missed_count": len(stale_missing),
        "pending_count": len(missing) - len(stale_missing),
        "extra_count": len(extra_ids),
        "missing": missing,
        "stale_missed": stale_missing,
        "page_only": [i for i in extra_ids],
    }
    if write_report:
        result["report_json"], result["report_md"] = write_reports(result)
    return result


def write_reports(res):
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = res["date"]
    json_path = config.REPORT_DIR / ("missing-%s.json" % day)
    json_path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [
        "# 当日页面缺失条目核验 · %s" % day,
        "",
        "- 生成时间：%s" % res["generated_at"],
        "- 数据源（全量采集）：%s" % res["collect_file"],
        "- 页面文件：%s" % res["page_file"],
        "- 入库文件：%s" % res["daily_file"],
        "",
        "## 统计",
        "",
        "| 口径 | 条数 |",
        "| --- | ---: |",
        "| 当日全量采集（去重） | %d |" % res["collect_unique"],
        "| 页面已展示 | %d |" % res["page_total"],
        "| **缺失（全量 - 页面）** | **%d** |" % res["missing_count"],
        "| ⚠ 其中：早前漏采（页面时间窗内） | %d |" % res.get("stale_missed_count", 0),
        "| 其中：页面快照后新发布（待下次更新） | %d |" % res.get("pending_count", 0),
        "| 页面独有（页面 - 全量） | %d |" % res["extra_count"],
        "",
        "- 页面已覆盖发布时间窗：%s ~ %s" % (res.get("page_window", {}).get("start") or "-",
                                            res.get("page_window", {}).get("end") or "-"),
        "",
        "> 缺失分类判据：发布时间 ≤ 页面最新条目时间 → 页面快照生成时该公告已存在却未入页，"
        "判定为「早前漏采」并在下方特别标注；发布时间晚于页面最新条目时间 → 属快照后新发布，"
        "等待下一次流水线采集入库。",
        "",
    ]

    def row_line(idx, r):
        return "| %d | %s | %s | %s | %s | %s | %s | [详情](%s) |" % (
            idx, r.get("title", ""), r.get("areaname", ""), r.get("industry", ""),
            r.get("stage", ""), r.get("pub_time", ""), r.get("infoid", ""), r.get("link", ""))

    if res["missing"]:
        stale = [r for r in res["missing"] if r.get("miss_type") == "stale_missed"]
        pending = [r for r in res["missing"] if r.get("miss_type") != "stale_missed"]
        if stale:
            lines += ["## ⚠ 早前漏采（需特别标注，共 %d 条）" % len(stale), "",
                      "> 发布时间落在页面已覆盖时间窗内，页面快照生成时该公告在官方接口已存在却未入页。", "",
                      "| # | 标题 | 地市 | 行业 | 业务环节 | 发布时间 | infoid | 官方链接 |",
                      "| ---: | --- | --- | --- | --- | --- | --- | --- |"]
            lines += [row_line(i, r) for i, r in enumerate(stale, 1)]
            lines.append("")
        if pending:
            lines += ["## 页面快照后新发布（待下次流水线更新入库，共 %d 条）" % len(pending), "",
                      "| # | 标题 | 地市 | 行业 | 业务环节 | 发布时间 | infoid | 官方链接 |",
                      "| ---: | --- | --- | --- | --- | --- | --- | --- |"]
            lines += [row_line(i, r) for i, r in enumerate(pending, 1)]
            lines.append("")
        lines += ["", "## 缺失全量明细（按发布时间升序）", "",
                  "| # | 标题 | 地市 | 行业 | 业务环节 | 发布时间 | infoid | 分类 | 官方链接 |",
                  "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for idx, r in enumerate(res["missing"], 1):
            lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | [详情](%s) |" % (
                idx, r.get("title", ""), r.get("areaname", ""), r.get("industry", ""),
                r.get("stage", ""), r.get("pub_time", ""), r.get("infoid", ""),
                "早前漏采" if r.get("miss_type") == "stale_missed" else "快照后新增",
                r.get("link", "")))
        lines += ["", "## 按地市 / 环节分布", ""]
        for field, label in (("areaname", "地市"), ("stage", "业务环节")):
            counter = collections.Counter(r.get(field, "") for r in res["missing"])
            lines.append("- %s：%s" % (label, "，".join("%s %d 条" % (k or "未知", v)
                                                     for k, v in counter.most_common())))
        lines.append("")
    else:
        lines += ["## 缺失明细", "", "无缺失：页面条目与当日全量采集完全一致。", ""]
    if res["extra_count"]:
        lines += ["## 页面独有 infoid（需人工确认）", ""] + ["- %s" % i for i in res["page_only"]] + [""]

    md_path = config.REPORT_DIR / ("missing-%s.md" % day)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return str(json_path), str(md_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="核验当日页面缺失条目")
    ap.add_argument("--date", default=None)
    ap.add_argument("--strict", action="store_true", help="存在缺失时返回退出码 2")
    args = ap.parse_args(argv)
    day = args.date or config.today()
    res = diff(day)
    print("日期            :", day)
    print("全量采集(去重)  :", res["collect_unique"])
    print("页面已展示      :", res["page_total"])
    print("缺失条数        :", res["missing_count"])
    print("  早前漏采      :", res.get("stale_missed_count", 0))
    print("  快照后新增    :", res.get("pending_count", 0))
    print("页面独有条数    :", res["extra_count"])
    for path in (res.get("report_json"), res.get("report_md")):
        if path:
            print("报告            :", path)
    if res.get("stale_missed"):
        print("⚠ 早前漏采明细（页面时间窗内，需特别关注）:")
        for idx, r in enumerate(res["stale_missed"], 1):
            print("  %2d. [%s] [%s] %s  infoid=%s" % (
                idx, r.get("stage", ""), r.get("pub_time", ""),
                r.get("title", "")[:60], r.get("infoid", "")))
    if res["missing_count"]:
        preview = [r for r in res["missing"] if r.get("miss_type") != "stale_missed"][:10]
        for idx, r in enumerate(preview, 1):
            print("  %2d. [%s] %s" % (idx, r.get("stage", ""), r.get("title", "")[:60]))
        if res.get("pending_count", 0) > len(preview):
            print("  ... 其余 %d 条见报告文件" % (res["pending_count"] - len(preview)))
    return 2 if (args.strict and res["missing_count"]) else 0


if __name__ == "__main__":
    sys.exit(main())
