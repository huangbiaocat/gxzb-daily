# -*- coding: utf-8 -*-
"""交建通 (JJT / 企业微信定制版) 自建应用通知与群聊推送模块

功能特点：
1. 采用交建通专用接口网关 (https://jjt.ccccltd.cn/cgi-bin/)
2. 自动获取并维护 access_token (本地缓存 + 过期重签)
3. 支持应用创建内部群聊 (appchat/create) 并立即下发初始化激活消息
4. 支持推送精美文本卡片 (textcard)、Markdown 以及纯文本消息
5. 针对每日招投标情报自动提取重点摘要、标讯统计，一键点击直达当日日报详情
6. 零三方依赖 (纯标准库 urllib)，配置严格隔离于本地未入库的 config_jjt.json
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config_jjt.json"
DATA_DIR = ROOT_DIR / "data"
TOKEN_CACHE_PATH = DATA_DIR / "jjt_token_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("jjt_notify")

DEFAULT_API_BASE = "https://jjt.ccccltd.cn/cgi-bin"


def load_config() -> dict:
    """读取交建通配置（优先 config_jjt.json，回退环境变量与 .env）"""
    cfg = {}
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
    except Exception as e:
        logger.error(f"读取交建通配置失败: {e}")

    # 环境变量与默认值回退
    if not cfg.get("corpid") and os.environ.get("JJT_CORPID"):
        cfg["corpid"] = os.environ.get("JJT_CORPID")
    if not cfg.get("corpsecret") and os.environ.get("JJT_CORPSECRET"):
        cfg["corpsecret"] = os.environ.get("JJT_CORPSECRET")
    if not cfg.get("app_code") and os.environ.get("JJT_APP_CODE"):
        cfg["app_code"] = os.environ.get("JJT_APP_CODE")
    if not cfg.get("agentid") and os.environ.get("JJT_AGENTID"):
        try:
            cfg["agentid"] = int(os.environ.get("JJT_AGENTID"))
        except (ValueError, TypeError):
            pass
    if not cfg.get("touser") and os.environ.get("JJT_TOUSER"):
        cfg["touser"] = os.environ.get("JJT_TOUSER")
    if not cfg.get("toparty") and os.environ.get("JJT_TOPARTY"):
        cfg["toparty"] = os.environ.get("JJT_TOPARTY")
    if not cfg.get("api_base_url"):
        cfg["api_base_url"] = os.environ.get("JJT_API_BASE_URL", DEFAULT_API_BASE)
    if not cfg.get("default_chatid") and os.environ.get("JJT_DEFAULT_CHATID"):
        cfg["default_chatid"] = os.environ.get("JJT_DEFAULT_CHATID")
    if not cfg.get("webhook_url") and os.environ.get("JJT_WEBHOOK_URL"):
        cfg["webhook_url"] = os.environ.get("JJT_WEBHOOK_URL")
    if not cfg.get("site_base_url") and os.environ.get("JJT_SITE_BASE_URL"):
        cfg["site_base_url"] = os.environ.get("JJT_SITE_BASE_URL")

    return cfg


def save_config(cfg: dict) -> bool:
    """回写配置（例如保存创建的 chatid）"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"保存交建通配置失败: {e}")
        return False


def get_access_token(force_refresh: bool = False) -> str:
    """获取交建通 API access_token，支持本地文件缓存"""
    cfg = load_config()
    corpid = cfg.get("corpid")
    corpsecret = cfg.get("corpsecret")
    api_base = cfg.get("api_base_url", DEFAULT_API_BASE).rstrip("/")

    if not corpid or not corpsecret:
        logger.error("缺少 corpid 或 corpsecret，无法获取交建通 access_token")
        return ""

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 尝试从缓存读取
    if not force_refresh and TOKEN_CACHE_PATH.exists():
        try:
            with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
                token = cached.get("access_token")
                expires_at = cached.get("expires_at", 0)
                # 提前 5 分钟刷新
                if token and time.time() < (expires_at - 300):
                    return token
        except Exception:
            pass

    # 2. 调用交建通接口获取新 Token
    url = f"{api_base}/gettoken?corpid={urllib.parse.quote(str(corpid))}&corpsecret={urllib.parse.quote(str(corpsecret))}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZTB-JJT-Client/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("errcode") == 0 and data.get("access_token"):
            token = data["access_token"]
            expires_in = data.get("expires_in", 7200)
            try:
                with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "access_token": token,
                        "expires_at": time.time() + expires_in,
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }, f, indent=2)
            except Exception as e:
                logger.warning(f"写入 Token 缓存失败: {e}")
            logger.info("成功获取并缓存交建通 access_token")
            return token
        else:
            logger.error(f"获取交建通 access_token 失败: errcode={data.get("errcode")}, errmsg={data.get("errmsg")}")
            return ""
    except Exception as e:
        logger.error(f"请求交建通 token 异常: {e}")
        return ""


