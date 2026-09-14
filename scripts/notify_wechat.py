# -*- coding: utf-8 -*-
"""微信服务号模板消息推送模块：
1. 自动获取并维护 access_token
2. 支持推送日常采集日报卡片（模版: 招标公告通知 t-hj2RwfI8oIeI9gRJdPOPgijcvJJk7IghTN3gwQpAc）
3. 支持推送异常告警卡片（模版: 任务执行失败告警）
4. 消息点击直达 VPS 对应日期的日报明细或首页，无外部三方依赖（纯标准库 urllib）
"""

import json
import logging
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# 支持相对导入与顶层导入
try:
    import config
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import config

logger = logging.getLogger(__name__)


def get_access_token(appid: str, appsecret: str) -> str:
    """通过 AppID 和 AppSecret 向微信服务器换取 access_token"""
    if not appid or not appsecret:
        return ""
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={appid}&secret={appsecret}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "access_token" in data:
                return data["access_token"]
            else:
                print(f"[微信推送] 获取 access_token 失败: {data}")
                return ""
    except Exception as exc:
        print(f"[微信推送] 网络请求 access_token 异常: {exc}")
        return ""


def send_template_message(access_token: str, payload: dict) -> bool:
    """向微信用户推送模板消息"""
    if not access_token:
        print("[微信推送] 缺少 access_token，跳过推送")
        return False
    url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("errcode") == 0:
                print(f"[微信推送] 模版消息发送成功 msgid={res.get('msgid')}")
                return True
            else:
                print(f"[微信推送] 模版消息发送失败: {res}")
                return False
    except Exception as exc:
        print(f"[微信推送] 发送请求异常: {exc}")
        return False


def send_daily_summary(day: str, total_count: int, focus_count: int, failed_steps: list = None) -> bool:
    """发送每日采集概览模版消息"""
    appid = config.WECHAT_APPID
    appsecret = config.WECHAT_APPSECRET
    touser = config.WECHAT_TOUSER
    tpl_id = config.WECHAT_TEMPLATE_ID

    if not (appid and appsecret and touser and tpl_id):
        print("[微信推送] 微信配置不完整，跳过推送（需配置 WECHAT_APPID, WECHAT_APPSECRET, WECHAT_TOUSER, WECHAT_TEMPLATE_ID）")
        return False

    token = get_access_token(appid, appsecret)
    if not token:
        return False

    base_url = getattr(config, "BASE_URL", "").rstrip("/")
    if base_url:
        page_url = f"{base_url}/{day}.html"
    else:
        page_url = f"http://127.0.0.1:8089/{day}.html"

    title_val = f"广西全区招投标公告日报（{day}）"
    content_val = f"全区共采集 {total_count} 条，重点预警标讯 {focus_count} 条。"
    if failed_steps:
        content_val += f" 注意：环节 {', '.join(failed_steps)} 执行有警报。"

    payload = {
        "touser": touser,
        "template_id": tpl_id,
        "url": page_url,
        "data": {
            "first": {"value": title_val, "color": "#1e293b"},
            "keyword1": {"value": "广西壮族自治区公共资源交易平台", "color": "#475569"},
            "keyword2": {"value": day, "color": "#2563eb"},
            "keyword3": {"value": content_val, "color": "#d97706" if focus_count > 0 else "#059669"},
            "remark": {"value": "点击本通知即可直接在手机端查看今日完整标讯明细与筛选。", "color": "#64748b"}
        }
    }
    return send_template_message(token, payload)


def send_alert(day: str, error_msg: str, step_name: str = "每日定时任务") -> bool:
    """发送异常告警模版消息"""
    appid = config.WECHAT_APPID
    appsecret = config.WECHAT_APPSECRET
    touser = config.WECHAT_TOUSER
    tpl_id = config.WECHAT_ALERT_TEMPLATE_ID or config.WECHAT_TEMPLATE_ID

    if not (appid and appsecret and touser and tpl_id):
        print("[微信推送] 微信配置不完整，跳过异常告警")
        return False

    token = get_access_token(appid, appsecret)
    if not token:
        return False

    base_url = getattr(config, "BASE_URL", "").rstrip("/")
    page_url = f"{base_url}/" if base_url else "http://127.0.0.1:8089/"

    if tpl_id == config.WECHAT_ALERT_TEMPLATE_ID and config.WECHAT_ALERT_TEMPLATE_ID:
        payload = {
            "touser": touser,
            "template_id": tpl_id,
            "url": page_url,
            "data": {
                "first": {"value": f"⚠️ 招标采集任务执行异常告警（{day}）", "color": "#dc2626"},
                "keyword1": {"value": step_name, "color": "#1e293b"},
                "keyword2": {"value": error_msg[:100], "color": "#dc2626"},
                "remark": {"value": "请登录服务器或检查运行日志，排查采集流程。", "color": "#64748b"}
            }
        }
    else:
        # 回退使用普通模板
        payload = {
            "touser": touser,
            "template_id": tpl_id,
            "url": page_url,
            "data": {
                "first": {"value": f"⚠️ 采集异常告警（{day}）", "color": "#dc2626"},
                "keyword1": {"value": "系统执行告警", "color": "#dc2626"},
                "keyword2": {"value": day, "color": "#1e293b"},
                "keyword3": {"value": f"{step_name} 失败: {error_msg}", "color": "#dc2626"},
                "remark": {"value": "请检查服务器日志以恢复自动化流程。", "color": "#64748b"}
            }
        }
    return send_template_message(token, payload)


if __name__ == "__main__":
    print("测试微信通知模版推送...")
    day = config.today()
    ok = send_daily_summary(day, total_count=182, focus_count=14)
    print("推送结果:", ok)
