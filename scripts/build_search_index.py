# -*- coding: utf-8 -*-
"""全盘检索索引构建器 (Global Search Index Builder)。

功能说明：
1. 汇总 data/daily/*.json 中的全量历史招投标公告；
2. 按公告发布时间（pub_time / date）严格降序排列（最新发布排在最前面）；
3. 关联首次扫描存证库（ScanRecordDB）校验滞后公开与基线状态；
4. 输出精简高效的 dist/search_index.json，供前端页面实现毫秒级全盘检索；
5. 同时也输出到 data/search_index.json 供 Manager 后端 API 直接使用。
"""

import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from scripts.scan_record_db import get_scan_record_db
from scripts.delayed_helper import calc_delay_info


def generate_search_index() -> Dict[str, Any]:
    """生成全盘检索索引数据并写入 dist/search_index.json 与 data/search_index.json。"""
    daily_dir = Path(config.DAILY_DIR)
    site_dir = Path(config.SITE_DIR)
    site_dir.mkdir(parents=True, exist_ok=True)
    
    db = get_scan_record_db()
    all_records: List[Dict[str, Any]] = []
    seen_ids = set()

    json_files = sorted(daily_dir.glob("*.json"), reverse=True)
    for f in json_files:
        file_date = f.stem
        try:
            items = json.loads(f.read_text(encoding="utf-8"))
            for it in items:
                iid = it.get("infoid")
                if not iid or iid in seen_ids:
                    continue
                seen_ids.add(iid)
                
                # 优先校准存证库状态
                rec = db.get_record(iid)
                is_del = 1 if (rec and rec.get("is_delayed")) else it.get("is_delayed", 0)
                d_days = rec.get("delay_days", 0) if rec else it.get("delay_days", 0)
                d_hours = rec.get("delay_hours", 0) if rec else it.get("delay_hours", 0)
                d_label = rec.get("delay_label") if (rec and rec.get("delay_label")) else it.get("delay_label", "")
                
                if is_del and not d_label:
                    if d_days >= 1:
                        d_label = f"滞后 {d_days}天"
                    elif d_hours >= 1:
                        d_label = f"滞后 {d_hours}小时"
                    else:
                        d_label = "滞后公开"

                pub_time = it.get("pub_time") or it.get("infodatepx") or f"{file_date} 00:00:00"
                
                all_records.append({
                    "infoid": iid,
                    "title": it.get("title") or "",
                    "pub_time": pub_time,
                    "date": file_date,
                    "areaname": it.get("areaname") or it.get("center") or "",
                    "stage": it.get("stage") or it.get("categoryname") or "",
                    "industry": it.get("industry") or "",
                    "source": it.get("source") or it.get("platform") or "广西公共资源交易平台",
                    "link": it.get("link") or it.get("detail_url") or "",
                    "is_delayed": 1 if is_del else 0,
                    "delay_days": d_days,
                    "delay_hours": d_hours,
                    "delay_label": d_label,
                    "delayed_reason": (rec.get("evidence_text") if rec else "") or it.get("delayed_reason", ""),
                    "is_overtime": it.get("is_overtime", 0),
                    "overtime_reason": it.get("overtime_reason", ""),
                    "is_focus": it.get("is_focus", 0),
                    "focus_tags": it.get("focus_tags", []),
                })
        except Exception as e:
            continue

    # 严格按发布时间/日期倒序排列（最新发布的公告排在最前）
    all_records.sort(key=lambda r: str(r.get("pub_time") or r.get("date")), reverse=True)

    # 写入 dist/search_index.json
    out_dist = site_dir / "search_index.json"
    out_dist.write_text(json.dumps(all_records, ensure_ascii=False), encoding="utf-8")
    
    # 同时写入 data/search_index.json 供 Manager 或其他服务直接本地高速读取
    data_dir = Path(config.DATA_DIR)
    out_data = data_dir / "search_index.json"
    out_data.write_text(json.dumps(all_records, ensure_ascii=False), encoding="utf-8")

    return {
        "total_records": len(all_records),
        "dist_path": str(out_dist),
        "data_path": str(out_data),
        "size_kb": round(out_dist.stat().st_size / 1024, 1),
    }


if __name__ == "__main__":
    res = generate_search_index()
    print(f"全盘检索索引构建完成！共收录 {res['total_records']} 条公告，文件大小 {res['size_kb']} KB")