def send_appchat_message(chatid: str, msgtype: str, content_payload: dict, safe: int = 0) -> bool:
    """以应用身份向群聊推送消息 (appchat/send)
    支持 msgtype: textcard, markdown, text
    """
    token = get_access_token()
    if not token:
        logger.error("发送群消息失败: 无法获取有效 access_token")
        return False

    cfg = load_config()
    api_base = cfg.get("api_base_url", DEFAULT_API_BASE).rstrip("/")
    url = f"{api_base}/appchat/send?access_token={token}"

    payload = {
        "chatid": chatid,
        "msgtype": msgtype,
        msgtype: content_payload,
        "safe": safe
    }

    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("errcode") == 0:
            logger.info(f"群消息推送成功 [chatid={chatid}, msgtype={msgtype}]")
            return True
        else:
            logger.error(f"群消息推送失败 [chatid={chatid}]: {data}")
            return False
    except Exception as e:
        logger.error(f"推送群消息异常: {e}")
        return False


def send_webhook_message(webhook_url_or_key: str, msgtype: str, content_payload: dict) -> bool:
    """向交建通/企业微信群机器人发送 Webhook 消息
    支持完整 URL 或纯 key 字符串
    """
    cfg = load_config()
    api_base = cfg.get("api_base_url", DEFAULT_API_BASE).rstrip("/")
    target_url = (webhook_url_or_key or "").strip()

    if not target_url:
        target_url = cfg.get("webhook_url", "").strip()

    if not target_url:
        logger.error("未指定 webhook_url_or_key 且配置中无 webhook_url")
        return False

    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        target_url = f"{api_base}/webhook/send?key={target_url}"

    payload = {
        "msgtype": msgtype,
        msgtype: content_payload
    }

    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            target_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("errcode") == 0:
            logger.info("群机器人 Webhook 消息推送成功")
            return True
        else:
            logger.error(f"群机器人 Webhook 消息推送失败: {data}")
            return False
    except Exception as e:
        logger.error(f"群机器人 Webhook 推送异常: {e}")
        return False


def get_agent_info(agentid: int = None) -> dict:
    """获取交建通自建应用详情 (agent/get)"""
    token = get_access_token()
    if not token:
        logger.error("获取应用详情失败: 无法获取有效 access_token")
        return {}

    cfg = load_config()
    target_agentid = agentid or cfg.get("agentid")
    if not target_agentid:
        logger.error("获取应用详情失败: 未配置 agentid")
        return {}

    api_base = cfg.get("api_base_url", DEFAULT_API_BASE).rstrip("/")
    url = f"{api_base}/agent/get?access_token={token}&agentid={target_agentid}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZTB-JJT-Client/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("errcode") == 0:
            logger.info(f"成功获取交建通自建应用详情: {data.get('name')} (agentid={data.get('agentid')})")
            return data
        else:
            logger.error(f"获取自建应用详情失败: {data}")
            return data
    except Exception as e:
        logger.error(f"请求应用详情异常: {e}")
        return {"errcode": -1, "errmsg": str(e)}


