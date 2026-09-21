# -*- coding: utf-8 -*-
"""微信服务号模板消息推送模块：
1. 自动获取并维护 access_token
2. 支持推送日常采集日报卡片（模版: 招标公告通知 t-hj2RwfI8oIeI9gRJdPOPgijcvJJk7IghTN3gwQpAc）
3. 支持推送异常告警卡片（模版: 任务执行失败告警）
4. 消息点击直达 VPS 对应日期的日报明细或首页，无外部三方依赖（纯标准库 urllib）
"""

import sys
# 控制台编码保护
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json
import logging
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


def get_all_followers(access_token: str) -> list:
    """获取关注该服务号/公众号的全部用户 OpenID 列表"""
    if not access_token:
        return []
    openids = []
    next_openid = ""
    base_url = "https://api.weixin.qq.com/cgi-bin/user/get?access_token=" + access_token
    try:
        while True:
            fetch_url = f"{base_url}&next_openid={next_openid}" if next_openid else base_url
            req = urllib.request.Request(fetch_url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("errcode"):
                    print(f"[微信推送] 拉取关注用户列表失败: {data}")
                    break
                data_inner = data.get("data", {})
                sub_ids = data_inner.get("openid", []) if isinstance(data_inner, dict) else []
                openids.extend(sub_ids)
                next_openid = data.get("next_openid", "")
                # 无下一批次或已拉完全部
                if not next_openid or len(openids) >= data.get("total", 0) or not sub_ids:
                    break
        print(f"[微信推送] 成功获取公众号关注用户清单，共 {len(openids)} 人")
        return openids
    except Exception as exc:
        print(f"[微信推送] 拉取全员关注列表异常: {exc}")
        return openids


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
    touser_raw = getattr(config, "WECHAT_TOUSER", "").strip()
    tpl_id = config.WECHAT_TEMPLATE_ID

    if not (appid and appsecret and touser_raw and tpl_id):
        print("[微信推送] 微信配置不完整，跳过日常推送（需配置 WECHAT_APPID, WECHAT_APPSECRET, WECHAT_TOUSER, WECHAT_TEMPLATE_ID）")
        return False

    token = get_access_token(appid, appsecret)
    if not token:
        return False

    # 解析目标接收人群
    target_users = []
    if touser_raw.lower() in ("@all", "all", "所有人"):
        target_users = get_all_followers(token)
        if not target_users:
            print("[微信推送] 提示：当前公众号暂无已关注粉丝或未获取到 OpenID 列表")
            return False
    else:
        target_users = [u.strip() for u in touser_raw.replace("，", ",").split(",") if u.strip()]

    if not target_users:
        print("[微信推送] 目标接收人列表为空")
        return False

    base_url = getattr(config, "SITE_BASE_URL", getattr(config, "BASE_URL", "https://ztb.139771.xyz")).rstrip("/")
    if base_url:
        page_url = f"{base_url}/{day}.html"
    else:
        page_url = f"http://127.0.0.1:8089/{day}.html"

    title_val = f"广西全区招投标公告日报（{day}）"
    content_val = f"全区共采集 {total_count} 条，重点预警标讯 {focus_count} 条。"
    if failed_steps:
        content_val += f" 注意：环节 {', '.join(failed_steps)} 执行有警报。"

    success_cnt = 0
    for uid in target_users:
        payload = {
            "touser": uid,
            "template_id": tpl_id,
            "url": page_url,
            "data": {
                "first": {"value": title_val, "color": "#1e293b"},
                "keyword1": {"value": "广西公共资源交易 · 崇左阳光采购", "color": "#475569"},
                "keyword2": {"value": day, "color": "#2563eb"},
                "keyword3": {"value": content_val, "color": "#d97706" if focus_count > 0 else "#059669"},
                "remark": {"value": "点击本通知即可直接在手机端查看今日完整标讯明细与筛选。", "color": "#64748b"}
            }
        }
        if send_template_message(token, payload):
            success_cnt += 1

    print(f"[微信推送] 日报模板消息发送完成: 成功 {success_cnt}/{len(target_users)}")
    if success_cnt > 0:
        record_push_success()
        return True
    return False


def send_alert(day: str, error_msg: str, step_name: str = "每日定时任务") -> bool:
    """发送异常告警模版消息"""
    if not getattr(config, "PUSH_NOTIFY_ERROR", True):
        return False
    appid = config.WECHAT_APPID
    appsecret = config.WECHAT_APPSECRET
    # 异常告警接收人：仅推送给管理员，绝不推送给普通关注者
    admin_touser = getattr(config, "WECHAT_ADMIN_TOUSER", "").strip()
    if not admin_touser:
        # 若未单独配置管理员，回退使用常规接收人中指定的具体 OpenID（若为 @all 则安全跳过，防止报错发给全员）
        regular_touser = getattr(config, "WECHAT_TOUSER", "").strip()
        if regular_touser and regular_touser.lower() not in ("@all", "all", "所有人"):
            admin_touser = regular_touser.replace("，", ",").split(",")[0].strip()

    tpl_id = config.WECHAT_ALERT_TEMPLATE_ID or config.WECHAT_TEMPLATE_ID

    if not (appid and appsecret and admin_touser and tpl_id):
        print("[微信推送] 未配置管理员接收人或微信凭证不完整，跳过异常告警（需配置 WECHAT_ADMIN_TOUSER）")
        return False

    token = get_access_token(appid, appsecret)
    if not token:
        return False

    admin_users = [u.strip() for u in admin_touser.replace("，", ",").split(",") if u.strip()]
    if not admin_users:
        return False

    base_url = getattr(config, "SITE_BASE_URL", getattr(config, "BASE_URL", "https://ztb.139771.xyz")).rstrip("/")
    page_url = f"{base_url}/" if base_url else "http://127.0.0.1:8089/"

    alert_cnt = 0
    for auid in admin_users:
        if tpl_id == config.WECHAT_ALERT_TEMPLATE_ID and config.WECHAT_ALERT_TEMPLATE_ID:
            payload = {
                "touser": auid,
                "template_id": tpl_id,
                "url": page_url,
                "data": {
                    "first": {"value": f"⚠️ 招标采集任务执行异常告警（{day}）", "color": "#dc2626"},
                    "keyword1": {"value": step_name, "color": "#1e293b"},
                    "keyword2": {"value": error_msg[:100], "color": "#dc2626"},
                    "remark": {"value": "【管理员专报】请检查服务器运行日志以排查恢复。", "color": "#64748b"}
                }
            }
        else:
            # 回退使用普通模板
            payload = {
                "touser": auid,
                "template_id": tpl_id,
                "url": page_url,
                "data": {
                    "first": {"value": f"⚠️ 采集任务异常告警（{day}）", "color": "#dc2626"},
                    "keyword1": {"value": "系统故障告警", "color": "#dc2626"},
                    "keyword2": {"value": day, "color": "#1e293b"},
                    "keyword3": {"value": f"{step_name} 失败: {error_msg[:60]}", "color": "#dc2626"},
                    "remark": {"value": "【管理员专报】请检查服务器日志以恢复自动化流程。", "color": "#64748b"}
                }
            }
        if send_template_message(token, payload):
            alert_cnt += 1

    print(f"[微信推送] 管理员异常告警发送完成: 成功 {alert_cnt}/{len(admin_users)}")
    return alert_cnt > 0



def check_push_condition(force: bool = False, total_count: int = 0, focus_count: int = 0, max_amount: float = 0.0):
    """
    检查当前时刻与采集数据是否满足推送条件（支持多条规则同时生效）：
    1. force=True：手动触发或强制模式，直接放行。
    2. 多规则评估（支持多规则并行生效，满足任意已启用的规则即触发）：
       - 'focus': 命中重点项目、重点业主（无门槛立即推）或通用预警词（达金额门槛立即推），不受最低标讯数门槛限制
       - 'batch_time': 到达每日指定批次时段集中归集推送
    返回 (should_push: bool, reason: str)
    """
    if force:
        return True, "手动/强制触发"

    # 获取当前启用的全部推送规则清单
    active_rules = list(getattr(config, "PUSH_TRIGGER_RULES", []))
    if not active_rules:
        # 兼容旧版配置项
        if getattr(config, "PUSH_ALERT_FOCUS", True):
            active_rules.append("focus")
        old_mode = getattr(config, "PUSH_TRIGGER_MODE", "")
        if old_mode == "batch_time":
            active_rules.append("batch_time")
        elif old_mode == "focus_only":
            if "focus" not in active_rules:
                active_rules.append("focus")
        if not active_rules:
            active_rules = ["focus", "batch_time", "error"]

    # 1. 优先评估不受数量门槛限制的即时预警规则（重点项目与重点业主无金额门槛，命中即推）
    if "focus" in active_rules and focus_count > 0:
        return True, f"命中重点跟踪规则：发现 {focus_count} 条重点标讯（重点项目/重点业主/达标预警词），触发即时强推"

    # 2. 最低数量门槛限制（针对定时归集推送）
    min_cnt = int(getattr(config, "PUSH_MIN_COUNT", 1))
    if total_count > 0 and total_count < min_cnt:
        return False, f"当日标讯总数 ({total_count}) 低于设定的最低推送门槛 ({min_cnt} 条)，跳过常规推送"

    from datetime import datetime, time
    import sqlite3
    now = datetime.now()
    cur_time = now.time()
    
    state_file = getattr(config, "STATE_DIR", Path("data/state")) / "wechat_push_state.json"
    state = {}
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
            
    today_str = now.strftime("%Y-%m-%d")

    # 3. 定时批次时段归集推送
    if "batch_time" in active_rules:
        batch_hours_str = getattr(config, "PUSH_BATCH_HOURS", "08:00, 17:30")
        for hour_item in batch_hours_str.replace("，", ",").split(","):
            hour_item = hour_item.strip()
            if not hour_item:
                continue
            try:
                parts = hour_item.split(":")
                bh = int(parts[0])
                bm = int(parts[1]) if len(parts) > 1 else 0
                # 检查当前时间是否在设定批次后 30 分钟窗口内
                start_sec = bh * 3600 + bm * 60
                end_sec = start_sec + 30 * 60
                now_sec = cur_time.hour * 3600 + cur_time.minute * 60 + cur_time.second
                if start_sec <= now_sec <= end_sec:
                    batch_key = f"batch_{bh:02d}{bm:02d}_{today_str}"
                    if state.get(batch_key):
                        return False, f"今日 [{hour_item}] 批次已推送过，避免重复推送"
                    return True, f"命中定时批次规则：当前处于批次时段 [{hour_item}] 窗口内"
            except Exception:
                continue
        return False, f"当前未处于任何设定的定时批次时段窗口 ({batch_hours_str})，当前时间 {cur_time.strftime('%H:%M')}"

    return False, f"当前未满足任何已启用的推送规则 (当前启用规则: {', '.join(active_rules) if active_rules else '无'})"


def record_push_success():
    """记录成功推送的时间与批次"""
    from datetime import datetime, time
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    cur_time = now.time()
    
    state_file = getattr(config, "STATE_DIR", Path("data/state")) / "wechat_push_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
            
    state["last_regular_push_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
    if time(17, 0) <= cur_time <= time(19, 0):
        state["last_afternoon_push_date"] = today_str
    elif time(7, 30) <= cur_time <= time(9, 30):
        state["last_morning_push_date"] = today_str
        
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[微信推送] 保存推送状态失败: {e}")


def test_push() -> bool:
    """测试推送微信卡片消息"""
    print("开始执行微信服务号推送自检测试...")
    day = config.today()
    ok = send_daily_summary(day, total_count=100, focus_count=10)
    if ok:
        print("微信推送测试成功！")
    else:
        print("微信推送测试失败。")
    return ok


if __name__ == "__main__":
    print("测试微信通知模版推送...")
    day = config.today()
    ok = send_daily_summary(day, total_count=182, focus_count=14)
    print("推送结果:", ok)
