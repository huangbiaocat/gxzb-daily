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
import collections
import sys
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

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

            # 底边基线快照表：记录各数据源在各发布日期的原始基线锁定状态
            conn.execute("""
                CREATE TABLE IF NOT EXISTS baseline_snapshots (
                    source TEXT NOT NULL,
                    pub_date TEXT NOT NULL,
                    snapshot_time TEXT NOT NULL,
                    notice_count INTEGER DEFAULT 0,
                    is_locked INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, pub_date)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bs_pub_date ON baseline_snapshots(pub_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bs_source ON baseline_snapshots(source)")

            # 动态检查 scan_records 是否具备 is_baseline 字段
            cols = [row[1] for row in conn.execute("PRAGMA table_info(scan_records)").fetchall()]
            if "is_baseline" not in cols:
                try:
                    conn.execute("ALTER TABLE scan_records ADD COLUMN is_baseline INTEGER DEFAULT 1")
                except Exception:
                    pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_records_baseline ON scan_records(is_baseline)")
            except Exception:
                pass

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

    def has_baseline(self, source: str, pub_date: str, daily_dir: Optional[Path] = None) -> bool:
        """检查指定数据源在指定发布日期是否已建立并锁定底边快照。"""
        s_key = str(source or "").strip()
        p_date = str(pub_date or "").strip()[:10]
        if not p_date:
            return False

        # 1. 检查数据库快照表
        with self._connection() as conn:
            cur = conn.cursor()
            if s_key and s_key not in ("all", "any", "*"):
                cur.execute(
                    "SELECT 1 FROM baseline_snapshots WHERE source = ? AND pub_date = ? AND is_locked = 1 LIMIT 1",
                    (s_key, p_date)
                )
                if cur.fetchone() is not None:
                    return True
                cur.execute(
                    "SELECT 1 FROM baseline_snapshots WHERE source = 'all' AND pub_date = ? AND is_locked = 1 LIMIT 1",
                    (p_date,)
                )
                if cur.fetchone() is not None:
                    return True
            else:
                cur.execute(
                    "SELECT 1 FROM baseline_snapshots WHERE pub_date = ? AND is_locked = 1 LIMIT 1",
                    (p_date,)
                )
                if cur.fetchone() is not None:
                    return True

        # 2. 检查历史归档日常文件
        d_dir = Path(daily_dir) if daily_dir else (self.db_path.parent / "daily" if (self.db_path.parent / "daily").is_dir() else config.DAILY_DIR)
        daily_file = d_dir / f"{p_date}.json"
        if daily_file.is_file():
            try:
                items = json.loads(daily_file.read_text(encoding="utf-8"))
                if not items:
                    return False
                if s_key and s_key not in ("all", "any", "*"):
                    for it in items:
                        link = str(it.get("link") or it.get("detail_url") or "")
                        if s_key == "cz_ygcg" and "cz.gxygcg.com" in link:
                            return True
                        if s_key == "gxzfcg" and "gxzfcg.gov.cn" in link:
                            return True
                        if s_key.startswith("gxggzy") and (not ("cz.gxygcg.com" in link or "gxzfcg.gov.cn" in link)):
                            return True
                    return False
                return True
            except Exception:
                pass
        return False

    def record_baseline_snapshot(
        self,
        source: str,
        pub_date: str,
        notice_count: int = 0,
        snapshot_time: Optional[str] = None,
        is_locked: int = 1,
    ):
        """登记并锁定指定数据源在指定发布日期的底边快照。"""
        s_key = str(source or "all").strip()
        p_date = str(pub_date or "").strip()[:10]
        if not p_date:
            return
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snap_time = snapshot_time or now_str
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO baseline_snapshots (source, pub_date, snapshot_time, notice_count, is_locked, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, pub_date) DO UPDATE SET
                    snapshot_time = excluded.snapshot_time,
                    notice_count = excluded.notice_count,
                    is_locked = excluded.is_locked,
                    updated_at = excluded.updated_at
                """,
                (s_key, p_date, snap_time, int(notice_count or 0), int(is_locked), now_str, now_str)
            )
            conn.commit()

    def get_baseline_snapshot(self, source: str, pub_date: str) -> Optional[Dict[str, Any]]:
        """获取指定数据源与日期的底边快照详情。"""
        with self._connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            row = cur.execute(
                "SELECT * FROM baseline_snapshots WHERE (source = ? OR source = 'all') AND pub_date = ? ORDER BY CASE WHEN source = ? THEN 0 ELSE 1 END LIMIT 1",
                (source, pub_date, source)
            ).fetchone()
            return dict(row) if row else None

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
        is_baseline: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """记录一条扫描条目。
       
        返回值字典包含：
        - is_new: bool, 是否为本系统首次捕获
        - is_delayed: bool, 是否判定为滞后公开
        - delay_days: int, 滞后天数
        - is_baseline: bool, 是否属于底边基线原始数据
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
                "is_baseline": bool(existing.get("is_baseline", 1)),
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
            raw_diff = (scan_d - pub_d).days
            delay = max(0, raw_diff)
        else:
            pub_date_str = scan_date_str
            delay = 0

        # 核心逻辑：基于底边数据库进行判定
        # 检查该源在 pub_date 是否已存在锁定底边
        has_base = self.has_baseline(scan_source, pub_date_str)

        if is_baseline is True or (is_baseline is None and not scan_source.startswith("backscan") and (not has_base or delay == 0)):
            # 无历史底边或明确指定为底边数据：纳入底边基线，绝不判定为滞后公开
            actual_is_baseline = 1
            is_del = 0
            delay = 0
            if p_time_str and scan_date_str == pub_date_str:
                evidence = (
                    f"【正常公开】官网标称发布于 {p_time_str or pub_date_str}，"
                    f"本系统于 {scan_time_str} 首次扫描捕获，正常公开。"
                )
            else:
                evidence = (
                    f"【底边原始数据】官网标称发布于 {p_time_str or pub_date_str}，"
                    f"作为数据源 {scan_source or '未知'} 在 {pub_date_str} 的原始底边基线存证入库。"
                )
        else:
            # 已有历史底边，且此条目在底边锁定后回扫突现：
            actual_is_baseline = 0
            is_del = 1 if delay >= min_delay_days else 0
            
            snap = self.get_baseline_snapshot(scan_source, pub_date_str)
            snap_desc = (
                f"数据源 {scan_source} 于 {snap.get('snapshot_time', '历史归档')} 已锁定 {pub_date_str} 底边基线"
                f"（当时底边共 {snap.get('notice_count', 0)} 条，无此条目）"
            ) if snap else f"历史底边基线已建立"
           
            if is_del:
                evidence = (
                    f"【底边比对确证】{snap_desc}；"
                    f"本系统于 {scan_time_str} 回扫时首次捕获官网标称条目（来源：{scan_source}），"
                    f"确证滞后公开 {delay} 天。"
                )
            else:
                evidence = (
                    f"【回扫补充】官网发布时间 {p_time_str or pub_date_str}，"
                    f"回扫时间 {scan_time_str}（来源：{scan_source}），间隔 {delay} 天，未达滞后阈值。"
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
            "is_baseline": actual_is_baseline,
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
                    delay_days, is_delayed, is_baseline,
                    evidence_text, created_at, updated_at
                ) VALUES (
                    :infoid, :title, :pub_time, :pub_date,
                    :first_scan_time, :first_scan_date,
                    :scan_source, :scan_batch, :center, :link,
                    :delay_days, :is_delayed, :is_baseline,
                    :evidence_text, :created_at, :updated_at
                )
            """, rec)

        return {
            "is_new": True,
            "is_delayed": bool(is_del),
            "delay_days": delay if delay > 0 else 0,
            "is_baseline": bool(actual_is_baseline),
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
        is_baseline: Optional[bool] = None,
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
                is_baseline=is_baseline,
            )
            results.append(res)
        return results

    def get_baseline_stats(self) -> Dict[str, Any]:
        """获取底边扫描数据库与滞后排查统计信息。"""
        with self._connection() as conn:
            cur = conn.cursor()
            total_records = cur.execute("SELECT count(*) FROM scan_records").fetchone()[0]
            baseline_records = cur.execute("SELECT count(*) FROM scan_records WHERE is_baseline = 1").fetchone()[0]
            delayed_records = cur.execute("SELECT count(*) FROM scan_records WHERE is_delayed = 1").fetchone()[0]
           
            snap_total = cur.execute("SELECT count(*) FROM baseline_snapshots").fetchone()[0]
            snap_dates = cur.execute("SELECT count(DISTINCT pub_date) FROM baseline_snapshots").fetchone()[0]
            last_snap_row = cur.execute("SELECT max(snapshot_time) FROM baseline_snapshots").fetchone()
            last_snap = last_snap_row[0] if last_snap_row and last_snap_row[0] else ""
           
            return {
                "total_records": total_records,
                "baseline_records": baseline_records,
                "delayed_records": delayed_records,
                "snapshot_count": snap_total,
                "dates_covered": snap_dates,
                "last_snapshot_time": last_snap,
           }
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
        """从历史 daily 归档文件建立并固化底边扫描数据库基线，彻底清理历史误判。
        
        核心原则（底边扫描铁律）：
        1. 凡是已存在于历史 daily 账册中的公告条目，均为系统历史底边原始数据（Baseline Data）。
        2. 系统未做底边扫描之前的数据，绝不能判定为滞后公开。
        3. 为各数据源在各历史发布日期登记并锁定 baseline_snapshots 快照。
        4. 彻底清理之前误打上的 is_delayed=1 与"滞后公开"/"滞后补录"标签。
        """
        d_dir = Path(daily_dir or config.DAILY_DIR)
        s_dir = Path(state_dir or config.STATE_DIR)
        
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
        baseline_snapshots_created = 0

        # 按时间正序遍历历史文件建立底边基线
        for json_path in sorted(d_dir.glob("2026-*.json")):
            file_date_str = json_path.stem
            file_d = self.parse_date_str(file_date_str)
            if not file_d:
                continue

            try:
                items = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            modified = False
            source_counts = collections.defaultdict(int)

            for it in items:
                infoid = str(it.get("infoid") or "").strip()
                if not infoid:
                    continue

                total_scanned += 1
                pub_time = it.get("pub_time") or it.get("infodatepx") or ""
                pub_d = self.parse_date_str(pub_time)
                pub_date_str = pub_d.strftime("%Y-%m-%d") if pub_d else file_date_str

                # 判定数据源
                link_str = str(it.get("link") or it.get("detail_url") or "")
                center_str = str(it.get("areaname") or it.get("center") or "")
                if "cz.gxygcg.com" in link_str:
                    source_key = "cz_ygcg"
                elif "gxzfcg.gov.cn" in link_str:
                    source_key = "gxzfcg"
                elif center_str:
                    source_key = f"gxggzy_{center_str}"
                else:
                    source_key = "gxggzy"

                source_counts[source_key] += 1
                source_counts["all"] += 1

                idx_seen = index_seen_map.get(infoid)
                first_scan_time = idx_seen if idx_seen else f"{file_date_str} 18:00:00"
                first_scan_date = file_date_str

                evidence = (
                    f"【底边原始数据】官网标称发布于 {pub_time or pub_date_str}，"
                    f"作为 {source_key} 在 {pub_date_str} 的历史底边基线原始数据存证入库。"
                )

                # 将历史条目全部强制作为底边原始数据入库，is_delayed 设为 0
                with self._connection() as conn:
                    conn.execute("""
                        INSERT INTO scan_records (
                            infoid, title, pub_time, pub_date,
                            first_scan_time, first_scan_date,
                            scan_source, center, link,
                            delay_days, is_delayed, is_baseline, evidence_text,
                            created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?,
                            ?, ?,
                            ?, ?, ?,
                            0, 0, 1, ?,
                            datetime('now', 'localtime'), datetime('now', 'localtime')
                        )
                        ON CONFLICT(infoid) DO UPDATE SET
                            is_baseline = 1,
                            is_delayed = 0,
                            delay_days = 0,
                            evidence_text = excluded.evidence_text,
                            updated_at = datetime('now', 'localtime')
                    """, (
                        infoid,
                        it.get("title") or "",
                        pub_time,
                        pub_date_str,
                        first_scan_time,
                        first_scan_date,
                        source_key,
                        center_str,
                        link_str,
                        evidence,
                    ))

                # 清理 daily 数据上的错误滞后标签
                if clean_false_delayed:
                    tags = it.get("focus_tags")
                    if isinstance(tags, list):
                        old_len = len(tags)
                        tags = [t for t in tags if t not in ("滞后补录", "滞后公开")]
                        if len(tags) != old_len:
                            it["focus_tags"] = tags
                            modified = True

                    reasons = it.get("focus_reason")
                    if isinstance(reasons, list):
                        old_len = len(reasons)
                        reasons = [r for r in reasons if not str(r).startswith("滞后公开")]
                        if len(reasons) != old_len:
                            it["focus_reason"] = reasons
                            modified = True

                    if it.get("is_delayed") or it.get("delay_days", 0) > 0:
                        it["is_delayed"] = 0
                        it["delay_days"] = 0
                        it.pop("delayed_reason", None)
                        it.pop("delayed_type", None)
                        it.pop("first_seen_date", None)
                        if not it.get("focus_tags"):
                            it["is_focus"] = 0
                            it["focus_reason"] = []
                        cleaned_false_delayed += 1
                        modified = True

            if modified:
                json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

            # 为该日期的各数据源记录底边基线快照
            for sk, cnt in source_counts.items():
                self.record_baseline_snapshot(
                    source=sk,
                    pub_date=file_date_str,
                    notice_count=cnt,
                    snapshot_time=f"{file_date_str} 23:59:59",
                    is_locked=1,
                )
                baseline_snapshots_created += 1

        # 清理 delayed_registry.json
        reg_file = s_dir / "delayed_registry.json"
        if reg_file.is_file():
            try:
                reg_data = json.loads(reg_file.read_text(encoding="utf-8"))
                reg_mod = False
                for rk, rv in list(reg_data.items()):
                    if rv.get("is_delayed"):
                        rv["is_delayed"] = False
                        rv["delay_days"] = 0
                        rv["evidence_text"] = "已校准为历史底边原始数据"
                        reg_mod = True
                if reg_mod:
                    reg_file.write_text(json.dumps(reg_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        # 清理当天的 delayed_today_*.json
        for dt_path in s_dir.glob("delayed_today_*.json"):
            try:
                dt_path.write_text("[]", encoding="utf-8")
            except Exception:
                pass

        return {
            "total_scanned": total_scanned,
            "cleaned_false_delayed": cleaned_false_delayed,
            "baseline_snapshots_created": baseline_snapshots_created,
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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="底边扫描存证数据库管理工具")
    parser.add_argument("--rebuild-baseline", action="store_true", help="根据历史已归档 daily 数据重建与校准底边基线并清除历史误判")
    parser.add_argument("--stats", action="store_true", help="打印底边数据库存证统计信息")
    args = parser.parse_args()

    db = get_scan_record_db()
    if args.rebuild_baseline:
        print("正在根据历史归档数据重建底边扫描基线并校准数据...")
        res = db.bootstrap_baseline_from_daily(clean_false_delayed=True)
        print(f"校准完成！扫描历史公告条目: {res['total_scanned']}，清除误判条目: {res['cleaned_false_delayed']}，建立底边快照: {res['baseline_snapshots_created']}")
        stats = db.get_baseline_stats()
        print(f"当前底边数据库状态: 总条目={stats['total_records']}, 底边基线条目={stats['baseline_records']}, 确证滞后条目={stats['delayed_records']}, 底边覆盖天数={stats['dates_covered']}")
    elif args.stats:
        stats = db.get_baseline_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        parser.print_help()
