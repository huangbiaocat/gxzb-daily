# -*- coding: utf-8 -*-
"""根据当前最新的重点跟踪信息与展示基础配置（重点关键词/项目/业主/类型/金额门槛），
重新计算并标注历史指定日期或全部历史日期的入库数据（data/daily 与 data/collect），
并自动触发重新生成历史日期的 HTML 页面和首页归档。

用法:
    python scripts/reapply_rules.py [--date YYYY-MM-DD] [--all]
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config
from scripts.collect import normalize

def _reapply_item(item, day):
    """重新应用规则：区分崇左阳光采购与普通公共资源交易中心公告。"""
    is_cz = (
        item.get("source") == "崇左阳光采购"
        or "cz.gxygcg.com" in str(item.get("link", ""))
        or "gxygcg.com" in str(item.get("link", ""))
        or item.get("areaname") == "崇左阳光采购"
    )
    if is_cz:
        # 崇左阳光采购：保留原始 link、pub_time、source、areaname，仅刷新重点关注规则
        title = item.get("title", "")
        owner = item.get("owner", "")
        industry = "房建市政工程" if item.get("industry") in ("房建市政", "房建市政工程") else item.get("industry", "房建市政工程")
        item["industry"] = industry
        item["source"] = "崇左阳光采购"
        item["areaname"] = "崇左市"

        reasons = []
        proj_hits = [p for p in getattr(config, "FOCUS_PROJECTS", []) if config.fuzzy_match(p, title)]
        if proj_hits:
            reasons.extend(["命中重点项目: %s" % p for p in proj_hits])

        owner_hits = [o for o in getattr(config, "FOCUS_OWNERS", []) if (config.fuzzy_match(o, title) or (owner and config.fuzzy_match(o, owner)))]
        if owner_hits:
            reasons.extend(["命中重点业主: %s" % o for o in owner_hits])

        type_hits = [t for t in getattr(config, "FOCUS_PROJECT_TYPES", []) if t in industry or config.fuzzy_match(t, title)]
        if type_hits:
            reasons.extend(["命中重点类型: %s" % t for t in type_hits])

        raw_kw_hits = [k for k in getattr(config, "FOCUS_KEYWORDS", []) if k in title]
        kw_hits = []
        if raw_kw_hits:
            min_amount = getattr(config, "FOCUS_MIN_AMOUNT", 0.0) or 0.0
            if min_amount > 0:
                amt = config.extract_amount_from_title(title)
                if amt is not None and amt >= min_amount:
                    try:
                        from extractors.normalize import format_money
                    except ImportError:
                        def format_money(v):
                            return f"{v/10000:.2f}万" if v >= 10000 else f"{v:.2f}元"
                    kw_hits = raw_kw_hits
                    reasons.extend(["命中关键词: %s (金额%s >= 门槛%s)" % (k, format_money(amt), format_money(min_amount)) for k in kw_hits])
            else:
                kw_hits = raw_kw_hits
                reasons.extend(["命中关键词: %s" % k for k in kw_hits])

        item["is_focus"] = 1 if (proj_hits or owner_hits or type_hits or kw_hits) else 0
        item["focus_reason"] = reasons
        item["focus_reasons"] = reasons
        focus_tags = []
        if proj_hits: focus_tags.append("重点项目")
        if owner_hits: focus_tags.append("重点业主")
        if type_hits: focus_tags.append("重点类型")
        if kw_hits: focus_tags.append("重点关键词")
        item["focus_tags"] = focus_tags
        return item
    else:
        return normalize(item, day)


def reapply_for_date(day: str) -> dict:
    """针对单一日期执行业务规则重标与页面重建。"""
    daily_file = config.DAILY_DIR / f"{day}.json"
    collect_file = config.COLLECT_DIR / f"{day}.json"

    updated_daily_count = 0
    updated_collect_count = 0
    focus_daily_count = 0
    focus_collect_count = 0

    # 1. 重新标注 data/daily/<day>.json
    if daily_file.exists():
        try:
            items = json.loads(daily_file.read_text(encoding="utf-8"))
            if isinstance(items, list):
                new_items = []
                for item in items:
                    norm = _reapply_item(item, day)
                    if norm:
                        new_items.append(norm)
                        if norm.get("is_focus"):
                            focus_daily_count += 1
                daily_file.write_text(json.dumps(new_items, ensure_ascii=False, indent=2), encoding="utf-8")
                updated_daily_count = len(new_items)
        except Exception as e:
            print(f"[{day}] 处理 daily 文件出错: {e}")

    # 2. 重新标注 data/collect/<day>.json
    if collect_file.exists():
        try:
            c_data = json.loads(collect_file.read_text(encoding="utf-8"))
            is_dict = isinstance(c_data, dict)
            items = c_data.get("items", []) if is_dict else c_data
            if isinstance(items, list):
                new_items = []
                for item in items:
                    norm = _reapply_item(item, day)
                    if norm:
                        new_items.append(norm)
                        if norm.get("is_focus"):
                            focus_collect_count += 1
                if is_dict:
                    c_data["items"] = new_items
                    out_data = c_data
                else:
                    out_data = new_items
                collect_file.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
                updated_collect_count = len(new_items)
        except Exception as e:
            print(f"[{day}] 处理 collect 文件出错: {e}")

    # 3. 重新生成当日页面
    if daily_file.exists():
        py_exe = sys.executable
        sub = subprocess.run(
            [py_exe, str(REPO_ROOT / "scripts" / "build_daily_page.py"), "--date", day],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True
        )
        if sub.returncode != 0:
            print(f"[{day}] build_daily_page.py 执行失败:\n{sub.stderr}")

    return {
        "date": day,
        "daily_count": updated_daily_count,
        "daily_focus": focus_daily_count,
        "collect_count": updated_collect_count,
        "collect_focus": focus_collect_count,
    }


def main():
    ap = argparse.ArgumentParser(description="根据当前最新的重点跟踪信息与展示基础配置重新计算并标注历史数据")
    ap.add_argument("--date", help="指定历史日期 YYYY-MM-DD，缺省时如果指定 --all 则处理全部")
    ap.add_argument("--all", action="store_true", help="重新计算并标注所有历史日期")
    args = ap.parse_args()

    if not args.date and not args.all:
        args.date = datetime.date.today().strftime("%Y-%m-%d")

    dates_to_process = []
    if args.all:
        found_dates = set()
        for p in config.DAILY_DIR.glob("*.json"):
            if len(p.stem) == 10 and p.stem[4] == "-" and p.stem[7] == "-":
                found_dates.add(p.stem)
        for p in config.COLLECT_DIR.glob("*.json"):
            if len(p.stem) == 10 and p.stem[4] == "-" and p.stem[7] == "-":
                found_dates.add(p.stem)
        dates_to_process = sorted(found_dates, reverse=True)
    else:
        dates_to_process = [args.date]

    print(f"开始根据最新业务配置重标历史数据，共 {len(dates_to_process)} 个日期...")
    results = []
    for day in dates_to_process:
        print(f"-> 正在处理 {day} ...")
        res = reapply_for_date(day)
        results.append(res)
        print(f"   完成: 日报 {res['daily_count']} 条 (重点 {res['daily_focus']}) | 全量 {res['collect_count']} 条 (重点 {res['collect_focus']})")

    # 4. 重新构建首页归档
    print("-> 重新构建首页/归档索引...")
    sub = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "build_archive_page.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True
    )
    if sub.returncode == 0:
        print("首页/归档索引更新成功")
    else:
        print(f"build_archive_page.py 失败:\n{sub.stderr}")

    # 5. 自动同步 VPS（若开启）
    if getattr(config, "AUTO_UPLOAD_VPS", True):
        print("-> 自动同步最新页面至 VPS 云端站点...")
        sub_vps = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "upload_vps.py")],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True
        )
        if sub_vps.returncode == 0:
            print("VPS 云端同步成功！")
        else:
            print(f"VPS 同步告警:\n{sub_vps.stderr or sub_vps.stdout}")

    print("全部重新计算与标注完成！")


if __name__ == "__main__":
    main()
