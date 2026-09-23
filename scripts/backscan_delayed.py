# -*- coding: utf-8 -*-
"""定时回扫历史公告：检测被官方滞后公开或隐匿现身的项目。

核心逻辑：
1. 默认回扫过去 30 天（如当前为 T，回扫 T-30 至 T-1）。
2. 与首次扫描存证库（ScanRecordDB）比对，捕获上次扫描时不存在、如今突然出现的新条目。
3. 若首次发现日期与官方标称发布日期间隔 >= 2 天，标记为「滞后公开」重点公告，并生成存证证据链。
4. 产出今日捕获清单（data/state/delayed_today_{today}.json），并同步回补至历史归属日数据中。

用法:
    python scripts/backscan_delayed.py [--date YYYY-MM-DD] [--days 30] [--min-delay 2] [--dry-run]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

# 项目根目录导入
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from scripts import fetcher
from scripts.scan_record_db import get_scan_record_db
from scripts.delayed_helper import (
    calc_delay_days,
    annotate_delayed_item,
    load_delayed_registry,
    save_delayed_registry,
    record_notice_seen,
    parse_date_str,
)
from scripts.collect import (
    JSON_HEADERS,
    build_payload as build_gx_payload,
    normalize as normalize_gx_record,
)
import urllib.parse
from scripts.collect_cz_ygcg import (
    CZ_LIST_API,
    CZ_REGION_CODE,
    _open_url as open_cz_url,
    normalize_cz_record,
)


def fetch_gx_center_window(center, start_date_str, end_date_str, page_size=500):
    """抓取广西公共资源交易中心指定 center 在 [start_date, end_date] 时间窗内的全量公告。"""
    rn = int(page_size or config.API_PAGE_SIZE)
    pn = 0
    all_records = []
    
    while True:
        condition = [{
            "fieldName": "categorynum",
            "equal": config.CATEGORY_PREFIX,
            "notEqual": None,
            "equalList": None,
            "notEqualList": None,
            "isLike": True,
            "likeType": config.CATEGORY_LIKE_TYPE,
        }]
        window = [{
            "fieldName": "infodatepx",
            "startTime": f"{start_date_str} 00:00:00",
            "endTime": f"{end_date_str} 23:59:59",
        }]
        payload = {
            "token": "", "pn": pn, "rn": rn,
            "sdt": "", "edt": "", "wd": "", "inc_wd": "", "exc_wd": "",
            "fields": "title", "cnum": center,
            "sort": json.dumps({"infodatepx": "0"}), "ssort": "title", "cl": 200,
            "terminal": "", "condition": condition, "time": window, "highlights": "",
            "statistics": None, "unionCondition": [], "accuracy": "",
            "noParticiple": "0", "searchRange": None, "isBusiness": "1",
        }
        
        try:
            resp = fetcher.post_json(config.API_URL, payload, headers=dict(JSON_HEADERS))
        except Exception as exc:
            print(f"  [!] 抓取交易中心 {center} (pn={pn}) 异常: {exc}", flush=True)
            break
            
        result = resp.get("result") or {}
        totalcount = int(result.get("totalcount") or 0)
        records = result.get("records") or []
        
        for r in records:
            norm = normalize_gx_record(r, center)
            if norm and norm.get("infoid"):
                all_records.append(norm)
                
        if not records or len(all_records) >= totalcount or len(records) < rn:
            break
        pn += rn
        
    return all_records


def fetch_cz_ygcg_window(start_date_str, end_date_str):
    """抓取崇左阳光采购平台在 [start_date, end_date] 时间窗内的公告。"""
    all_records = []
    page_num = 1
    page_size = 10
    start_d = parse_date_str(start_date_str)
    end_d = parse_date_str(end_date_str)
    
    while True:
        params = {
            "region_code": CZ_REGION_CODE,
            "page_size": page_size,
            "page": page_num,
        }
        query_str = urllib.parse.urlencode(params)
        url = f"{CZ_LIST_API}?{query_str}"
        try:
            content = open_cz_url(url)
            resp = json.loads(content.decode("utf-8"))
        except Exception as exc:
            print(f"  [!] 抓取崇左阳光采购 (page={page_num}) 异常: {exc}", flush=True)
            break
            
        data_obj = resp.get("data")
        if isinstance(data_obj, dict):
            rows = data_obj.get("data") or []
        elif isinstance(data_obj, list):
            rows = data_obj
        else:
            rows = []
        if not rows:
            break
            
        has_relevant = False
        reached_older_than_window = False
        
        for row in rows:
            norm = normalize_cz_record(row)
            if not norm:
                continue
            item_date = parse_date_str(norm.get("pub_time") or norm.get("date"))
            if not item_date:
                continue
                
            if start_d <= item_date <= end_d:
                all_records.append(norm)
                has_relevant = True
            elif item_date < start_d:
                reached_older_than_window = True
                
        if reached_older_than_window:
            break
        page_num += 1
        if page_num > 50:  # 安全防线
            break
            
    return all_records


def scan_delayed_notices(today_str, days=30, min_delay=2, dry_run=False, sync_history=True):
    """核心回扫逻辑：回扫 [today - days, today - 1] 的数据并比对。"""
    t_date = parse_date_str(today_str) or parse_date_str(config.today())
    today = t_date.strftime("%Y-%m-%d")
    
    start_date = (t_date - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = (t_date - timedelta(days=1)).strftime("%Y-%m-%d")
    
    print(f"=== 开始执行历史回扫任务 ===")
    print(f"  回扫基准日 (今日): {today}")
    print(f"  回扫历史窗口: {start_date} 至 {end_date} (共 {days} 天)")
    print(f"  滞后公开判定阈值: >= {min_delay} 天")
    print(f"  写入模式: {'DRY RUN (只检测不写入)' if dry_run else 'ACTIVE (自动持久化与回补历史)'}")
    
    # 1. 加载首次扫描存证库与已知条目注册表
    scan_db = get_scan_record_db()
    registry = load_delayed_registry()
    print(f"  当前存证库与已知公告条目数: {len(registry)}")
    
    # 2. 从广西公共资源交易中心抓取
    print(f"  正在回扫广西公共资源交易中心 15 个中心...")
    all_scanned = []
    for c in config.CENTERS:
        recs = fetch_gx_center_window(c, start_date, end_date)
        all_scanned.extend(recs)
        
    # 3. 从崇左阳光采购抓取
    print(f"  正在回扫崇左阳光采购平台...")
    cz_recs = fetch_cz_ygcg_window(start_date, end_date)
    all_scanned.extend(cz_recs)
    
    print(f"  回扫获取到 {len(all_scanned)} 条历史时间窗内公告，开始差异比对...")
    
    # 4. 差异比对：找出新露头的公告
    new_notices = []
    delayed_notices = []
    new_by_pub_date = {}
    
    seen_in_batch = set()
    for item in all_scanned:
        infoid = str(item.get("infoid") or "").strip()
        if not infoid or infoid in seen_in_batch:
            continue
        seen_in_batch.add(infoid)
        
        # 检查是否已在存证库或注册表中
        if scan_db.has_record(infoid) or infoid in registry:
            continue
            
        # 全新出现的公告！
        pub_time = item.get("pub_time") or item.get("infodatepx") or ""
        delay_days = calc_delay_days(pub_time, today)
        is_delayed = delay_days >= min_delay
        
        # 注入滞后与重点元数据（按重点处理）
        annotate_delayed_item(item, today, min_delay)
        
        new_notices.append(item)
        if is_delayed:
            delayed_notices.append(item)
            
        # 记录到首次扫描存证数据库与注册表
        if not dry_run:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            scan_db.record_scan(
                infoid=infoid,
                title=item.get("title") or "",
                pub_time=pub_time,
                scan_time=now_str,
                scan_source=f"backscan_{days}d",
                center=item.get("areaname") or item.get("source") or "",
                link=item.get("link") or item.get("detail_url") or "",
                min_delay_days=min_delay,
            )
            record_notice_seen(infoid, pub_time, today, registry=registry, min_delay_days=min_delay)
            
        pub_date = str(parse_date_str(pub_time) or pub_time[:10])
        new_by_pub_date.setdefault(pub_date, []).append(item)
        
    print(f"  差异分析完成:")
    print(f"    - 新增发现条目: {len(new_notices)} 条")
    print(f"    - 其中确证滞后公开条目 (>= {min_delay} 天): {len(delayed_notices)} 条")
    
    # 5. 打印重点滞后公告明细
    if delayed_notices:
        print("\n" + "=" * 60)
        print(f"🚨【重点预警】回扫发现 {len(delayed_notices)} 条被官方滞后公开/隐匿现身公告:")
        for idx, it in enumerate(delayed_notices, 1):
            p_date = it.get("pub_time", "")[:10]
            delay = it.get("delay_days", 0)
            city = it.get("areaname", "") or it.get("source", "")
            print(f"  {idx}. [滞后 {delay} 天] 【{city}】{it.get('title')}")
            print(f"     官方标称: {it.get('pub_time')} | 首次捕获: {today}")
            print(f"     链接: {it.get('link') or it.get('detail_url')}")
        print("=" * 60 + "\n")
    else:
        print("  ✓ 未发现新增滞后公开条目，历史数据完整度正常。")
        
    if dry_run:
        return {
            "today": today,
            "scanned_count": len(all_scanned),
            "new_count": len(new_notices),
            "delayed_count": len(delayed_notices),
            "delayed_notices": delayed_notices,
        }
        
    # 6. 保存注册表
    save_delayed_registry(registry)
    
    # 7. 写入今日回扫发现结果到 state
    today_delayed_path = config.get_delayed_today_path(today)
    today_delayed_path.parent.mkdir(parents=True, exist_ok=True)
    today_delayed_path.write_text(
        json.dumps(delayed_notices, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  已保存今日回扫滞后清单: {today_delayed_path}")
    
    # 8. 同步回补到历史日期的 daily/<pub_date>.json
    if sync_history and new_by_pub_date:
        for p_date, items in new_by_pub_date.items():
            daily_file = config.DAILY_DIR / f"{p_date}.json"
            existing_items = []
            if daily_file.is_file():
                try:
                    existing_items = json.loads(daily_file.read_text(encoding="utf-8"))
                except Exception:
                    existing_items = []
            
            existing_ids = {str(x.get("infoid")): x for x in existing_items if x.get("infoid")}
            added_cnt = 0
            for item in items:
                iid = str(item.get("infoid"))
                if iid not in existing_ids:
                    existing_items.append(item)
                    existing_ids[iid] = item
                    added_cnt += 1
            
            if added_cnt > 0:
                daily_file.parent.mkdir(parents=True, exist_ok=True)
                daily_file.write_text(
                    json.dumps(existing_items, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"  已回补 {added_cnt} 条新公告到历史归档: {daily_file.name}")
                
    return {
        "today": today,
        "scanned_count": len(all_scanned),
        "new_count": len(new_notices),
        "delayed_count": len(delayed_notices),
        "delayed_notices": delayed_notices,
    }


def main():
    parser = argparse.ArgumentParser(description="历史公告回扫（检测滞后公开/隐匿现身项目）")
    parser.add_argument("--date", default=None, help="基准日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days", type=int, default=config.BACKSCAN_DAYS, help="回扫天数（默认 30）")
    parser.add_argument("--min-delay", type=int, default=config.BACKSCAN_MIN_DELAY, help="滞后公开判定阈值天数（默认 2）")
    parser.add_argument("--dry-run", action="store_true", help="只比对扫描，不修改持久化数据")
    parser.add_argument("--no-sync-history", action="store_true", help="不回补历史日期的 daily.json")
    args = parser.parse_args()
    
    target_date = args.date or config.today()
    res = scan_delayed_notices(
        today_str=target_date,
        days=args.days,
        min_delay=args.min_delay,
        dry_run=args.dry_run,
        sync_history=not args.no_sync_history,
    )
    
    # 退出码：0 表示成功
    sys.exit(0)


if __name__ == "__main__":
    main()
