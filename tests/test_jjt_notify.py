# -*- coding: utf-8 -*-
"""交建通 (企业微信私有化) 自建应用与群机器人推送模块单元测试."""

import io
import json
import os
import unittest
from unittest.mock import patch

from scripts import jjt_notify


class TestJjtNotify(unittest.TestCase):

    def setUp(self):
        self.old_env = os.environ.copy()
        if hasattr(jjt_notify, "_token_cache"):
            jjt_notify._token_cache.clear()
        if jjt_notify.TOKEN_CACHE_PATH.exists():
            try:
                jjt_notify.TOKEN_CACHE_PATH.unlink()
            except Exception:
                pass

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        if hasattr(jjt_notify, "_token_cache"):
            jjt_notify._token_cache.clear()
        if jjt_notify.TOKEN_CACHE_PATH.exists():
            try:
                jjt_notify.TOKEN_CACHE_PATH.unlink()
            except Exception:
                pass

    def test_load_config_fallback(self):
        """测试从环境变量加载交建通配置的回退机制"""
        os.environ["JJT_CORPID"] = "wx_test_corp_123"
        os.environ["JJT_CORPSECRET"] = "sec_test_abc"
        os.environ["JJT_APP_CODE"] = "D3B5E124591D"
        os.environ["JJT_AGENTID"] = "1003407"
        os.environ["JJT_TOUSER"] = "user_abc"
        os.environ["JJT_TOPARTY"] = "77249|100881"
        os.environ["JJT_DEFAULT_CHATID"] = "chat_test_999"
        os.environ["JJT_WEBHOOK_URL"] = "https://jjt.ccccltd.cn/cgi-bin/webhook/send?key=test_key"

        with patch("scripts.jjt_notify.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            cfg = jjt_notify.load_config()

            self.assertEqual(cfg.get("corpid"), "wx_test_corp_123")
            self.assertEqual(cfg.get("corpsecret"), "sec_test_abc")
            self.assertEqual(cfg.get("app_code"), "D3B5E124591D")
            self.assertEqual(cfg.get("agentid"), 1003407)
            self.assertEqual(cfg.get("touser"), "user_abc")
            self.assertEqual(cfg.get("toparty"), "77249|100881")
            self.assertEqual(cfg.get("default_chatid"), "chat_test_999")
            self.assertEqual(cfg.get("webhook_url"), "https://jjt.ccccltd.cn/cgi-bin/webhook/send?key=test_key")
            self.assertEqual(cfg.get("api_base_url"), "https://jjt.ccccltd.cn/cgi-bin")

    @patch("urllib.request.urlopen")
    def test_get_access_token_and_cache(self, mock_urlopen):
        """测试 Access Token 获取与本地内存缓存"""
        fake_response = io.BytesIO(json.dumps({
            "errcode": 0,
            "errmsg": "ok",
            "access_token": "ACCESS_TOKEN_XYZ",
            "expires_in": 7200
        }).encode("utf-8"))
        mock_urlopen.return_value = fake_response

        with patch("scripts.jjt_notify.load_config") as mock_cfg:
            mock_cfg.return_value = {
                "corpid": "corp_123",
                "corpsecret": "sec_456",
                "api_base_url": "https://jjt.ccccltd.cn/cgi-bin"
            }

            token1 = jjt_notify.get_access_token()
            self.assertEqual(token1, "ACCESS_TOKEN_XYZ")
            self.assertEqual(mock_urlopen.call_count, 1)

            token2 = jjt_notify.get_access_token()
            self.assertEqual(token2, "ACCESS_TOKEN_XYZ")
            self.assertEqual(mock_urlopen.call_count, 1)

    @patch("urllib.request.urlopen")
    @patch("scripts.jjt_notify.get_access_token")
    def test_send_appchat_message(self, mock_get_token, mock_urlopen):
        """测试应用向应用群聊发送卡片消息"""
        mock_get_token.return_value = "MOCK_TOKEN"
        fake_resp = io.BytesIO(json.dumps({"errcode": 0, "errmsg": "ok"}).encode("utf-8"))
        mock_urlopen.return_value = fake_resp

        payload = {
            "title": "测试标题",
            "description": "测试卡片描述",
            "url": "https://ztb.139771.xyz",
            "btntxt": "进入详情"
        }
        ok = jjt_notify.send_appchat_message(
            chatid="chat_test_1",
            msgtype="textcard",
            content_payload=payload
        )
        self.assertTrue(ok)
        self.assertTrue(mock_urlopen.called)
        req = mock_urlopen.call_args[0][0]
        self.assertIn("/appchat/send?access_token=MOCK_TOKEN", req.full_url)
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["chatid"], "chat_test_1")
        self.assertEqual(body["msgtype"], "textcard")

    @patch("urllib.request.urlopen")
    def test_send_webhook_message(self, mock_urlopen):
        """测试向群机器人 Webhook 发送 Markdown 消息"""
        fake_resp = io.BytesIO(json.dumps({"errcode": 0, "errmsg": "ok"}).encode("utf-8"))
        mock_urlopen.return_value = fake_resp

        with patch("scripts.jjt_notify.load_config") as mock_cfg:
            mock_cfg.return_value = {
                "api_base_url": "https://jjt.ccccltd.cn/cgi-bin"
            }
            ok = jjt_notify.send_webhook_message(
                webhook_url_or_key="my_robot_key_888",
                msgtype="markdown",
                content_payload={"content": "### 招标简报测试"}
            )
            self.assertTrue(ok)
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.full_url, "https://jjt.ccccltd.cn/cgi-bin/webhook/send?key=my_robot_key_888")
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["msgtype"], "markdown")
            self.assertEqual(body["markdown"]["content"], "### 招标简报测试")

    @patch("scripts.jjt_notify.send_daily_report_card")
    @patch("scripts.jjt_notify.send_webhook_message")
    @patch("scripts.jjt_notify.get_daily_metrics")
    def test_send_daily_summary(self, mock_metrics, mock_webhook, mock_appchat):
        """测试每日招投标多通道综合推送（应用群卡片 + 机器人 Webhook）"""
        mock_metrics.return_value = {"total_count": 28, "total_zb": 18, "total_win": 10}
        mock_appchat.return_value = True
        mock_webhook.return_value = True

        with patch("scripts.jjt_notify.load_config") as mock_cfg:
            mock_cfg.return_value = {
                "corpid": "corp_test",
                "corpsecret": "sec_test",
                "default_chatid": "chat_001",
                "webhook_url": "https://jjt.ccccltd.cn/cgi-bin/webhook/send?key=robot_001",
                "site_base_url": "https://ztb.139771.xyz"
            }

            res = jjt_notify.send_daily_summary("2026-10-16")
            self.assertEqual(res["appchat"], True)
            self.assertEqual(res["webhook"], True)
            self.assertIsNone(res["app_message"])
            self.assertTrue(mock_appchat.called)
            self.assertTrue(mock_webhook.called)

    @patch("urllib.request.urlopen")
    @patch("scripts.jjt_notify.get_access_token")
    def test_get_agent_info(self, mock_get_token, mock_urlopen):
        """测试获取自建应用详情"""
        mock_get_token.return_value = "MOCK_TOKEN"
        fake_resp = io.BytesIO(json.dumps({
            "errcode": 0,
            "errmsg": "ok",
            "agentid": 1003407,
            "name": "招投标情报监控",
            "redirect_domain": "ztb.139771.xyz",
            "home_url": "https://ztb.139771.xyz/"
        }).encode("utf-8"))
        mock_urlopen.return_value = fake_resp

        with patch("scripts.jjt_notify.load_config") as mock_cfg:
            mock_cfg.return_value = {
                "agentid": 1003407,
                "api_base_url": "https://jjt.ccccltd.cn/cgi-bin"
            }
            info = jjt_notify.get_agent_info(1003407)
            self.assertEqual(info.get("errcode"), 0)
            self.assertEqual(info.get("name"), "招投标情报监控")
            self.assertEqual(info.get("agentid"), 1003407)
            req = mock_urlopen.call_args[0][0]
            self.assertIn("/agent/get?access_token=MOCK_TOKEN&agentid=1003407", req.full_url)

    @patch("urllib.request.urlopen")
    @patch("scripts.jjt_notify.get_access_token")
    def test_send_app_message(self, mock_get_token, mock_urlopen):
        """测试以自建应用身份向指定人员/部门推送卡片消息"""
        mock_get_token.return_value = "MOCK_TOKEN"
        fake_resp = io.BytesIO(json.dumps({"errcode": 0, "errmsg": "ok"}).encode("utf-8"))
        mock_urlopen.return_value = fake_resp

        with patch("scripts.jjt_notify.load_config") as mock_cfg:
            mock_cfg.return_value = {
                "agentid": 1003407,
                "touser": "huangbiao",
                "toparty": "77249",
                "api_base_url": "https://jjt.ccccltd.cn/cgi-bin"
            }
            payload = {
                "title": "测试工作台通知",
                "description": "测试内容",
                "url": "https://ztb.139771.xyz",
                "btntxt": "进入查看"
            }
            ok = jjt_notify.send_app_message(
                msgtype="textcard",
                content_payload=payload
            )
            self.assertTrue(ok)
            req = mock_urlopen.call_args[0][0]
            self.assertIn("/message/send?access_token=MOCK_TOKEN", req.full_url)
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["agentid"], 1003407)
            self.assertEqual(body["touser"], "huangbiao")
            self.assertEqual(body["toparty"], "77249")
            self.assertEqual(body["msgtype"], "textcard")

    @patch("scripts.jjt_notify.send_app_message")
    @patch("scripts.jjt_notify.get_daily_metrics")
    def test_send_daily_summary_with_app_message(self, mock_metrics, mock_app_msg):
        """测试配置了 agentid 和 toparty 时的自建应用工作台消息推送"""
        mock_metrics.return_value = {"total_count": 10, "total_zb": 6, "total_win": 4}
        mock_app_msg.return_value = True

        with patch("scripts.jjt_notify.load_config") as mock_cfg:
            mock_cfg.return_value = {
                "corpid": "corp_test",
                "corpsecret": "sec_test",
                "agentid": 1003407,
                "toparty": "77249",
                "site_base_url": "https://ztb.139771.xyz"
            }
            res = jjt_notify.send_daily_summary("2026-10-16")
            self.assertIsNone(res["appchat"])
            self.assertIsNone(res["webhook"])
            self.assertEqual(res["app_message"], True)
            self.assertTrue(mock_app_msg.called)


if __name__ == "__main__":
    unittest.main()
