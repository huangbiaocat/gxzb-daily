# -*- coding: utf-8 -*-
"""针对日常极速监控与历史回扫分工策略的单元测试。"""

import json
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

import config
from run_daily import should_run_backscan


class MockArgs:
    def __init__(self, **kwargs):
        self.skip_backscan = False
        self.backscan = False
        self.final = False
        self.yesterday_final = False
        self.__dict__.update(kwargs)


class TestBackscanSchedule(unittest.TestCase):
    def setUp(self):
        self.marker_file = config.STATE_DIR / ".backscan_schedule_runs.json"
        if self.marker_file.exists():
            self.marker_file.unlink()

    def tearDown(self):
        if self.marker_file.exists():
            self.marker_file.unlink()

    def test_normal_routine_monitoring_skips_backscan(self):
        """测试日常10分钟例行监控（非早晚时段）必须极速跳过历史回扫。"""
        mock_now = datetime(2026, 10, 21, 14, 20, 0)
        with patch("run_daily.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            args = MockArgs()
            need_bs, reason = should_run_backscan(args, "2026-10-21")
            self.assertFalse(need_bs)
            self.assertIn("日常监控极速时段", reason)

    def test_scheduled_window_triggers_once(self):
        """测试命中早晚固定时段窗口首轮触发，次轮自动跳过防重复。"""
        mock_now_1 = datetime(2026, 10, 21, 8, 32, 0)
        with patch("run_daily.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now_1
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            args = MockArgs()
            need_bs1, r1 = should_run_backscan(args, "2026-10-21")
            self.assertTrue(need_bs1)
            self.assertIn("命中早晚固定回扫时段 (08:30)", r1)

            # 10 分钟后（08:42，仍在 20 分钟窗口内），次轮必须跳过
            mock_dt.now.return_value = datetime(2026, 10, 21, 8, 42, 0)
            need_bs2, r2 = should_run_backscan(args, "2026-10-21")
            self.assertFalse(need_bs2)
            self.assertIn("今日已执行过，跳过重复回扫", r2)

            # 傍晚 18:05 命中 18:00 窗口，触发傍晚回扫
            mock_dt.now.return_value = datetime(2026, 10, 21, 18, 5, 0)
            need_bs3, r3 = should_run_backscan(args, "2026-10-21")
            self.assertTrue(need_bs3)
            self.assertIn("命中早晚固定回扫时段 (18:00)", r3)

    def test_final_archive_always_triggers_backscan(self):
        """测试凌晨终版封存归档必须自动全量回扫。"""
        mock_now = datetime(2026, 10, 21, 0, 10, 0)
        with patch("run_daily.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            args = MockArgs(final=True)
            need_bs, reason = should_run_backscan(args, "2026-10-20")
            self.assertTrue(need_bs)
            self.assertIn("终版归档封存流水线", reason)

    def test_explicit_backscan_flag_overrides_schedule(self):
        """测试命令行传 --backscan 显式强制执行。"""
        mock_now = datetime(2026, 10, 21, 15, 0, 0)
        with patch("run_daily.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            args = MockArgs(backscan=True)
            need_bs, reason = should_run_backscan(args, "2026-10-21")
            self.assertTrue(need_bs)
            self.assertIn("命令行传入 --backscan", reason)

    def test_explicit_skip_backscan_overrides_everything(self):
        """测试命令行传 --skip-backscan 显式跳过。"""
        mock_now = datetime(2026, 10, 21, 8, 30, 0)
        with patch("run_daily.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            args = MockArgs(skip_backscan=True, final=True)
            need_bs, reason = should_run_backscan(args, "2026-10-21")
            self.assertFalse(need_bs)
            self.assertIn("命令行传入 --skip-backscan", reason)


if __name__ == "__main__":
    unittest.main()