def send_app_message(
    msgtype: str,
    content_payload: dict,
    touser: str = None,
    toparty: str = None,
    totag: str = None,
    agentid: int = None,
    safe: int = 0
) -> bool:
    """以自建应用身份向成员/部门推送工作台消息 (message/send)
    支持 touser (多账号以 | 分隔), toparty (多部门以 | 分隔), totag
    支持 msgtype: textcard, markdown, text
    """
    token = get_access_token()
    if not token:
        logger.error("发送应用工作消息失败: 无法获取有效 access_token")
        return False

    cfg = load_config()
    target_agentid = agentid or cfg.get("agentid")
    if not target_agentid:
        logger.error("发送应用工作消息失败: 未配置 agentid")
        return False

    target_touser = touser if touser is not None else cfg.get("touser", "")
    target_toparty = toparty if toparty is not None else cfg.get("toparty", "")
    target_totag = totag if totag is not None else cfg.get("totag", "")

    if not target_touser and not target_toparty and not target_totag:
        logger.error("发送应用工作消息失败: 必须指定 touser、toparty 或 totag 至少一项")
        return False

    api_base = cfg.get("api_base_url", DEFAULT_API_BASE).rstrip("/")
    url = f"{api_base}/message/send?access_token={token}"

    payload = {
        "msgtype": msgtype,
        "agentid": int(target_agentid),
        msgtype: content_payload,
        "safe": safe
    }
    if target_touser:
        payload["touser"] = str(target_touser)
    if target_toparty:
        payload["toparty"] = str(target_toparty)
    if target_totag:
        payload["totag"] = str(target_totag)

    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("errcode") == 0:
            logger.info(f"应用工作消息推送成功 [agentid={target_agentid}, touser={target_touser}, toparty={target_toparty}]")
            return True
        else:
            logger.error(f"应用工作消息推送失败: {data}")
            return False
    except Exception as e:
        logger.error(f"推送应用工作消息异常: {e}")
        return False


def create_appchat(name: str, owner: str, userlist: list, chatid: str = None) -> str:
    """创建应用群聊 (appchat/create)
    文档要求：
    1. 只能操作在当前应用可见范围的成员；
    2. 只有当前应用创建的群聊，当前应用可以操作；
    3. 刚创建的群，如果没有下发消息，在客户端不会出现该群。
    """
    token = get_access_token()
    if not token:
        logger.error("创建群聊失败: 无法获取有效 access_token")
        return ""

    cfg = load_config()
    api_base = cfg.get("api_base_url", DEFAULT_API_BASE).rstrip("/")
    url = f"{api_base}/appchat/create?access_token={token}"

    payload = {
        "name": name,
        "owner": owner,
        "userlist": userlist
    }
    if chatid:
        payload["chatid"] = chatid

    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("errcode") == 0 and data.get("chatid"):
            new_chatid = data["chatid"]
            logger.info(f"应用群聊创建成功! 群名称: {name}, 群ID: {new_chatid}")

            # 按照交建通文档说明：必须立即下发一条初始化消息，客户端才会显示该群！
            welcome_msg = (
                f"🎉 **【{name}】已成功创建**\n\n"
                "本群由「招投标信息监控应用」自动创建与维护。\n"
                "系统将每天定时推送最新的广西招投标情报、重点标讯与统计日报。"
            )
            send_appchat_message(
                chatid=new_chatid,
                msgtype="markdown",
                content_payload={"content": welcome_msg}
            )

            # 自动将该 chatid 记录到本地配置中
            cfg["default_chatid"] = new_chatid
            save_config(cfg)
            return new_chatid
        else:
            logger.error(f"创建应用群聊失败: {data}")
            return ""
    except Exception as e:
        logger.error(f"创建应用群聊网络异常: {e}")
        return ""


