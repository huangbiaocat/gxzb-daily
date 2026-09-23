# -*- coding: utf-8 -*-
"""首次扫描记录存证数据库（First-Scan Record & Evidence Database）。

核心职责：
1. 建立不可篡改的公告首次扫描抓取存证体系（first_scan_time / first_scan_date / scan_source）。
2. 提供确凿的本地证据判定逻辑：
   - 只有首次在系统中捕获、且官网标称发布时间早于系统首次抓取时间 >= 2 天的项目，
     才能凭借本系统扫描时间戳作为铁证判定为「滞后公开」。
   - 已在库中记录过的历史公告，无论何时再次回扫，均不可篡改首次抓取时间，防止误判为滞后。
3. 支持对历史存量档案一键基线校准（Reconcile Baseline），剔除因回扫误判的虚假滞后标记。
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import config


class ScanRecordDB:
    """首次扫描记录与存证数据库管理类。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or config.DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        """初始化存证数据表及索引。"""
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_records (
                    infoid TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    pub_time TEXT,
                    pub_date TEXT,
                    first_scan_time TEXT NOT NULL,
                    first_scan_date TEXT NOT NULL,
                    scan_source TEXT DEFAULT 'daily_collect',
                    scan_batch TEXT,
                    center TEXT,
                    link TEXT,
                    delay_days INTEGER DEFAULT 0,
                    is_delayed INTEGER DEFAULT 0,
                    evidence_text TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_records_date ON scan_records(first_scan_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_records_pub ON scan_records(pub_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_records_delayed ON scan_records(is_delayed)")
            conn.commit()

    @staticmethod
    def parse_date_str(val: Optional[str]) -> Optional[date]:
        """安全提取 YYYY-MM-DD 日期。"""
        if not val:
            return None
        s = str(val).strip()
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            try:
                return datetime.strptime(s[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    def get_record(self, infoid: str) -> Optional[Dict[str, Any]]:
        """获取指定 infoid 的首次扫描存证记录。"""
        if not infoid:
            return None
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM scan_records WHERE infoid = ?", (str(infoid).strip(),)).fetchone()
            if row:
                return dict(row)
        return None

    def has_record(self, infoid: str) -> bool:
        """检查指定 infoid 是否已在存证库中。"""
        if not infoid:
            return False
        with self._connection() as conn:
            row = conn.execute("SELECT 1 FROM scan_records WHERE infoid = ?", (str(infoid).strip(),)).fetchone()
            return row is not None

    def record_scan(
        self,
        infoid: str,
        title: str,
        pub_time: Optional[str],
        scan_time: Optional[str] = None,
        scan_source: str = "daily_collect",
        scan_batch: Optional[str] = None,
        center: str = "",
        link: str = "",
        min_delay_days: int = 2,
    ) -> Dict[str, Any]:
        """记录一条扫描条目。
        
        返回值字典包含：
        - is_new: bool, 是否为本系统首次捕获
        - is_delayed: bool, 是否判定为滞后公开
        - delay_days: int, 滞后天数
        - evidence_text: str, 证据描述
        - record: Dict[str, Any], 存证记录详情
        """
        infoid = str(infoid).strip()
        if not infoid:
            raise ValueError("infoid 不能为空")

        existing = self.get_record(infoid)
        if existing:
            return {
                "is_new": False,
                "is_delayed": bool(existing["is_delayed"]),
                "delay_days": int(existing["delay_days"] or 0),
                "evidence_text": existing.get("evidence_text") or "",
                "record": existing,
            }

        now_dt = datetime.now()
        scan_time_str = scan_time or now_dt.strftime("%Y-%m-%d %H:%M:%S")
        scan_d = self.parse_date_str(scan_time_str) or now_dt.date()
        scan_date_str = scan_d.strftime("%Y-%m-%d")

        p_time_str = str(pub_time or "").strip()
        pub_d = self.parse_date_str(p_time_str)
        if pub_d:
            pub_date_str = pub_d.strftime("%Y-%m-%d")
            delay = (scan_d - pub_d).days
        else:
            pub_date_str = scan_date_str
            delay = 0

        is_del = 1 if delay >= min_delay_days else 0
        if is_del:
            evidence = (
                f"【存证判定】官网标称发布于 {p_time_str or pub_date_str}，"
                f"本系统于 {scan_time_str} 首次扫描捕获（来源：{scan_source}），确证滞后公开 {delay} 天。"
            )
        else:
            evidence = (
                f"【存证判定】官网标称发布于 {p_time_str or pub_date_str}，"
                f"本系统于 {scan_time_str} 首次扫描捕获，正常公开。"
            )

        now_iso = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        rec = {
            "infoid": infoid,
            "title": title or "",
            "pub_time": p_time_str,
            "pub_date": pub_date_str,
            "first_scan_time": scan_time_str,
            "first_scan_date": scan_date_str,
            "scan_source": scan_source,
            "scan_batch": scan_batch or "",
            "center": center or "",
            "link": link or "",
            "delay_days": delay if delay > 0 else 0,
            "is_delayed": is_del,
            "evidence_text": evidence,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        with self._connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO scan_records (
                    infoid, title, pub_time, pub_date,
                    first_scan_time, first_scan_date,
                    scan_source, scan_batch, center, link,
                    delay_days, is_delayed, evidence_text,
                    created_at, updated_at
                ) VALUES (
                    :infoid, :title, :pub_time, :pub_date,
                    :first_scan_time, :first_scan_date,
                    :scan_source, :scan_batch, :center, :link,
                    :delay_days, :is_delayed, :evidence_text,
                    :created_at, :updated_at
                )
            """, rec)

        return {
            "is_new": True,
            "is_delayed": bool(is_del),
            "delay_days": delay if delay > 0 else 0,
            "evidence_text": evidence,
            "record": rec,
        }

    def batch_record_scans(
        self,
        items: List[Dict[str, Any]],
        scan_time: Optional[str] = None,
        scan_source: str = "daily_collect",
        scan_batch: Optional[str] = None,
        min_delay_days: int = 2,
    ) -> List[Dict[str, Any]]:
        """批量记录扫描条目。"""
        results = []
        for it in items:
            infoid = it.get("infoid")
            if not infoid:
                continue
            title = it.get("title") or ""
            pub_time = it.get("pub_time") or it.get("infodatepx") or ""
            center = it.get("center") or it.get("areaname") or ""
            link = it.get("link") or ""
            res = self.record_scan(
                infoid=infoid,
                title=title,
                pub_time=pub_time,
                scan_time=scan_time,
                scan_source=scan_source,
                scan_batch=scan_batch,
                center=center,
                link=link,
                min_delay_days=min_delay_days,
            )
            results.append(res)
        return results

    def get_delayed_records(self, scan_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取滞后公开的记录列表。"""
        query = "SELECT * FROM scan_records WHERE is_delayed = 1"
        params = []
        if scan_date:
            query += " AND first_scan_date = ?"
            params.append(scan_date)
        query += " ORDER BY first_scan_date DESC, delay_days DESC"
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def count_delayed(self, scan_date: Optional[str] = None) -> int:
        """统计滞后公开条目数量。"""
        query = "SELECT count(*) FROM scan_records WHERE is_delayed = 1"
        params = []
        if scan_date:
            query += " AND first_scan_date = ?"
            params.append(scan_date)
        with self._connection() as conn:
            return conn.execute(query, params).fetchone()[0]

    def bootstrap_baseline_from_daily(
        self,
        daily_dir: Optional[Path] = None,
        state_dir: Optional[Path] = None,
        clean_false_delayed: bool = True,
    ) -> Dict[str, int]:
        """从历史 daily 归档文件建立存证基线，并校准历史数据。
        
        逻辑依据：
        1. 每个 data/daily/YYYY-MM-DD.json 文件代表系统在该日抓取归档的历史账册。
        2. 如果条目原本就在 YYYY-MM-DD.json 中，且标称发布时间即为 YYYY-MM-DD（或相邻当天/跨夜），
           其首次扫描时间即为该文件日期，为【正常公开】，绝非滞后公开！
        3. 清除之前因盲目回扫误打上的 is_delayed=1 与误加的 "滞后补录" / "滞后公开" 标签。
        """
        d_dir = Path(daily_dir or config.DAILY_DIR)
        s_dir = Path(state_dir or config.STATE_DIR)
        
        # 尝试从 index.json 获得最精确的 first_seen 时间戳
        index_seen_map = {}
        index_file = s_dir / "index.json"
        if index_file.is_file():
            try:
                idx_data = json.loads(index_file.read_text(encoding="utf-8"))
                for iid, meta in idx_data.get("items", {}).items():
                    fs = meta.get("first_seen")
                    if fs:
                        index_seen_map[iid] = fs
            except Exception:
                pass

        total_scanned = 0
        cleaned_false_delayed = 0
        genuinely_delayed = 0

        # 按时间正序遍历历史文件建立基线
        for json_path in sorted(d_dir.glob("2026-*.json")):
            file_date_str = json_path.stem  # e.g. 2026-08-24
            file_d = self.parse_date_str(file_date_str)
            if not file_d:
                continue

            try:
                items = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            modified = False
            for it in items:
                infoid = str(it.get("infoid") or "").strip()
                if not infoid:
                    continue

                total_scanned += 1
                pub_time = it.get("pub_time") or it.get("infodatepx") or ""
                pub_d = self.parse_date_str(pub_time)

                # 获取条目首次出现的确切时间与日期
                existing = self.get_record(infoid)
                if existing:
                    first_scan_time = existing["first_scan_time"]
                    first_scan_date = existing["first_scan_date"]
                    delay_days = existing["delay_days"]
                    is_delayed = existing["is_delayed"]
                    evidence = existing["evidence_text"]
                else:
                    # 历史归档文件的日期即为该公告当初被系统纳入账册归档的日期
                    idx_seen = index_seen_map.get(infoid)
                    if idx_seen and idx_seen.startswith(file_date_str):
                        first_scan_time = idx_seen
                    else:
                        first_scan_time = f"{file_date_str} 18:00:00"
                    first_scan_date = file_date_str
                    first_d = file_d

                    if pub_d:
                        delay_days = (first_d - pub_d).days
                    else:
                        delay_days = 0

                    if delay_days >= 2:
                        is_delayed = 1
                        genuinely_delayed += 1
                        evidence = (
                            f"【存证判定】官网标称发布于 {pub_time}，"
                            f"本系统于 {first_scan_time} 首次扫描捕获，确证滞后公开 {delay_days} 天。"
                        )
                    else:
                        is_delayed = 0
                        delay_days = 0
                        evidence = (
                            f"【存证判定】官网标称发布于 {pub_time}，"
                            f"本系统于 {first_scan_time} 首次扫描捕获，正常公开。"
                        )

                    # 插入存证库
                    with self._connection() as conn:
                        conn.execute("""
                            INSERT OR REPLACE INTO scan_records (
                                infoid, title, pub_time, pub_date,
                                first_scan_time, first_scan_date,
                                scan_source, center, link,
                                delay_days, is_delayed, evidence_text,
                                created_at, updated_at
                            ) VALUES (
                                ?, ?, ?, ?,
                                ?, ?,
                                'baseline_archive', ?, ?,
                                ?, ?, ?,
                                datetime('now', 'localtime'), datetime('now', 'localtime')
                            )
                        """, (
                            infoid,
                            it.get("title") or "",
                            pub_time,
                            pub_d.strftime("%Y-%m-%d") if pub_d else file_date_str,
                            first_scan_time,
                            first_scan_date,
                            it.get("areaname") or it.get("center") or "",
                            it.get("link") or "",
                            delay_days,
                            is_delayed,
                            evidence,
                        ))

                # 校准清理条目上的属性
                if clean_false_delayed:
                    tags = it.get("focus_tags")
                    if isinstance(tags, list):
                        old_len = len(tags)
                        # 移除任何旧的"滞后补录"标签
                        tags = [t for t in tags if t not in ("滞后补录", "滞后公开")]
                        if is_delayed:
                            tags.append("滞后公开")
                        if len(tags) != old_len or it.get("focus_tags") != tags:
                            it["focus_tags"] = tags
                            modified = True

                    if not is_delayed and (it.get("is_delayed") or it.get("delay_days", 0) > 0):
                        it["is_delayed"] = 0
                        it["delay_days"] = 0
                        it.pop("delayed_reason", None)
                        it.pop("first_seen_date", None)
                        # 如果没有其他重点标签，重置 is_focus
                        if not it.get("focus_tags"):
                            it["is_focus"] = 0
                            it["focus_reason"] = []
                        cleaned_false_delayed += 1
                        modified = True
                    elif is_delayed:
                        it["is_delayed"] = 1
                        it["delay_days"] = delay_days
                        it["delayed_type"] = "滞后公开"
                        it["delayed_reason"] = evidence
                        it["first_seen_date"] = first_scan_date
                        it["is_focus"] = 1
                        modified = True

            if modified:
                json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "total_scanned": total_scanned,
            "cleaned_false_delayed": cleaned_false_delayed,
            "genuinely_delayed": genuinely_delayed,
        }


# 全局单例辅助获取
_global_scan_db: Optional[ScanRecordDB] = None


def get_scan_record_db(db_path: Optional[Path] = None) -> ScanRecordDB:
    """获取 ScanRecordDB 实例（支持动态感知 config.DB_PATH 变化）。"""
    global _global_scan_db
    target_path = Path(db_path or config.DB_PATH)
    if _global_scan_db is None or _global_scan_db.db_path != target_path:
        _global_scan_db = ScanRecordDB(target_path)
    return _global_scan_db
