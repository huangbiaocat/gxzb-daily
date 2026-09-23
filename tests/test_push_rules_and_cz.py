# -*- coding: utf-8 -*-
import unittest
import config
from scripts.collect_cz_ygcg import normalize_cz_record, get_cz_stage_and_category
from scripts.notify_wechat import check_push_condition, build_rich_summary, format_time_range, get_incremental_notices


class TestPushRulesAndCZ(unittest.TestCase):
    def setUp(self):
        self.orig_projects = getattr(config, "FOCUS_PROJECTS", [])
        self.orig_owners = getattr(config, "FOCUS_OWNERS", [])
        self.orig_keywords = getattr(config, "FOCUS_KEYWORDS", [])
        self.orig_min_amount = getattr(config, "FOCUS_MIN_AMOUNT", 0.0)
        self.orig_push_rules = getattr(config, "PUSH_TRIGGER_RULES", [])
        self.orig_min_count = getattr(config, "PUSH_MIN_COUNT", 1)

    def tearDown(self):
        config.FOCUS_PROJECTS = self.orig_projects
        config.FOCUS_OWNERS = self.orig_owners
        config.FOCUS_KEYWORDS = self.orig_keywords
        config.FOCUS_MIN_AMOUNT = self.orig_min_amount
        config.PUSH_TRIGGER_RULES = self.orig_push_rules
        config.PUSH_MIN_COUNT = self.orig_min_count

    def test_cz_stage_and_category(self):
        stage, cat = get_cz_stage_and_category(1, "某工程招标公告")
        self.assertEqual(stage, "招标公告")
        self.assertEqual(cat, "001001001002")

        stage, cat = get_cz_stage_and_category(5, "某工程成交结果公告")
        self.assertEqual(stage, "中标公告")
        self.assertEqual(cat, "001001001006")

        # 备选规则识别
        stage, cat = get_cz_stage_and_category(99, "某项目第一中标候选人公示")
        self.assertEqual(stage, "中标公示")
        self.assertEqual(cat, "001001001005")

    def test_cz_owner_and_focus_no_amount_threshold(self):
        config.FOCUS_PROJECTS = ["凭祥加工园区"]
        config.FOCUS_OWNERS = ["富宁建设投资"]
        config.FOCUS_KEYWORDS = ["光储充一体化"]
        config.FOCUS_MIN_AMOUNT = config.parse_min_amount_val("1000")  # 设置 1000 万元门槛 (10000000 元)

        # 1. 命中重点业主（无金额门槛，即使标题没有金额也必须命中重点）
        rec_owner = {
            "noticeId": "1001",
            "noticeTitle": "标准厂房装修改造工程成交公告",
            "noticeType": 5,
            "tendererName": "宁明县富宁建设投资开发有限公司",
            "noticeTime": 1726919755000,
            "purchaseProjectsIds": "p1",
        }
        norm = normalize_cz_record(rec_owner)
        self.assertIsNotNone(norm)
        self.assertEqual(norm["is_focus"], 1)
        self.assertIn("重点业主", norm["focus_tags"])
        self.assertEqual(norm["owner"], "宁明县富宁建设投资开发有限公司")

        # 2. 命中重点项目（无金额门槛，立即命中重点）
        rec_proj = {
            "noticeId": "1002",
            "noticeTitle": "凭祥加工园区东区三期装修改造工程招标公告",
            "noticeType": 1,
            "tendererName": "未知公司",
            "noticeTime": 1726919755000,
            "purchaseProjectsIds": "p2",
        }
        norm_p = normalize_cz_record(rec_proj)
        self.assertEqual(norm_p["is_focus"], 1)
        self.assertIn("重点项目", norm_p["focus_tags"])

        # 3. 命中通用预警词，但金额未达门槛 -> 不算重点
        rec_kw_low = {
            "noticeId": "1003",
            "noticeTitle": "光储充一体化充电站项目50万元施工招标公告",
            "noticeType": 1,
            "tendererName": "普通公司",
            "noticeTime": 1726919755000,
            "purchaseProjectsIds": "p3",
        }
        norm_kw_low = normalize_cz_record(rec_kw_low)
        self.assertEqual(norm_kw_low["is_focus"], 0)

        # 4. 命中通用预警词，且金额达标 (1500万 >= 1000万) -> 算重点
        rec_kw_high = {
            "noticeId": "1004",
            "noticeTitle": "光储充一体化充电站项目1500万元EPC总承包招标公告",
            "noticeType": 1,
            "tendererName": "普通公司",
            "noticeTime": 1726919755000,
            "purchaseProjectsIds": "p4",
        }
        norm_kw_high = normalize_cz_record(rec_kw_high)
        self.assertEqual(norm_kw_high["is_focus"], 1)
        self.assertIn("重点关键词", norm_kw_high["focus_tags"])

    def test_push_conditions(self):
        config.PUSH_TRIGGER_RULES = ["focus", "batch_time", "error"]
        config.PUSH_MIN_COUNT = 5

        # 重点标讯命中：focus_count > 0，不受 PUSH_MIN_COUNT 限制，立即触发
        ok, reason = check_push_condition(force=False, total_count=1, focus_count=1, max_amount=10.0)
        self.assertTrue(ok)
        self.assertIn("重点跟踪", reason)

        # 非重点标讯且未达总数门槛 (1 < 5)，不触发
        ok2, reason2 = check_push_condition(force=False, total_count=1, focus_count=0, max_amount=10.0)
        self.assertFalse(ok2)
        self.assertIn("低于设定的最低推送门槛", reason2)

    def test_focus_keywords_amount_threshold(self):
        # 仅配置“码头”且门槛 5000 万
        config.FOCUS_KEYWORDS = ["码头"]
        config.FOCUS_MIN_AMOUNT = 50000000.0
        config.FOCUS_PROJECTS = []
        config.FOCUS_OWNERS = []
        config.FOCUS_PROJECT_TYPES = []

        # 历史残留词（灌区、水库等）绝不能触发重点
        rec_old = {
            "noticeId": "old1",
            "noticeTitle": "某灌区水库治理工程施工招标公告",
            "noticeType": 1,
            "tendererName": "某建设局",
            "noticeTime": 1726919755000,
            "purchaseProjectsIds": "old1",
        }
        norm_old = normalize_cz_record(rec_old)
        self.assertIsNotNone(norm_old)
        self.assertEqual(norm_old["is_focus"], 0)
        self.assertEqual(norm_old["focus_tags"], [])

        # 码头项目但金额小于 5000 万（例如 3000 万元），不触发
        rec_low = {
            "noticeId": "mt1",
            "noticeTitle": "某港区码头3000万元配套工程招标公告",
            "noticeType": 1,
            "tendererName": "某港口集团",
            "noticeTime": 1726919755000,
            "purchaseProjectsIds": "mt1",
        }
        norm_low = normalize_cz_record(rec_low)
        self.assertIsNotNone(norm_low)
        self.assertEqual(norm_low["is_focus"], 0)

        # 码头项目且金额达到或超过 5000 万（例如 8000 万元），触发重点预警
        rec_high = {
            "noticeId": "mt2",
            "noticeTitle": "某港区码头8000万元码头作业区工程招标公告",
            "noticeType": 1,
            "tendererName": "某港口集团",
            "noticeTime": 1726919755000,
            "purchaseProjectsIds": "mt2",
        }
        norm_high = normalize_cz_record(rec_high)
        self.assertIsNotNone(norm_high)
        self.assertEqual(norm_high["is_focus"], 1)
        self.assertIn("重点关键词", norm_high["focus_tags"])

    def test_build_rich_summary(self):
        # 测试存在数据的日期
        summary = build_rich_summary("2026-09-20", total_count=127, focus_count=5)
        self.assertIn("content_text", summary)
        self.assertIn("【广西招投标公告日报 · 2026-09-20】", summary["content_text"])
        self.assertIn("共采集 127 条", summary["content_text"])
        self.assertIn("🎯 精选重点标讯推荐：", summary["content_text"])
        self.assertTrue(len(summary["kw1"]) > 0)
        self.assertTrue(len(summary["kw2"]) > 0)
        self.assertIn("重点预警 5 条", summary["kw3"])

        # 测试终版封存
        final_sum = build_rich_summary("2026-09-20", total_count=127, focus_count=5, is_final=True)
        self.assertIn("【广西招投标终版日报 · 2026-09-20】", final_sum["content_text"])

        # 测试无文件日期的兜底处理
        empty_sum = build_rich_summary("1999-01-01", total_count=10, focus_count=0)
        self.assertIn("【广西招投标公告日报 · 1999-01-01】", empty_sum["content_text"])
        self.assertIn("共采集 10 条", empty_sum["content_text"])
        self.assertIn("常规流转", empty_sum["kw3"])

    def test_format_time_range_and_incremental_summary(self):
        from datetime import datetime
        # 测试跨天时间格式化（昨晚 17:30 ~ 今晨 08:00）
        t_desc = format_time_range("2026-09-14 17:30:00", datetime(2026, 9, 15, 8, 0, 0))
        self.assertEqual(t_desc, "昨晚 17:30 ~ 今晨 08:00")

        # 测试同天时间格式化（今日 08:00 ~ 17:30）
        t_desc_today = format_time_range("2026-09-15 08:00:00", datetime(2026, 9, 15, 17, 30, 0))
        self.assertEqual(t_desc_today, "今日 08:00 ~ 17:30")

        # 测试增量富文本摘要组织
        inc_items = [
            {
                "infoid": "test-inc-001",
                "title": "昨晚更新的新增项目招标公告",
                "stage": "招标公告",
                "region": "南宁市",
                "pub_time": "2026-09-14 20:00:00",
                "is_focus": 1,
                "amount": "1500万元"
            }
        ]
        summary = build_rich_summary(
            "2026-09-15",
            total_count=1,
            focus_count=1,
            incremental_items=inc_items,
            since_time_str="2026-09-14 17:30:00",
            until_time_str="2026-09-15 08:00:00"
        )
        self.assertIn("【广西招投标标讯增量提醒】", summary["content_text"])
        self.assertIn("昨晚 17:30 ~ 今晨 08:00", summary["content_text"])
        self.assertIn("共 1 条标讯", summary["content_text"])
        self.assertIn("昨晚更新的新增项目招标公告", summary["content_text"])
        self.assertIn("新增 1 条", summary["title_val"])

    def test_check_push_condition_conditional_incremental(self):
        from unittest.mock import patch
        from datetime import datetime, time
        import json

        config.PUSH_TRIGGER_RULES = ["batch_time"]
        config.PUSH_BATCH_HOURS = "08:00, 17:30"
        config.PUSH_CONDITIONAL_INCREMENTAL = True
        config.PUSH_MIN_COUNT = 0

        # 模拟当前处于早 08:05（在 08:00 批次窗口内）
        fake_now = datetime(2026, 9, 15, 8, 5, 0)
        fake_state = {
            "last_regular_push_time": "2026-09-14 17:30:00"
        }

        # Case 1: 自昨晚 17:30 以来有新增 1 条标讯 -> 触发推送
        with patch("scripts.notify_wechat.datetime") as mock_dt, \
             patch("scripts.notify_wechat.get_incremental_notices") as mock_inc, \
             patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(fake_state))):
            mock_dt.now.return_value = fake_now
            mock_dt.strptime = datetime.strptime
            mock_inc.return_value = [{"infoid": "x1", "title": "新增标讯"}]

            ok, reason = check_push_condition(force=False, total_count=10, focus_count=0)
            self.assertTrue(ok)
            self.assertIn("命中定时条件推送规则", reason)
            self.assertIn("新增 1 条标讯", reason)

        # Case 2: 自昨晚 17:30 以来无新增标讯 (0 条) -> 静默跳过，不发空消息
        with patch("scripts.notify_wechat.datetime") as mock_dt, \
             patch("scripts.notify_wechat.get_incremental_notices") as mock_inc, \
             patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(fake_state))):
            mock_dt.now.return_value = fake_now
            mock_dt.strptime = datetime.strptime
            mock_inc.return_value = []

            ok, reason = check_push_condition(force=False, total_count=10, focus_count=0)
            self.assertFalse(ok)
            self.assertIn("未触发条件推送", reason)
            self.assertIn("无新增标讯", reason)


if __name__ == "__main__":
    unittest.main()
