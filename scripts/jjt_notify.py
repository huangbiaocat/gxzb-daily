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
    """读取本地交建通配置（不入库）"""
    if not CONFIG_PATH.exists():
        logger.warning(f"交建通配置文件不存在: {CONFIG_PATH}，请参考 config_jjt.example.json 创建")
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取交建通配置失败: {e}")
        return {}


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
    parser.add_argument("--token", action="store_true", help="测试并打印当前 access_token")

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
        print(f"正在向群聊推送 {day} 招投标日报卡片...")
        ok = send_daily_report_card(day_str=day, chatid=args.chatid)
        if ok:
            print("[OK] 日报卡片推送成功！")
        else:
            print("[FAIL] 日报卡片推送失败")
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

    parser.print_help()


if __name__ == "__main__":
    main()
