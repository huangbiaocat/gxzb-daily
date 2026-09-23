# -*- coding: utf-8 -*-
"""滞后/隐藏发布检测与元数据辅助模块。

功能定义：
1. 计算公告官方标称发布时间与首次扫描捕获日期的间隔天数（delay_days）。
2. 当 delay_days >= 阈值（默认 2 天）时，判定为「滞后发布 / 隐藏补录」公告。
3. 按重点处理：自动标记 is_focus=1，增加 focus_tags=["滞后补录"] 与详细说明。
4. 维护全局首次发现注册表（delayed_registry.json），确保回扫时精准判定条目是历史已有还是新冒出。
"""
import json
import os
from datetime import datetime, date
from pathlib import Path

import config


def parse_date_str(val):
    """安全将字符串或 date 对象转为 date 对象。"""
    if not val:
        return None
    if isinstance(val, (datetime, date)):
        return val if isinstance(val, date) else val.date()
    s = str(val).strip()
    if len(s) >= 10:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def calc_delay_days(pub_time, first_seen_date=None):
    """计算首次发现日期与官方发布日期的间隔天数。
    
    Args:
        pub_time: 官方标称发布时间字符串或日期（如 '2026-08-25 10:00:00' 或 '2026-08-25'）
        first_seen_date: 首次被采集系统发现的日期（默认为今日）
    Returns:
        int: 滞后天数（非负整数）。若解析失败则返回 0。
    """
    p_date = parse_date_str(pub_time)
    s_date = parse_date_str(first_seen_date) or parse_date_str(config.today())
    if not p_date or not s_date:
        return 0
    diff = (s_date - p_date).days
    return max(0, diff)


def is_delayed_notice(pub_time, first_seen_date=None, min_delay_days=None):
    """判断是否属于滞后补录/隐藏现身公告（默认 delay_days >= 2）。"""
    thresh = config.BACKSCAN_MIN_DELAY if min_delay_days is None else int(min_delay_days)
    return calc_delay_days(pub_time, first_seen_date) >= thresh


def annotate_delayed_item(item, first_seen_date=None, min_delay_days=None):
    """对公告字典注入滞后补录标记与重点关注元数据（原地修改并返回）。
    
    规则（按重点处理）：
    1. item["is_delayed"] = 1
    2. item["delay_days"] = delay_days
    3. item["first_seen_date"] = first_seen_date
    4. item["delayed_reason"] = 详细文本描述
    5. item["is_focus"] = 1
    6. item["focus_tags"] 包含 "滞后补录"
    7. item["focus_reason"] 包含滞后补录说明
    """
    if not isinstance(item, dict):
        return item
    
    pub_time = item.get("pub_time") or item.get("infodatepx") or item.get("date") or ""
    s_date = parse_date_str(first_seen_date) or parse_date_str(config.today())
    s_date_str = s_date.strftime("%Y-%m-%d") if s_date else config.today()
    delay_days = calc_delay_days(pub_time, s_date)
    thresh = config.BACKSCAN_MIN_DELAY if min_delay_days is None else int(min_delay_days)
    
    pub_date_str = str(parse_date_str(pub_time) or pub_time[:10])
    
    if delay_days >= thresh:
        item["is_delayed"] = 1
        item["delay_days"] = delay_days
        item["first_seen_date"] = s_date_str
        reason = (
            f"官方标称 {pub_date_str} 发布，于 {s_date_str} 定时回扫捕获"
            f"（滞后 {delay_days} 天补录/隐匿现身）"
        )
        item["delayed_reason"] = reason
        
        # 用户明确要求：按重点处理
        item["is_focus"] = 1
        tags = item.get("focus_tags")
        if not isinstance(tags, list):
            tags = [tags] if tags else []
        if "滞后补录" not in tags:
            tags.append("滞后补录")
        item["focus_tags"] = tags
        
        f_reasons = item.get("focus_reason")
        if not isinstance(f_reasons, list):
            f_reasons = [f_reasons] if f_reasons else []
        f_reason_str = f"滞后 {delay_days} 天补录现身 (官方日期 {pub_date_str} -> 首次捕获 {s_date_str})"
        if f_reason_str not in f_reasons:
            f_reasons.append(f_reason_str)
        item["focus_reason"] = f_reasons
    else:
        item["is_delayed"] = 0
        item["delay_days"] = delay_days
        item["first_seen_date"] = s_date_str
    
    return item