def get_daily_metrics(day_str: str) -> dict:
    """提取指定日期的招投标数据概览指标"""
    metrics = {
        "day": day_str,
        "total_zb": 0,
        "total_win": 0,
        "total_count": 0,
        "focus_items": []
    }
    summary_file = DATA_DIR / f"summary_{day_str}.json"
    if summary_file.exists():
        try:
            with open(summary_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                metrics["total_count"] = data.get("total_count", 0)
                metrics["total_zb"] = data.get("zb_count", 0)
                metrics["total_win"] = data.get("win_count", 0)
                metrics["focus_items"] = data.get("focus_items", [])
                return metrics
        except Exception:
            pass

    try:
        from scripts.scan_record_db import get_day_stats
        stats = get_day_stats(day_str)
        if stats:
            metrics["total_count"] = stats.get("total", 0)
            metrics["total_zb"] = stats.get("zb", 0)
            metrics["total_win"] = stats.get("win", 0)
    except Exception:
        pass

    return metrics


def send_daily_report_card(day_str: str = None, chatid: str = None) -> bool:
    """组装并推送每日招投标日报卡片 (textcard)"""
    if not day_str:
        day_str = datetime.now().strftime("%Y-%m-%d")

    cfg = load_config()
    target_chatid = chatid or cfg.get("default_chatid")
    if not target_chatid:
        logger.error("未指定 chatid 且配置中无 default_chatid，无法推送日报")
        return False

    site_base = cfg.get("site_base_url", "https://ztb.139771.xyz").rstrip("/")
    detail_url = f"{site_base}/{day_str}.html"

    metrics = get_daily_metrics(day_str)
    total_count = metrics["total_count"]
    zb_count = metrics["total_zb"]
    win_count = metrics["total_win"]

    now_time = datetime.now().strftime("%H:%M")

    description = (
        f"<div class=\"gray\">期号：{day_str} ({now_time} 更新)</div>"
        f"<div class=\"normal\">本日共监测标讯 <b>{total_count}</b> 条</div>"
        f"<div class=\"normal\">• 招标公告：<b>{zb_count}</b> 条</div>"
        f"<div class=\"normal\">• 中标候选与结果：<b>{win_count}</b> 条</div>"
        f"<div class=\"highlight\">点击卡片可直接在交建通内查看当日完整日报与项目明细</div>"
    )

    card_payload = {
        "title": f"📢 广西招投标每日情报 ({day_str})",
        "description": description,
        "url": detail_url,
        "btntxt": "进入系统查看详情"
    }

    return send_appchat_message(
        chatid=target_chatid,
        msgtype="textcard",
        content_payload=card_payload
    )


def send_daily_summary(day_str: str = None) -> dict:
    """向所有配置的交建通渠道（应用群聊 + 群机器人）发送每日招投标简报
    返回各渠道推送结果 dict: {"appchat": bool|None, "webhook": bool|None, "app_message": bool|None}
    """
    if not day_str:
        day_str = datetime.now().strftime("%Y-%m-%d")

    cfg = load_config()
    results = {"appchat": None, "webhook": None, "app_message": None}

    # 1. 尝试自建应用群聊卡片推送
    chatid = cfg.get("default_chatid")
    if chatid and cfg.get("corpid") and cfg.get("corpsecret"):
        try:
            results["appchat"] = send_daily_report_card(day_str=day_str, chatid=chatid)
        except Exception as e:
            logger.error(f"应用群聊推送异常: {e}")
            results["appchat"] = False

    # 2. 尝试群机器人 Webhook 推送
    webhook_url = cfg.get("webhook_url")
    if webhook_url:
        try:
            site_base = cfg.get("site_base_url", "https://ztb.139771.xyz").rstrip("/")
            detail_url = f"{site_base}/{day_str}.html"
            metrics = get_daily_metrics(day_str)
            now_time = datetime.now().strftime("%H:%M")

            md_content = (
                f"### 📢 广西招投标每日情报 ({day_str})\n"
                f"> 发布时间: <font color=\"comment\">{day_str} {now_time}</font>\n\n"
                f"**本日监测概览**：\n"
                f"- 监测标讯总量: **<font color=\"info\">{metrics['total_count']}</font>** 条\n"
                f"- 招标与资格预审公告: **{metrics['total_zb']}** 条\n"
                f"- 中标候选人与结果公告: **{metrics['total_win']}** 条\n\n"
                f"👉 [点击此处直接进入系统查看当日完整日报与项目明细]({detail_url})"
            )
            results["webhook"] = send_webhook_message(
                webhook_url_or_key=webhook_url,
                msgtype="markdown",
                content_payload={"content": md_content}
            )
        except Exception as e:
            logger.error(f"群机器人推送异常: {e}")
            results["webhook"] = False

    # 3. 尝试自建应用工作台消息推送 (message/send)
    agentid = cfg.get("agentid")
    touser = cfg.get("touser")
    toparty = cfg.get("toparty")
    if agentid and (touser or toparty) and cfg.get("corpid") and cfg.get("corpsecret"):
        try:
            site_base = cfg.get("site_base_url", "https://ztb.139771.xyz").rstrip("/")
            detail_url = f"{site_base}/{day_str}.html"
            metrics = get_daily_metrics(day_str)
            now_time = datetime.now().strftime("%H:%M")
            description = (
                f"<div class=\"gray\">期号：{day_str} ({now_time} 更新)</div>"
                f"<div class=\"normal\">本日共监测标讯 <b>{metrics['total_count']}</b> 条</div>"
                f"<div class=\"normal\">• 招标公告：<b>{metrics['total_zb']}</b> 条</div>"
                f"<div class=\"normal\">• 中标候选与结果：<b>{metrics['total_win']}</b> 条</div>"
                f"<div class=\"highlight\">点击卡片可直接在交建通内查看当日完整日报与项目明细</div>"
            )
            card_payload = {
                "title": f"📢 广西招投标每日情报 ({day_str})",
                "description": description,
                "url": detail_url,
                "btntxt": "进入系统查看详情"
            }
            results["app_message"] = send_app_message(
                msgtype="textcard",
                content_payload=card_payload,
                touser=touser,
                toparty=toparty,
                agentid=agentid
            )
        except Exception as e:
            logger.error(f"应用工作消息推送异常: {e}")
            results["app_message"] = False

    return results


def send_alert(day_str: str = None, message: str = "", step: str = None) -> dict:
    """向配置的交建通渠道发送告警通知"""
    if not day_str:
        day_str = datetime.now().strftime("%Y-%m-%d")

    cfg = load_config()
    results = {"appchat": None, "webhook": None, "app_message": None}
    now_time = datetime.now().strftime("%H:%M:%S")

    step_info = f"【阶段: {step}】" if step else ""
    alert_title = f"⚠️ 招投标采集运行告警 {step_info}"
    md_content = (
        f"### {alert_title}\n"
        f"> 告警时间: <font color=\"warning\">{day_str} {now_time}</font>\n\n"
        f"**告警详情**：\n"
        f"{message}\n\n"
        f"> 请登录管理控制台排查运行状态。"
    )

    chatid = cfg.get("default_chatid")
    if chatid and cfg.get("corpid") and cfg.get("corpsecret"):
        try:
            results["appchat"] = send_appchat_message(
                chatid=chatid,
                msgtype="markdown",
                content_payload={"content": md_content}
            )
        except Exception as e:
            logger.error(f"交建通群聊告警发送异常: {e}")
            results["appchat"] = False

    webhook_url = cfg.get("webhook_url")
    if webhook_url:
        try:
            results["webhook"] = send_webhook_message(
                webhook_url_or_key=webhook_url,
                msgtype="markdown",
                content_payload={"content": md_content}
            )
        except Exception as e:
            logger.error(f"交建通机器人告警发送异常: {e}")
            results["webhook"] = False

    agentid = cfg.get("agentid")
    touser = cfg.get("touser")
    toparty = cfg.get("toparty")
    if agentid and (touser or toparty) and cfg.get("corpid") and cfg.get("corpsecret"):
        try:
            results["app_message"] = send_app_message(
                msgtype="markdown",
                content_payload={"content": md_content},
                touser=touser,
                toparty=toparty,
                agentid=agentid
            )
        except Exception as e:
            logger.error(f"交建通自建应用工作台告警推送异常: {e}")
            results["app_message"] = False

    return results


def main():
    parser = argparse.ArgumentParser(description="交建通 (JJT) 自建应用群通知工具")
    parser.add_argument("--create-chat", action="store_true", help="调用接口创建一个新的群聊")
    parser.add_argument("--name", type=str, default="广西招投标每日情报群", help="群聊名称")
    parser.add_argument("--owner", type=str, help="群主 userid（必须在成员列表中）")
    parser.add_argument("--users", type=str, help="初始群成员列表，逗号分隔，例如 'zhangsan,lisi'")
    parser.add_argument("--chatid", type=str, help="自定义群 ID（可选，英文/数字/下划线）")
    parser.add_argument("--send-daily", action="store_true", help="推送今日日报卡片到群聊")
    parser.add_argument("--day", type=str, help="指定日报日期 (YYYY-MM-DD)")
    parser.add_argument("--test", action="store_true", help="发送一条测试群消息")
    parser.add_argument("--webhook", type=str, help="指定群机器人 Webhook URL 或 Key 发送测试消息")
    parser.add_argument("--token", action="store_true", help="测试并打印当前 access_token")
    parser.add_argument("--alert", type=str, help="发送一条测试告警消息")
    parser.add_argument("--agent-info", action="store_true", help="查询已对接的交建通自建应用详情")
    parser.add_argument("--send-app-msg", type=str, help="向指定人员或部门发送自建应用测试消息")
    parser.add_argument("--touser", type=str, help="指定接收人账号 (多账号以 | 分隔)")
    parser.add_argument("--toparty", type=str, help="指定接收部门 ID (多部门以 | 分隔)")

    args = parser.parse_args()

    if args.token:
        token = get_access_token(force_refresh=True)
        if token:
            print(f"[OK] 成功获取 Token: {token[:12]}... (共{len(token)}位)")
        else:
            print("[FAIL] 获取 Token 失败，请检查配置与网络")
        return

    if args.create_chat:
        if not args.owner or not args.users:
            print("错误: 创建群聊必须指定 --owner 和 --users (例如: --owner zhangsan --users zhangsan,lisi)")
            sys.exit(1)
        userlist = [u.strip() for u in args.users.replace("，", ",").split(",") if u.strip()]
        if args.owner not in userlist:
            userlist.append(args.owner)
        print(f"正在创建交建通群聊: {args.name}...")
        cid = create_appchat(name=args.name, owner=args.owner, userlist=userlist, chatid=args.chatid)
        if cid:
            print(f"[OK] 群聊创建成功，chatid: {cid}")
            print("提示: 已自动写入 config_jjt.json 的 default_chatid，并已下发初始化欢迎消息激活该群！")
        else:
            print("[FAIL] 群聊创建失败，请查看日志输出")
        return

    if args.send_daily:
        day = args.day or datetime.now().strftime("%Y-%m-%d")
        print(f"正在向交建通渠道推送 {day} 招投标日报...")
        res = send_daily_summary(day_str=day)
        print(f"推送完成，结果: {res}")
        return

    if args.alert:
        day = args.day or datetime.now().strftime("%Y-%m-%d")
        print(f"正在发送告警通知: {args.alert}...")
        res = send_alert(day_str=day, message=args.alert, step="测试验证")
        print(f"告警发送完成，结果: {res}")
        return

    if args.webhook:
        print(f"正在向 Webhook 发送测试消息: {args.webhook}...")
        content = {
            "content": f"🔔 **交建通/企业微信群机器人联调测试**\n- 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n- 运行环境: 广西招投标数据中心\n- 状态: 连通正常"
        }
        ok = send_webhook_message(webhook_url_or_key=args.webhook, msgtype="markdown", content_payload=content)
        if ok:
            print("[OK] Webhook 测试消息发送成功！")
        else:
            print("[FAIL] Webhook 测试消息发送失败")
        return

    if args.test:
        cfg = load_config()
        cid = args.chatid or cfg.get("default_chatid")
        if not cid:
            print("错误: 未指定 --chatid 且配置中无 default_chatid，请先创建群或配置 chatid")
            sys.exit(1)
        content = {
            "content": f"🔔 **交建通应用接口联调测试**\n- 发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n- 运行环境: 国内 VPS (8.134.69.22)\n- 状态: 正常连通"
        }
        ok = send_appchat_message(chatid=cid, msgtype="markdown", content_payload=content)
        if ok:
            print("[OK] 测试消息发送成功！")
        else:
            print("[FAIL] 测试消息发送失败")
        return

    if args.agent_info:
        cfg = load_config()
        agentid = cfg.get("agentid")
        print(f"正在查询交建通自建应用信息 (AgentId: {agentid})...")
        info = get_agent_info(agentid)
        if info and info.get("errcode") == 0:
            print("[OK] 自建应用对接正常！")
            print(f"  • 应用名称: {info.get('name')}")
            print(f"  • 应用ID (AgentId): {info.get('agentid')}")
            print(f"  • 主页地址: {info.get('home_url')}")
            print(f"  • 可信域名: {info.get('redirect_domain')}")
            partys = info.get("allow_partys", {}).get("partyid", [])
            users = info.get("allow_userinfos", {}).get("user", [])
            print(f"  • 可见部门ID: {partys}")
            print(f"  • 可见人员数: {len(users)}")
        else:
            print(f"[FAIL] 获取自建应用信息失败: {info}")
        return

    if args.send_app_msg:
        cfg = load_config()
        touser = args.touser or cfg.get("touser")
        toparty = args.toparty or cfg.get("toparty")
        print(f"正在以自建应用身份发送测试工作台消息 (touser={touser}, toparty={toparty})...")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        card_payload = {
            "title": "🔔 交建通自建应用工作台消息联调测试",
            "description": (
                f"<div class=\"gray\">测试时间：{now_str}</div>"
                f"<div class=\"normal\">{args.send_app_msg}</div>"
                f"<div class=\"highlight\">系统状态：运行正常</div>"
            ),
            "url": cfg.get("site_base_url", "https://ztb.139771.xyz"),
            "btntxt": "进入系统"
        }
        ok = send_app_message(
            msgtype="textcard",
            content_payload=card_payload,
            touser=touser,
            toparty=toparty
        )
        if ok:
            print("[OK] 自建应用工作台消息发送成功！")
        else:
            print("[FAIL] 自建应用工作台消息发送失败")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
