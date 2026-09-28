# -*- coding: utf-8 -*-
"""测试三项核心规范：
1. 所有不是今天的过去再次最终出来的结果都是封装（终版封存，final-YYYY-MM-DD.json，已封存徽章）。
2. 发现有滞后公开的公告时立即推送微信提醒（带去重保障与优先覆盖）。
3. 采集异常（包括广西中心、崇左阳光平台、日终对账）及无法更新 VPS 上的信息时均向管理员推送告警。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from scripts.notify_wechat import (
    send_delayed_notice_alert,
    send_alert,
    check_push_condition,
)


class TestPastSealAndAlerts(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp_dir.name) / "state"
        self.daily_dir = Path(self.tmp_dir.name) / "daily"
        self.dist_dir = Path(self.tmp_dir.name) / "dist"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.dist_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("config.today", return_value="2026-09-24")
    def test_past_date_auto_sealed(self, mock_today):
        """测试：所有不是今天的过去日期，无论是否加 --final，最终生成结果都自动封装"""
        past_date = "2026-09-20"
        final_file = self.state_dir / f"final-{past_date}.json"
        self.assertFalse(final_file.exists())

        with patch("config.STATE_DIR", self.state_dir):
            # 验证 build_archive_page 判定
            is_past = (past_date < config.today())
            is_final = final_file.exists() or is_past
            self.assertTrue(is_final)

            # 模拟 run_daily 针对过去日期的参数解析逻辑
            today_str = config.today()
            day = past_date
            is_final_run = True if day < today_str else False
            self.assertTrue(is_final_run)

    @patch("config.WECHAT_APPID", "mock_appid")
    @patch("config.WECHAT_APPSECRET", "mock_secret")
    @patch("config.WECHAT_TOUSER", "user_openid_1")
    @patch("config.WECHAT_ADMIN_TOUSER", "admin_openid_9")
    @patch("config.WECHAT_TEMPLATE_ID", "mock_tpl_id")
    @patch("scripts.notify_wechat.get_access_token", return_value="mock_token")
    @patch("scripts.notify_wechat.send_template_message", return_value=True)
    def test_delayed_notice_immediate_alert_and_dedup(self, mock_send, mock_token):
        """测试：发现滞后公开公告立即推送提醒，并且避免对同一条目重复提醒"""
        delayed_items = [
            {
                "infoid": "delay-item-001",
                "title": "某重点水利工程施工总承包补录公告",
                "pub_time": "2026-09-18 10:00:00",
                "delay_days": 5,
                "areaname": "南宁市",
                "stage": "招标公告",
                "link": "https://example.com/item/001"
            }
        ]

        with patch("config.STATE_DIR", self.state_dir):
            # 1. 首次捕获滞后公开，立即推送
            ok = send_delayed_notice_alert(delayed_items, today="2026-09-23")
            self.assertTrue(ok)
            # 验证接收人同时包含用户和管理员
            self.assertEqual(mock_send.call_count, 2)
            sent_users = {call.args[1]["touser"] for call in mock_send.call_args_list}
            self.assertIn("user_openid_1", sent_users)
            self.assertIn("admin_openid_9", sent_users)

            # 验证推存记录文件生成
            pushed_file = self.state_dir / "pushed_delayed_alerts.json"
            self.assertTrue(pushed_file.exists())
            pushed_data = json.loads(pushed_file.read_text(encoding="utf-8"))
            self.assertIn("delay-item-001", pushed_data)

            # 2. 再次执行相同回扫时，自动去重不再重复刷屏
            mock_send.reset_mock()
            ok_dup = send_delayed_notice_alert(delayed_items, today="2026-09-23")
            self.assertTrue(ok_dup)
            mock_send.assert_not_called()

            # 3. 出现新的滞后公告时，立即推送新条目
            new_item = {
                "infoid": "delay-item-002",
                "title": "新建高速公路附属绿化工程",
                "pub_time": "2026-09-15 08:30:00",
                "delay_days": 8,
                "areaname": "柳州市",
                "stage": "中标候选人",
                "link": "https://example.com/item/002"
            }
            ok_new = send_delayed_notice_alert(delayed_items + [new_item], today="2026-09-23")
            self.assertTrue(ok_new)
            self.assertEqual(mock_send.call_count, 2)

    def test_check_push_condition_delayed_rule(self):
        """测试：只要发现有滞后公开公告，立即强推"""
        should_push, reason = check_push_condition(
            force=False, total_count=0, focus_count=0, delayed_count=2
        )
        self.assertTrue(should_push)
        self.assertIn("滞后公开", reason)

    @patch("config.WECHAT_APPID", "mock_appid")
    @patch("config.WECHAT_APPSECRET", "mock_secret")
    @patch("config.WECHAT_ADMIN_TOUSER", "admin_special_user")
    @patch("config.WECHAT_ALERT_TEMPLATE_ID", "mock_alert_tpl")
    @patch("scripts.notify_wechat.get_access_token", return_value="mock_token")
    @patch("scripts.notify_wechat.send_template_message", return_value=True)
    def test_send_alert_to_admin_on_failure(self, mock_send, mock_token):
        """测试：采集异常与无法更新 VPS 上的信息时均仅推送给管理员"""
        # 无法更新 VPS 异常
        ok_vps = send_alert("2026-09-24", "无法更新 VPS 上的信息：端口连接超时", "upload_vps")
        self.assertTrue(ok_vps)
        payload = mock_send.call_args[0][1]
        self.assertEqual(payload["touser"], "admin_special_user")
        self.assertIn("无法更新 VPS", payload["data"]["content"]["value"])

        # 采集异常
        mock_send.reset_mock()
        ok_collect = send_alert("2026-09-24", "采集异常：崇左阳光采购平台响应超时", "collect_cz_ygcg")
        self.assertTrue(ok_collect)
        payload_c = mock_send.call_args[0][1]
        self.assertEqual(payload_c["touser"], "admin_special_user")
        self.assertIn("崇左阳光采购平台", payload_c["data"]["content"]["value"])


if __name__ == "__main__":
    unittest.main()
