# -*- coding: utf-8 -*-
"""人工纠错工具：撤销公告的滞后公开标记，并永久划入底边原始基线数据。

功能说明：
1. 当用户判定某条公告系由于网络波动、漏扫描等原因被系统误判为滞后公开时；
2. 运行此工具（或通过管理后台点击撤销按钮），即可将该条目划入系统底层原始对比基线；
3. 永久锁定该条目在首次扫描存证库中的基线地位，防止未来任何定时回扫再次误判；
4. 自动清除 daily 归档 JSON 中的滞后与预警标签，并重新渲染对应的单日明细静态 HTML 与全盘搜索索引。
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List

# 确保根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from scripts.scan_record_db import get_scan_record_db
from scripts.delayed_helper import unmark_delayed_item


def unmark_notice(infoid: str, date: Optional[str] = None, notes: str = "人工纠错划入原始对比数据", rebuild_page: bool = True) -> Dict[str, Any]:
    """执行人工纠错与基线归入全流程。"""
    infoid = str(infoid).strip()
    if not infoid:
        return {"ok": False, "error": "必须提供公告 infoid"}

    db = get_scan_record_db()
    db_res = db.unmark_delayed(infoid, notes=notes)
    
    daily_dir = Path(config.DAILY_DIR)
    modified_dates = []
    found_item = None

    # 如果指定了具体日期，优先检查该日
    target_files: List[Path] = []
    if date:
        d_file = daily_dir / f"{date}.json"
        if d_file.is_file():
            target_files.append(d_file)
    
    # 否则遍历所有 daily json 文件
    if not target_files:
        target_files = sorted(daily_dir.glob("*.json"), reverse=True)

    for f_path in target_files:
        try:
            items = json.loads(f_path.read_text(encoding="utf-8"))
            file_modified = False
            for it in items:
                if it.get("infoid") == infoid:
                    found_item = dict(it)
                    unmark_delayed_item(it)
                    file_modified = True
            if file_modified:
                f_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
                modified_dates.append(f_path.stem)
        except Exception:
            pass

    # 重新渲染被修改日期的单日 HTML
    rebuilt_pages = []
    if rebuild_page and modified_dates:
        for d_str in modified_dates:
            try:
                from scripts.build_daily_page import build_daily_page
                out_html = build_daily_page(d_str)
                rebuilt_pages.append(str(out_html))
            except Exception as e:
                pass

    # 刷新搜索索引
    try:
        from scripts.build_search_index import generate_search_index
        generate_search_index()
    except Exception:
        pass

    return {
        "ok": True,
        "infoid": infoid,
        "title": found_item.get("title") if found_item else "",
        "notes": notes,
        "db_updated": db_res.get("ok", False),
        "modified_dates": modified_dates,
        "rebuilt_pages": rebuilt_pages,
        "message": f"公告 {infoid} 已成功解除滞后标记，并已永久划入原始对比基线库！",
    }


def main():
    parser = argparse.ArgumentParser(description="人工撤销公告滞后标记并划入原始对比基线")
    parser.add_argument("--infoid", required=True, help="公告唯一标识 infoid")
    parser.add_argument("--date", help="公告所在日期（如 2026-09-14，可选）")
    parser.add_argument("--notes", default="人工纠错划入原始对比数据", help="撤销原因备注")
    parser.add_argument("--no-rebuild", action="store_true", help="不重新渲染单日页面")
    args = parser.parse_args()

    res = unmark_notice(
        infoid=args.infoid,
        date=args.date,
        notes=args.notes,
        rebuild_page=not args.no_rebuild,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
