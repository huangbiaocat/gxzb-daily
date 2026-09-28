# -*- coding: utf-8 -*-
import unittest
from scripts.overtime_helper import is_overtime_publication, annotate_item_overtime


class TestOvertimeHelper(unittest.TestCase):
    def test_weekend_overtime(self):
        # 2026-09-20 为周日
        is_ot, reason = is_overtime_publication("2026-09-20 10:15:00")
        self.assertTrue(is_ot)
        self.assertIn("周末加班发布（周日 10:15）", reason)

        # 2026-09-19 为周六
        is_ot, reason = is_overtime_publication("2026-09-19 14:00:00")
        self.assertTrue(is_ot)
        self.assertIn("周末加班发布（周六 14:00）", reason)

    def test_workday_normal_hours(self):
        # 2026-09-22 为周二
        is_ot, reason = is_overtime_publication("2026-09-22 09:30:00")
        self.assertFalse(is_ot)
        self.assertEqual(reason, "")

        is_ot, reason = is_overtime_publication("2026-09-22 17:59:59")
        self.assertFalse(is_ot)
        self.assertEqual(reason, "")

    def test_workday_after_hours(self):
        # 2026-09-22 为周二，下班后（>= 18:00:00）
        is_ot, reason = is_overtime_publication("2026-09-22 18:00:00")
        self.assertTrue(is_ot)
        self.assertIn("工作日下班后加班发布（18:00）", reason)

        is_ot, reason = is_overtime_publication("2026-09-22 22:56:00")
        self.assertTrue(is_ot)
        self.assertIn("工作日下班后加班发布（22:56）", reason)

    def test_workday_before_hours(self):
        # 2026-09-22 为周二，上班前（< 08:00:00）
        is_ot, reason = is_overtime_publication("2026-09-22 00:03:21")
        self.assertTrue(is_ot)
        self.assertIn("工作日早间/凌晨加班发布（00:03）", reason)

        is_ot, reason = is_overtime_publication("2026-09-22 07:59:59")
        self.assertTrue(is_ot)
        self.assertIn("工作日早间/凌晨加班发布（07:59）", reason)

    def test_holiday_overtime(self):
        # 2026-10-01 为国庆节
        is_ot, reason = is_overtime_publication("2026-10-01 10:00:00")
        self.assertTrue(is_ot)
        self.assertIn("法定节假日加班发布（国庆节 10:00）", reason)

        # 2026-09-25 为中秋节
        is_ot, reason = is_overtime_publication("2026-09-25 15:30:00")
        self.assertTrue(is_ot)
        self.assertIn("法定节假日加班发布（中秋节 15:30）", reason)

        # 广西专属法定节假日：壮族三月三（2025-03-31, 2026-04-20）
        is_ot, reason = is_overtime_publication("2025-03-31 09:30:00")
        self.assertTrue(is_ot)
        self.assertIn("法定节假日加班发布（壮族三月三 09:30）", reason)

        is_ot, reason = is_overtime_publication("2026-04-20 14:00:00")
        self.assertTrue(is_ot)
        self.assertIn("法定节假日加班发布（壮族三月三 14:00）", reason)

    def test_workday_lunch_break_not_overtime(self):
        # 中午时段统一不计为加班发布
        # 夏季（9月）：12:15、14:30 发布均不算加班
        is_ot, reason = is_overtime_publication("2026-09-22 12:15:00")
        self.assertFalse(is_ot)
        self.assertEqual(reason, "")

        is_ot, reason = is_overtime_publication("2026-09-22 14:30:00")
        self.assertFalse(is_ot)
        self.assertEqual(reason, "")

        # 冬季（12月）：12:30、14:15 发布亦不算加班
        is_ot, reason = is_overtime_publication("2026-12-15 12:30:00")
        self.assertFalse(is_ot)
        self.assertEqual(reason, "")

        is_ot, reason = is_overtime_publication("2026-12-15 14:30:00")
        self.assertFalse(is_ot)
        self.assertEqual(reason, "")

    def test_winter_after_hours(self):
        # 冬季（11月至次年4月）下午 17:30 下班
        # 2026-12-15 为周二，17:35 已下班
        is_ot, reason = is_overtime_publication("2026-12-15 17:35:00")
        self.assertTrue(is_ot)
        self.assertIn("工作日下班后加班发布（17:35）", reason)

        # 17:20 仍在工作时间内
        is_ot, reason = is_overtime_publication("2026-12-15 17:20:00")
        self.assertFalse(is_ot)
        self.assertEqual(reason, "")

    def test_source_platform_independent(self):
        # 验证不是因为来自阳光采购就是加班发布：
        # 阳光采购在正常工作时间发布不是加班
        cz_work_item = {
            "source": "崇左阳光采购",
            "title": "某阳光采购正常招标",
            "pub_time": "2026-09-22 10:30:00",
        }
        annotate_item_overtime(cz_work_item)
        self.assertEqual(cz_work_item["is_overtime"], 0)
        self.assertEqual(cz_work_item["overtime_reason"], "")

        # 阳光采购在下班时间发布才算加班
        cz_after_item = {
            "source": "崇左阳光采购",
            "title": "某阳光采购晚间招标",
            "pub_time": "2026-09-22 18:30:00",
        }
        annotate_item_overtime(cz_after_item)
        self.assertEqual(cz_after_item["is_overtime"], 1)
        self.assertIn("下班后加班发布", cz_after_item["overtime_reason"])

        # 广西公共资源交易平台同理
        gx_work_item = {
            "source": "广西公共资源交易平台",
            "title": "某公资正常招标",
            "pub_time": "2026-09-22 10:30:00",
        }
        annotate_item_overtime(gx_work_item)
        self.assertEqual(gx_work_item["is_overtime"], 0)

        gx_after_item = {
            "source": "广西公共资源交易平台",
            "title": "某公资晚间招标",
            "pub_time": "2026-09-22 19:30:00",
        }
        annotate_item_overtime(gx_after_item)
        self.assertEqual(gx_after_item["is_overtime"], 1)

    def test_compensatory_workday(self):
        # 2026-01-04 为调休补班日（周日）
        # 白天上班时间发布不视为加班
        is_ot, reason = is_overtime_publication("2026-01-04 10:00:00")
        self.assertFalse(is_ot)
        self.assertEqual(reason, "")

        # 调休补班日下班后依然视为加班
        is_ot, reason = is_overtime_publication("2026-01-04 19:30:00")
        self.assertTrue(is_ot)
        self.assertIn("工作日下班后加班发布（19:30）", reason)

    def test_annotate_item(self):
        item = {"title": "某测试项目", "pub_time": "2026-09-22 22:56:00"}
        annotate_item_overtime(item)
        self.assertEqual(item["is_overtime"], 1)
        self.assertIn("22:56", item["overtime_reason"])

        item_normal = {"title": "正常项目", "pub_time": "2026-09-22 10:00:00"}
        annotate_item_overtime(item_normal)
        self.assertEqual(item_normal["is_overtime"], 0)
        self.assertEqual(item_normal["overtime_reason"], "")

    def test_edge_cases(self):
        self.assertEqual(is_overtime_publication(""), (False, ""))
        self.assertEqual(is_overtime_publication(None), (False, ""))
        self.assertEqual(is_overtime_publication("invalid_date"), (False, ""))


if __name__ == "__main__":
    unittest.main()
