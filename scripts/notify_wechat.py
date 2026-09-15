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

    base_url = getattr(config, "SITE_BASE_URL", getattr(config, "BASE_URL", "https://ztb.139771.xyz")).rstrip("/")
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
    ret = send_template_message(token, payload)
    if ret:
        record_push_success()
    return ret


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

    base_url = getattr(config, "SITE_BASE_URL", getattr(config, "BASE_URL", "https://ztb.139771.xyz")).rstrip("/")
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



def check_push_condition(force: bool = False):
    """
    检查当前时刻是否满足推送条件：
    1. force=True：手动触发或强制模式，直接放行。
    2. 下午17:30批次（17:30-18:00）：发送日常常规推送（记录推送信标）。
    3. 早上08:00批次（08:00-08:30）：
       检查从昨天17:30（或上次常规推送时间）至今天早上08:00之间，是否有新公告入库。
       - 有新公告：触发早上推送；
       - 无新公告：跳过推送，避免无实质更新的打扰。
    返回 (should_push: bool, reason: str)
    """
    if force:
        return True, "手动/强制触发"
    
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
    
    # 批次1：下午 17:30 - 18:00
    if time(17, 30) <= cur_time <= time(18, 0):
        if state.get("last_afternoon_push_date") == today_str:
            return False, "今日下午 17:30 批次已推送过，跳过重复推送"
        return True, "命中下午 17:30 常规推送时段"
        
    # 批次2：早上 08:00 - 08:30
    elif time(8, 0) <= cur_time <= time(8, 30):
        if state.get("last_morning_push_date") == today_str:
            return False, "今日早晨 08:00 批次已推送过，跳过重复推送"
            
        last_push_time_str = state.get("last_regular_push_time")
        if not last_push_time_str:
            # 默认使用昨天下午 17:30
            from datetime import timedelta
            yesterday_1730 = (now - timedelta(days=1)).strftime("%Y-%m-%d 17:30:00")
            last_push_time_str = yesterday_1730
            
        # 查询从 last_push_time_str 以来是否有新增公告
        new_cnt = 0
        db_path = getattr(config, "DB_PATH", Path("data/gxzb.sqlite3"))
        if db_path.exists():
            try:
                with sqlite3.connect(str(db_path)) as conn:
                    c = conn.cursor()
                    c.execute("SELECT count(*) FROM notices WHERE created_at > ?", (last_push_time_str,))
                    row = c.fetchone()
                    if row:
                        new_cnt = row[0]
            except Exception as e:
                print(f"[微信推送] 查询新增公告失败: {e}")
                
        if new_cnt > 0:
            return True, f"命中早间 08:00 推送时段（自 {last_push_time_str} 以来新增 {new_cnt} 条公告）"
        else:
            return False, f"早间 08:00 检查：自 {last_push_time_str} 以来无新增公告，跳过推送"
            
    return False, f"非指定常规推送时间窗口（17:30-18:00 或 08:00-08:30），当前时间 {cur_time.strftime('%H:%M')}"


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
