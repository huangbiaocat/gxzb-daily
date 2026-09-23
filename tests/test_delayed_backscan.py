# -*- coding: utf-8 -*-
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import config
from scripts.notify_wechat import build_rich_summary
from scripts.delayed_helper import (
    calc_delay_days,
    is_delayed_notice,
    annotate_delayed_item,
    load_delayed_registry,
    save_delayed_registry,
    record_notice_seen,
    bootstrap_registry_from_files,
)
from scripts.backscan_delayed import scan_delayed_notices


class TestDelayedBackscan(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.daily_dir = self.tmp_dir / "daily"
        self.collect_dir = self.tmp_dir / "collect"
        self.state_dir = self.tmp_dir / "state"
        self.daily_dir.mkdir(parents=True)
        self.collect_dir.mkdir(parents=True)
        self.state_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_delay_calculation(self):
        """测试滞后天数计算与阈值判断（用户决定：阈值>=2）。"""
        today = "2026-09-23"
        # 当天发布：0 天
        self.assertEqual(calc_delay_days("2026-09-23 09:00:00", today), 0)
        self.assertFalse(is_delayed_notice("2026-09-23 09:00:00", today, min_delay_days=2))
        # 昨天发布：1 天（常规跨夜同步，不判定为滞后隐匿）
        self.assertEqual(calc_delay_days("2026-09-22 22:30:00", today), 1)
        self.assertFalse(is_delayed_notice("2026-09-22 22:30:00", today, min_delay_days=2))
        # 2 天前发布：2 天（>= 2，判定为滞后补录）
        self.assertEqual(calc_delay_days("2026-09-21 14:00:00", today), 2)
        self.assertTrue(is_delayed_notice("2026-09-21 14:00:00", today, min_delay_days=2))
        # 28 天前发布（如 2026-08-26）：28 天，强烈判定为隐匿补录
        self.assertEqual(calc_delay_days("2026-08-26 10:00:00", today), 28)
        self.assertTrue(is_delayed_notice("2026-08-26 10:00:00", today, min_delay_days=2))

    def test_annotate_delayed_item(self):
        """测试滞后条目注记与重点关注属性注入（用户决定：按重点处理）。"""
        today = "2026-09-23"
        item = {
            "infoid": "test-12345",
            "title": "崇左市某隐匿水利工程招标公告",
            "pub_time": "2026-09-05 10:00:00",
            "is_focus": 0,
            "focus_tags": [],
            "focus_reason": [],
        }
        annotate_delayed_item(item, today, min_delay_days=2)
        
        self.assertEqual(item["is_delayed"], 1)
        self.assertEqual(item["delay_days"], 18)
        self.assertEqual(item["first_seen_date"], "2026-09-23")
        # 按重点处理核验
        self.assertEqual(item["is_focus"], 1)
        self.assertIn("滞后补录", item["focus_tags"])
        self.assertTrue(any("滞后 18 天" in r for r in item["focus_reason"]))
        self.assertIn("滞后 18 天补录", item["delayed_reason"])

    def test_registry_bootstrap_and_record(self):
        """测试注册表从历史 daily 文件初始化及新条目识别。"""
        reg_file = self.state_dir / "delayed_registry.json"
        
        # 准备基线数据
        hist_data = [
            {"infoid": "hist-001", "pub_time": "2026-09-01 10:00:00", "title": "历史项目1"},
            {"infoid": "hist-002", "pub_time": "2026-09-01 11:00:00", "title": "历史项目2"},
        ]
        (self.daily_dir / "2026-09-01.json").write_text(json.dumps(hist_data), encoding="utf-8")
        
        reg = bootstrap_registry_from_files(
            daily_dir=self.daily_dir,
            collect_dir=self.collect_dir,
            path=reg_file,
        )
        self.assertIn("hist-001", reg)
        self.assertIn("hist-002", reg)
        self.assertEqual(reg["hist-001"]["first_seen_date"], "2026-09-01")
        
        # 再次记录已知条目：不应判定为新条目
        is_new, delay, is_del, _ = record_notice_seen(
            "hist-001", "2026-09-01 10:00:00", seen_date="2026-09-23", registry=reg, min_delay_days=2
        )
        self.assertFalse(is_new)
        
        # 记录全新冒出的条目（官方标称 2026-09-05 发布）
        is_new2, delay2, is_del2, rec2 = record_notice_seen(
            "hidden-new-999", "2026-09-05 08:00:00", seen_date="2026-09-23", registry=reg, min_delay_days=2
        )
        self.assertTrue(is_new2)
        self.assertEqual(delay2, 18)
        self.assertTrue(is_del2)
        self.assertEqual(rec2["is_delayed"], 1)
        self.assertEqual(rec2["first_seen_date"], "2026-09-23")

    @patch("scripts.backscan_delayed.fetch_gx_center_window")
    @patch("scripts.backscan_delayed.fetch_cz_ygcg_window")
    def test_scan_delayed_notices_flow(self, mock_cz, mock_gx):
        """测试回扫主流程：模拟发现隐藏公告并验证产物。"""
        today = "2026-09-23"
        reg_file = self.state_dir / "delayed_registry.json"
        
        # 历史基线：2026-09-10 有一笔已存项目
        existing_hist = [
            {"infoid": "known-10", "pub_time": "2026-09-10 10:00:00", "title": "旧已知项目"}
        ]
        (self.daily_dir / "2026-09-10.json").write_text(json.dumps(existing_hist), encoding="utf-8")
        bootstrap_registry_from_files(self.daily_dir, self.collect_dir, reg_file)
        
        # 模拟回扫接口返回：包含已知项目 + 一条被官方隐匿在 2026-09-10 发布的全新项目
        new_hidden_notice = {
            "infoid": "new-hidden-20260910",
            "title": "某被隐匿13天的重点桥梁建设项目",
            "pub_time": "2026-09-10 15:30:00",
            "areaname": "南宁市",
            "stage": "招标公告",
            "link": "https://example.com/item?infoid=new-hidden-20260910",
        }
        
        def fake_gx(center, s_d, e_d, page_size=500):
            if center == config.CENTERS[0]:
                return [dict(existing_hist[0]), dict(new_hidden_notice)]
            return []
            
        mock_gx.side_effect = fake_gx
        mock_cz.return_value = []
        
        with patch("config.DELAYED_REGISTRY_PATH", reg_file), \
             patch("config.DAILY_DIR", self.daily_dir), \
             patch("config.STATE_DIR", self.state_dir):
             
            res = scan_delayed_notices(
                today_str=today,
                days=30,
                min_delay=2,
                dry_run=False,
                sync_history=True,
            )
            
            self.assertEqual(res["new_count"], 1)
            self.assertEqual(res["delayed_count"], 1)
            del_item = res["delayed_notices"][0]
            self.assertEqual(del_item["infoid"], "new-hidden-20260910")
            self.assertEqual(del_item["is_delayed"], 1)
            self.assertEqual(del_item["delay_days"], 13)
            self.assertEqual(del_item["is_focus"], 1)
            self.assertIn("滞后补录", del_item["focus_tags"])
            
            # 验证今日发现文件已生成
            today_path = self.state_dir / f"delayed_today_{today}.json"
            self.assertTrue(today_path.is_file())
            saved_today = json.loads(today_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_today), 1)
            
            # 验证历史日期 2026-09-10.json 已自动回补
            hist_updated = json.loads((self.daily_dir / "2026-09-10.json").read_text(encoding="utf-8"))
            self.assertEqual(len(hist_updated), 2)
            hist_ids = {x["infoid"] for x in hist_updated}
            self.assertIn("new-hidden-20260910", hist_ids)

    def test_notify_wechat_delayed_formatting(self):
        """测试企业微信通知排版包含滞后预警（按重点排在显眼位置）。"""
        today = "2026-09-23"
        today_path = self.state_dir / f"delayed_today_{today}.json"
        delayed_items = [
            {
                "infoid": "del-001",
                "title": "南宁市某地下综合管廊施工隐匿补录工程",
                "pub_time": "2026-09-08 09:00:00",
                "areaname": "南宁市",
                "stage": "招标公告",
                "delay_days": 15,
                "link": "https://example.com/item1",
            }
        ]
        today_path.write_text(json.dumps(delayed_items), encoding="utf-8")
        
        normal_items = [
            {
                "infoid": "norm-001",
                "title": "今日正常发布的普通项目",
                "pub_time": "2026-09-23 09:00:00",
                "areaname": "南宁市",
                "stage": "招标公告",
                "link": "https://example.com/norm1",
            }
        ]
        (self.daily_dir / f"{today}.json").write_text(json.dumps(normal_items), encoding="utf-8")
        
        with patch("config.STATE_DIR", self.state_dir), \
             patch("config.DAILY_DIR", self.daily_dir):
             
            summary = build_rich_summary(
                day=today,
                total_count=1,
                focus_count=0,
            )
            
            content_val = summary.get("content_text", "")
            # 验证滞后预警文字已注入富文本消息
            self.assertIn("🚨【特别预警】历史回扫捕获 1 条滞后补录/隐匿现身项目", content_val)
            self.assertIn("滞后15天", content_val)
            self.assertIn("南宁市某地下综合管廊施工隐匿补录工程", content_val)
            
            remark_val = summary.get("remark_val", "")
            self.assertIn("滞后15天", remark_val)
            self.assertIn("滞后补录 1 条", summary.get("kw3", ""))


if __name__ == "__main__":
    unittest.main()