# ------------------------------------------------------------------ 首次发现注册表管理
def get_registry_path(path=None):
    return Path(path) if path else config.DELAYED_REGISTRY_PATH


def load_delayed_registry(path=None):
    """加载持久化的公告首次捕获注册表。若不存在则自动从已有每日归档中初始化。"""
    p = get_registry_path(path)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 自动从现有数据中初始化基线
    return bootstrap_registry_from_files(path=p)


def save_delayed_registry(registry, path=None):
    """原子写入首次捕获注册表。"""
    p = get_registry_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def bootstrap_registry_from_files(daily_dir=None, collect_dir=None, path=None):
    """从现有 daily/*.json 和 collect/*.json 构建初始全局条目索引。"""
    d_dir = Path(daily_dir) if daily_dir else config.DAILY_DIR
    c_dir = Path(collect_dir) if collect_dir else config.COLLECT_DIR
    
    registry = {}
    
    # 1. 扫描所有 daily 文件（按日期升序）
    if d_dir.is_dir():
        for f in sorted(d_dir.glob("*.json")):
            file_date = f.stem
            try:
                items = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for it in items:
                infoid = str(it.get("infoid") or "").strip()
                if not infoid or infoid in registry:
                    continue
                pub_time = it.get("pub_time") or it.get("infodatepx") or ""
                registry[infoid] = {
                    "pub_time": pub_time,
                    "pub_date": pub_time[:10] if len(pub_time) >= 10 else file_date,
                    "first_seen_date": it.get("first_seen_date") or file_date,
                    "is_delayed": int(it.get("is_delayed", 0)),
                    "delay_days": int(it.get("delay_days", 0)),
                }
                
    # 2. 扫描所有 collect 文件补充
    if c_dir.is_dir():
        for f in sorted(c_dir.glob("*.json")):
            file_date = f.stem
            try:
                items = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for it in items:
                infoid = str(it.get("infoid") or "").strip()
                if not infoid or infoid in registry:
                    continue
                pub_time = it.get("pub_time") or it.get("infodatepx") or ""
                registry[infoid] = {
                    "pub_time": pub_time,
                    "pub_date": pub_time[:10] if len(pub_time) >= 10 else file_date,
                    "first_seen_date": file_date,
                    "is_delayed": 0,
                    "delay_days": 0,
                }
                
    if path:
        save_delayed_registry(registry, path)
    return registry


def record_notice_seen(infoid, pub_time, seen_date=None, registry=None, min_delay_days=None):
    """记录条目发现状态并判定是否为滞后补录。
    
    Returns:
        (is_new, delay_days, is_delayed, record)
    """
    infoid = str(infoid).strip()
    if not infoid:
        return False, 0, False, {}
    
    if registry is None:
        registry = load_delayed_registry()
        
    s_date = parse_date_str(seen_date) or parse_date_str(config.today())
    s_date_str = s_date.strftime("%Y-%m-%d") if s_date else config.today()
    thresh = config.BACKSCAN_MIN_DELAY if min_delay_days is None else int(min_delay_days)
    
    if infoid in registry:
        rec = registry[infoid]
        return False, rec.get("delay_days", 0), bool(rec.get("is_delayed")), rec
    
    # 全新发现条目！计算是否滞后
    delay = calc_delay_days(pub_time, s_date)
    is_del = delay >= thresh
    pub_str = str(pub_time or "")
    pub_date = pub_str[:10] if len(pub_str) >= 10 else s_date_str
    
    rec = {
        "pub_time": pub_str,
        "pub_date": pub_date,
        "first_seen_date": s_date_str,
        "first_seen_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_delayed": 1 if is_del else 0,
        "delay_days": delay,
    }
    registry[infoid] = rec
    return True, delay, is_del, rec
