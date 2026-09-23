# -*- coding: utf-8 -*-
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.scan_record_db import ScanRecordDB


class TestScanRecordDB(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "test_scan.sqlite3"
        self.db = ScanRecordDB(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_record_scan_normal_and_delayed(self):
        """测试首次扫描记录：正常公开与滞后公开的判定与不可篡改存证。"""
        # 1. 正常公开测试：今天发布，今天捕获
        res1 = self.db.record_scan(
            infoid="norm-1",
            title="南宁市普通正常公告",
            pub_time="2026-09-23 09:30:00",
            scan_time="2026-09-23 10:00:00",
            scan_source="daily_collect",
        )
        self.assertTrue(res1["is_new"])
        self.assertFalse(res1["is_delayed"])
        self.assertEqual(res1["delay_days"], 0)
        self.assertIn("正常公开", res1["evidence_text"])

        # 2. 滞后公开测试：5 天前发布，今天才首次捕获
        res2 = self.db.record_scan(
            infoid="delayed-1",
            title="某隐匿滞后公开水库工程",
            pub_time="2026-09-18 10:00:00",
            scan_time="2026-09-23 10:00:00",
            scan_source="backscan_30d",
            min_delay_days=2,
        )
        self.assertTrue(res2["is_new"])
        self.assertTrue(res2["is_delayed"])
        self.assertEqual(res2["delay_days"], 5)
        self.assertIn("确证滞后公开 5 天", res2["evidence_text"])
        self.assertIn("backscan_30d", res2["evidence_text"])

        # 3. 不可篡改性测试：再次扫描 delayed-1（如 10 天后），首次捕获时间与存证不能被篡改
        res3 = self.db.record_scan(
            infoid="delayed-1",
            title="标题更新测试",
            pub_time="2026-09-18 10:00:00",
            scan_time="2026-10-05 12:00:00",
        )
        self.assertFalse(res3["is_new"])
        self.assertTrue(res3["is_delayed"])
        self.assertEqual(res3["delay_days"], 5)
        rec = self.db.get_record("delayed-1")
        self.assertEqual(rec["first_scan_time"], "2026-09-23 10:00:00")

    def test_bootstrap_baseline_and_cleaning(self):
        """测试从历史 daily 文件还原基线并清理虚假滞后标记。"""
        daily_dir = self.tmp_dir / "daily"
        state_dir = self.tmp_dir / "state"
        daily_dir.mkdir()
        state_dir.mkdir()

        # 模拟之前被误标记为滞后的历史数据
        falsely_delayed_data = [
            {
                "infoid": "hist-false-del",
                "title": "2026-08-25正常历史项目",
                "pub_time": "2026-08-25 10:00:00",
                "is_delayed": 1,
                "delay_days": 29,
                "focus_tags": ["滞后补录"],
                "focus_reason": ["滞后 29 天补录现身"],
                "is_focus": 1,
            }
        ]
        file_path = daily_dir / "2026-08-25.json"
        file_path.write_text(json.dumps(falsely_delayed_data, ensure_ascii=False), encoding="utf-8")

        stats = self.db.bootstrap_baseline_from_daily(
            daily_dir=daily_dir,
            state_dir=state_dir,
            clean_false_delayed=True,
        )
        self.assertEqual(stats["total_scanned"], 1)
        self.assertEqual(stats["cleaned_false_delayed"], 1)

        # 验证文件已被修正清洗
        cleaned_data = json.loads(file_path.read_text(encoding="utf-8"))[0]
        self.assertEqual(cleaned_data["is_delayed"], 0)
        self.assertEqual(cleaned_data["delay_days"], 0)
        self.assertNotIn("滞后补录", cleaned_data.get("focus_tags", []))
        self.assertNotIn("滞后公开", cleaned_data.get("focus_tags", []))
        self.assertEqual(cleaned_data["is_focus"], 0)


if __name__ == "__main__":
    unittest.main()
