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


def daily_infoids(day):
    p = config.DAILY_DIR / ("%s.json" % day)
    if not p.exists():
        return None, p
    rows = json.loads(p.read_text(encoding="utf-8"))
    return [str(r.get("infoid") or r.get("id") or "") for r in rows], p


def collect_rows(day):
    p = config.COLLECT_DIR / ("%s.json" % day)
    if not p.exists():
        return [], p
    return json.loads(p.read_text(encoding="utf-8")), p


def diff(day, write_report=True):
    rows, collect_path = collect_rows(day)
    infos, page_path = page_infoids(day)
    daily_ids, daily_path = daily_infoids(day)
    if infos is None:                       # 页面还没生成时，以入库数据为口径
        infos = set(daily_ids or [])
    collect_ids = [str(r.get("infoid") or "") for r in rows]
    collect_set = set(collect_ids)

    missing_ids = [i for i in collect_ids if i not in infos]
    extra_ids = [i for i in infos if i not in collect_set]
    by_id = {str(r.get("infoid")): r for r in rows}

    result = {
        "date": day,
        "generated_at": config.now_stamp(),
        "collect_file": str(collect_path),
        "page_file": str(page_path),
        "daily_file": str(daily_path),
        "collect_total": len(collect_ids),
        "collect_unique": len(collect_set),
        "page_total": len(infos),
        "daily_total": len(daily_ids or []),
        "missing_count": len(missing_ids),
        "extra_count": len(extra_ids),
        "missing": [by_id[i] for i in missing_ids],
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
        "| 页面独有（页面 - 全量） | %d |" % res["extra_count"],
        "",
    ]
    if res["missing"]:
        lines += ["## 缺失明细", "",
                  "| # | 标题 | 地市 | 行业 | 业务环节 | 发布时间 | infoid | 官方链接 |",
                  "| ---: | --- | --- | --- | --- | --- | --- | --- |"]
        for idx, r in enumerate(res["missing"], 1):
            lines.append("| %d | %s | %s | %s | %s | %s | %s | [详情](%s) |" % (
                idx, r.get("title", ""), r.get("areaname", ""), r.get("industry", ""),
                r.get("stage", ""), r.get("pub_time", ""), r.get("infoid", ""), r.get("link", "")))
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
    print("页面独有条数    :", res["extra_count"])
    for path in (res.get("report_json"), res.get("report_md")):
        if path:
            print("报告            :", path)
    if res["missing_count"]:
        preview = res["missing"][:10]
        for idx, r in enumerate(preview, 1):
            print("  %2d. [%s] %s" % (idx, r.get("stage", ""), r.get("title", "")[:60]))
        if res["missing_count"] > len(preview):
            print("  ... 其余 %d 条见报告文件" % (res["missing_count"] - len(preview)))
    return 2 if (args.strict and res["missing_count"]) else 0


if __name__ == "__main__":
    sys.exit(main())
