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
