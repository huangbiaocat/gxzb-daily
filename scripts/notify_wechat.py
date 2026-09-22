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
from datetime import datetime, time
from pathlib import Path

# 支持相对导入与顶层导入
try:
    import config
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import config

logger = logging.getLogger(__name__)

# 全局增量推送缓存（供 check_push_condition 与紧接着的 send_daily_summary 共享，避免重复读库）
_CACHED_INCREMENTAL = None


def format_time_range(since_str: str, until_dt=None) -> str:
    """将推送时间跨度格式化为友好的中文显示（如：昨晚 17:30 ~ 今晨 08:00）"""
    from datetime import datetime
    if until_dt is None:
        until_dt = datetime.now()
    if not since_str:
        return f"截至今日 {until_dt.strftime('%H:%M')}"
    try:
        since_dt = datetime.strptime(since_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return f"{since_str} ~ {until_dt.strftime('%H:%M')}"

    delta_days = (until_dt.date() - since_dt.date()).days
    since_hour = since_dt.hour
    until_hour = until_dt.hour

    if delta_days == 1:
        since_txt = f"昨晚 {since_dt.strftime('%H:%M')}" if since_hour >= 17 else f"昨日 {since_dt.strftime('%H:%M')}"
        until_txt = f"今晨 {until_dt.strftime('%H:%M')}" if until_hour < 12 else f"今日 {until_dt.strftime('%H:%M')}"
        return f"{since_txt} ~ {until_txt}"
    elif delta_days == 0:
        return f"今日 {since_dt.strftime('%H:%M')} ~ {until_dt.strftime('%H:%M')}"
    else:
        return f"{since_dt.strftime('%m-%d %H:%M')} ~ {until_dt.strftime('%m-%d %H:%M')}"


def get_incremental_notices(since_time_str: str = None, until_time_str: str = None) -> list:
    """
    获取从 since_time_str 到 until_time_str 之间入库或发布的新增标讯。
    优先从 SQLite notices 表查询并结合 daily json 与推送历史状态去重与补全字段。
    """
    if not since_time_str:
        return []

    from datetime import datetime
    if not until_time_str:
        until_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    state_file = getattr(config, "STATE_DIR", Path("data/state")) / "wechat_push_state.json"
    pushed_infoids = set()
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                sdata = json.load(f)
                pushed_infoids = set(sdata.get("pushed_infoids", []))
        except Exception:
            pass

    incremental = []
    seen_infoids = set()

    # 1. 优先从 SQLite notices 表查询
    db_path = getattr(config, "DB_PATH", None)
    if db_path and Path(db_path).exists():
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            sql = """
            SELECT infoid, categorynum, industry, stage, stage_key, title, project_name, region, pub_time, link, detail_url, created_at, pushed_at
            FROM notices
            WHERE (
                (created_at > ? AND created_at <= ?)
                OR (pub_time > ? AND pub_time <= ? AND (pushed_at IS NULL OR pushed_at <= ?))
            )
            ORDER BY created_at ASC, pub_time ASC
            """
            rows = cur.execute(sql, (since_time_str, until_time_str, since_time_str, until_time_str, since_time_str)).fetchall()
            for r in rows:
                r_dict = dict(r)
                iid = r_dict.get("infoid")
                if iid and iid not in pushed_infoids and iid not in seen_infoids:
                    seen_infoids.add(iid)
                    incremental.append(r_dict)
            conn.close()
        except Exception as exc:
            logger.warning(f"查询 SQLite 增量标讯异常: {exc}")

    # 2. 结合今日与昨日的 daily json 进行字段补全或兜底
    now_dt = datetime.now()
    check_dates = [now_dt.strftime("%Y-%m-%d")]
    try:
        since_dt = datetime.strptime(since_time_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
        if since_dt.strftime("%Y-%m-%d") not in check_dates:
            check_dates.append(since_dt.strftime("%Y-%m-%d"))
    except Exception:
        pass

    for d in check_dates:
        jp = getattr(config, "DAILY_DIR", Path("data/daily")) / f"{d}.json"
        if jp.exists():
            try:
                with open(jp, "r", encoding="utf-8") as f:
                    d_items = json.load(f)
                if isinstance(d_items, list):
                    for item in d_items:
                        iid = item.get("infoid")
                        if iid in seen_infoids:
                            for ex in incremental:
                                if ex.get("infoid") == iid:
                                    for k, v in item.items():
                                        if k not in ex or not ex[k]:
                                            ex[k] = v
                                    break
                        else:
                            pub = item.get("pub_time", "")
                            if iid and iid not in pushed_infoids and pub > since_time_str and pub <= until_time_str:
                                seen_infoids.add(iid)
                                incremental.append(item)
            except Exception:
                pass

    # 3. 补充重点标记
    try:
        from scripts.rule_checker import check_notice_focus
        for it in incremental:
            if "is_focus" not in it:
                f_stat, f_reason, f_tags = check_notice_focus(it)
                it["is_focus"] = 1 if f_stat else 0
                it["focus_reason"] = f_reason
                it["focus_tags"] = f_tags
    except Exception:
        pass

    return incremental


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


def build_rich_summary(day: str, total_count: int, focus_count: int, failed_steps: list = None, is_final: bool = False, incremental_items: list = None, since_time_str: str = None, until_time_str: str = None) -> dict:
    """构建用于微信模板消息推送的丰富内容（同时兼容 {{content.DATA}} 与 传统结构化模版，支持增量提醒）"""
    from collections import Counter
    from datetime import datetime

    # 若为增量提醒模式（通过 incremental_items 传入或由条件发送触发）
    if incremental_items is not None:
        inc_count = len(incremental_items)
        until_dt = None
        if until_time_str:
            try:
                until_dt = datetime.strptime(until_time_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        time_desc = format_time_range(since_time_str, until_dt or datetime.now())
        focus_items = [it for it in incremental_items if it.get("is_focus")]
        focus_count = len(focus_items)

        city_counter = Counter((it.get("areaname") or it.get("region") or "广西").replace("市", "") for it in incremental_items if it.get("areaname") or it.get("region"))
        top_cities = [c for c, _ in city_counter.most_common(4)]
        city_str = "、".join(top_cities) if top_cities else "全区"

        display_tuples = [(it, True) for it in focus_items]
        existing_keys = {it.get("infoid") or it.get("title") for it in focus_items}
        for it in incremental_items:
            if len(display_tuples) >= 3:
                break
            k = it.get("infoid") or it.get("title")
            if k not in existing_keys:
                display_tuples.append((it, False))
                existing_keys.add(k)

        lines = [
            "🔔 【广西招投标标讯增量提醒】",
            f"⏰ 统计区间：{time_desc}",
            f"🆕 区间新增：共 {inc_count} 条标讯",
            f"🎯 重点关注：{focus_count} 条",
            f"📍 涉及地区：{city_str}",
            "--------------------------------",
            "⭐ 本次新增标讯精选：" if display_tuples else "📌 本时段暂无更多新增标讯。"
        ]
        for it, is_f in display_tuples[:3]:
            city = (it.get("areaname") or it.get("region") or "广西").replace("市", "")
            stage = it.get("stage") or "公告"
            tag = "🔥" if is_f else "•"
            title = it.get("title", "").strip().replace("\r", "").replace("\n", " ")
            if len(title) > 30:
                title = title[:29].rstrip("(-_/:· ") + "…"
            lines.append(f"{tag} 【{city}·{stage}】{title}")
            pub = it.get("pub_time", "")
            amt = it.get("amount")
            meta_parts = []
            if pub:
                meta_parts.append(f"时间: {pub[5:16] if len(pub) >= 16 else pub}")
            if amt:
                meta_parts.append(f"金额: {amt}")
            if meta_parts:
                lines.append(f"   {' | '.join(meta_parts)}")

        lines.append("--------------------------------")
        if failed_steps:
            lines.append(f"⚠️ 运行提示：环节 {', '.join(failed_steps)} 执行有警报")
        lines.append("👉 点击下方卡片直接在手机端查看全部明细及筛选")
        content_text = "\n".join(lines)

        title_val = f"🔔 广西标讯增量提醒（新增 {inc_count} 条）"
        if display_tuples:
            first_p = display_tuples[0][0]
            c0 = (first_p.get("areaname") or first_p.get("region") or "").replace("市", "")
            t0 = first_p.get("title", "").strip().replace("\r", "").replace("\n", " ")
            if len(t0) > 22:
                t0 = t0[:21].rstrip("(-_/:· ") + "…"
            kw1 = f"新增 {inc_count} 条：【{c0}】{t0}"
        else:
            kw1 = f"新增 {inc_count} 条标讯（{time_desc}）"

        kw2 = city_str
        kw3 = f"重点预警标讯 {focus_count} 条" if focus_count > 0 else "常规增量标讯"
        kw4 = time_desc
        remark_val = f"自上次推送（{since_time_str or '上次'}）以来新增 {inc_count} 条标讯，点击卡片即刻查看明细。"

        return {
            "content_text": content_text,
            "title_val": title_val,
            "kw1": kw1,
            "kw2": kw2,
            "kw3": kw3,
            "kw4": kw4,
            "remark_val": remark_val
        }

    items = []
    json_path = config.DAILY_DIR / f"{day}.json"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    items = data
        except Exception as e:
            logger.warning(f"读取当日标讯文件异常: {e}")

    from collections import Counter
    # 行业统计
    ind_counter = Counter(it.get("industry", "其他") for it in items if it.get("industry"))
    ind_top = [f"{k[:4]} {v}条" for k, v in ind_counter.most_common(3)]
    ind_str = " | ".join(ind_top)

    # 地市统计
    city_counter = Counter(it.get("areaname", "广西") for it in items if it.get("areaname"))
    top_cities = [c.replace("市", "") for c, _ in city_counter.most_common(4)]
    city_str = "、".join(top_cities) + ("等" if len(city_counter) > 4 else "")

    # 重点项目提取
    focus_items = [it for it in items if it.get("is_focus")]
    if not focus_items and focus_count > 0:
        for it in items:
            title = it.get("title", "")
            for kw in getattr(config, "FOCUS_PROJECT_KEYWORDS", []):
                if kw in title:
                    focus_items.append(it)
                    break

    # 组织富文本（用于测试号万能模板 {{content.DATA}}）
    lines = []
    if is_final:
        lines.append(f"【广西招投标终版日报 · {day}】")
        lines.append(f"📊 昨日汇总：全天共采集 {total_count} 条，重点标讯 {focus_count} 条")
    else:
        lines.append(f"【广西招投标公告日报 · {day}】")
        lines.append(f"📊 今日动态：共采集 {total_count} 条，重点标讯 {focus_count} 条")

    if ind_str:
        lines.append(f"🏢 行业分布：{ind_str}")

    # 组合推荐项目（优先重点项目，不足3条时用最新精选补齐）
    display_tuples = [(it, True) for it in focus_items]
    existing_keys = {it.get("id") or it.get("title") for it in focus_items}
    for it in items:
        if len(display_tuples) >= 3:
            break
        k = it.get("id") or it.get("title")
        if k not in existing_keys:
            display_tuples.append((it, False))
            existing_keys.add(k)

    if display_tuples:
        header_label = "🎯 精选重点标讯推荐：" if focus_items else "📌 最新标讯精选："
        lines.append(f"\n{header_label}")
        for it, is_f in display_tuples[:3]:
            city = (it.get("areaname") or it.get("city") or "广西").replace("市", "")
            stage = it.get("stage") or "公告"
            tag = "🔥" if is_f else "•"
            title = it.get("title", "").strip().replace("\r", "").replace("\n", " ")
            if len(title) > 30:
                title = title[:29].rstrip("(-_/:· ") + "…"
            lines.append(f"{tag} 【{city}·{stage}】{title}")
    else:
        lines.append("\n📌 今日标讯持续收集中，暂无预警标讯。")

    if failed_steps:
        lines.append(f"\n⚠️ 运行提示：环节 {', '.join(failed_steps)} 执行有警报")

    lines.append("\n👉 点击下方卡片直接在手机端查看全部明细及筛选")
    content_text = "\n".join(lines)

    # 组织单项结构化字段（用于传统模版）
    if is_final:
        title_val = f"【终版封存】广西招投标公告日报（{day}）"
        remark_val = "昨日全天数据已封存对账完成，点击查看完整标讯。"
    else:
        title_val = f"广西全区招投标公告日报（{day}）"
        remark_val = "点击本通知即可直接在手机端查看今日完整标讯明细与筛选。"

    if display_tuples:
        first_p = display_tuples[0][0]
        c0 = (first_p.get("areaname") or "").replace("市", "")
        t0 = first_p.get("title", "").strip().replace("\r", "").replace("\n", " ")
        if len(t0) > 22:
            t0 = t0[:21].rstrip("(-_/:· ") + "…"
        kw1 = f"【{c0}】{t0}" + (f" 等{len(display_tuples)}个项目" if len(display_tuples) > 1 else "")
    else:
        kw1 = f"广西全区标讯汇总（共 {total_count} 条）"

    kw2 = city_str or "广西公共资源交易 · 崇左阳光采购"
    kw3 = f"重点预警标讯 {focus_count} 条" if focus_count > 0 else "常规流转（无重点预警）"
    kw4 = day

    return {
        "content_text": content_text,
        "title_val": title_val,
        "kw1": kw1,
        "kw2": kw2,
        "kw3": kw3,
        "kw4": kw4,
        "remark_val": remark_val
    }


def send_daily_summary(day: str, total_count: int = 0, focus_count: int = 0, failed_steps: list = None, is_final: bool = False, incremental_items: list = None, since_time_str: str = None) -> bool:
    """发送每日采集概览或增量提醒模版消息"""
    global _CACHED_INCREMENTAL
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

    # 如果未显式传入增量信息，但缓存中存在（由 check_push_condition 发现），则复用
    if incremental_items is None and _CACHED_INCREMENTAL:
        incremental_items = _CACHED_INCREMENTAL.get("items")
        since_time_str = _CACHED_INCREMENTAL.get("since_time")

    # 若配置开启了条件发送，且当前不是 final 汇总，尝试拉取增量
    if incremental_items is None and getattr(config, "PUSH_CONDITIONAL_INCREMENTAL", True) and not is_final:
        state_file = getattr(config, "STATE_DIR", Path("data/state")) / "wechat_push_state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                    lpt = sdata.get("last_regular_push_time") or sdata.get("last_push_time")
                    if lpt:
                        inc = get_incremental_notices(lpt)
                        if inc:
                            incremental_items = inc
                            since_time_str = lpt
            except Exception:
                pass

    summary_data = build_rich_summary(
        day, total_count, focus_count, failed_steps, is_final,
        incremental_items=incremental_items,
        since_time_str=since_time_str
    )

    success_cnt = 0
    for uid in target_users:
        payload = {
            "touser": uid,
            "template_id": tpl_id,
            "url": page_url,
            "data": {
                # 适配自定义万能内容模板（如测试号的 {{content.DATA}}）
                "content": {"value": summary_data["content_text"], "color": "#1e293b"},
                # 适配标准结构化卡片模版（如 first / keyword1..4 / remark）
                "first": {"value": summary_data["title_val"], "color": "#1e293b"},
                "keyword1": {"value": summary_data["kw1"], "color": "#2563eb"},
                "keyword2": {"value": summary_data["kw2"], "color": "#475569"},
                "keyword3": {"value": summary_data["kw3"], "color": "#d97706" if focus_count > 0 else "#059669"},
                "keyword4": {"value": summary_data["kw4"], "color": "#64748b"},
                "remark": {"value": summary_data["remark_val"], "color": "#64748b"}
            }
        }
        if send_template_message(token, payload):
            success_cnt += 1

    print(f"[微信推送] 日报模板消息发送完成: 成功 {success_cnt}/{len(target_users)}")
    if success_cnt > 0:
        pushed_ids = []
        if incremental_items:
            pushed_ids = [it.get("infoid") for it in incremental_items if it.get("infoid")]
        else:
            jp = getattr(config, "DAILY_DIR", Path("data/daily")) / f"{day}.json"
            if jp.exists():
                try:
                    with open(jp, "r", encoding="utf-8") as f:
                        d_items = json.load(f)
                        pushed_ids = [it.get("infoid") for it in d_items if it.get("infoid")]
                except Exception:
                    pass

        b_key = _CACHED_INCREMENTAL.get("batch_key") if _CACHED_INCREMENTAL else None
        record_push_success(pushed_infoids=pushed_ids, batch_key=b_key)
        _CACHED_INCREMENTAL = None
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
                    "content": {
                        "value": f"⚠️ 招标采集异常告警（{day}）\n━━━━━━━━━━━━━━━━━━━━\n环节：{step_name}\n详情：{error_msg[:100]}\n\n【管理员专报】请检查服务器运行日志以排查恢复。",
                        "color": "#dc2626"
                    },
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
                    "content": {
                        "value": f"⚠️ 招标采集异常告警（{day}）\n━━━━━━━━━━━━━━━━━━━━\n环节：{step_name}\n详情：{error_msg[:100]}\n\n【管理员专报】请检查服务器运行日志以排查恢复。",
                        "color": "#dc2626"
                    },
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
       - 'conditional' 或 PUSH_CONDITIONAL_INCREMENTAL: 条件发送（仅自上次发送后有新增内容时发送）
    返回 (should_push: bool, reason: str)
    """
    global _CACHED_INCREMENTAL
    _CACHED_INCREMENTAL = None

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
    if total_count > 0 and min_cnt > 0 and total_count < min_cnt and focus_count == 0:
        return False, f"当日标讯总数 ({total_count}) 低于设定的最低推送门槛 ({min_cnt} 条)，跳过常规推送"

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

    # 3. 定时批次时段归集推送（含条件发送判断）
    if "batch_time" in active_rules or getattr(config, "PUSH_CONDITIONAL_INCREMENTAL", False):
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

                    # 检查是否开启“条件发送（仅有增量时发送）”
                    if getattr(config, "PUSH_CONDITIONAL_INCREMENTAL", True):
                        last_push_time = state.get("last_regular_push_time") or state.get("last_push_time")
                        if last_push_time:
                            inc_items = get_incremental_notices(
                                since_time_str=last_push_time,
                                until_time_str=now.strftime("%Y-%m-%d %H:%M:%S")
                            )
                            if len(inc_items) > 0:
                                _CACHED_INCREMENTAL = {
                                    "items": inc_items,
                                    "since_time": last_push_time,
                                    "batch_key": batch_key
                                }
                                return True, f"命中定时条件推送规则：处于批次 [{hour_item}] 窗口，自上次推送（{last_push_time}）以来新增 {len(inc_items)} 条标讯"
                            else:
                                return False, f"未触发条件推送：处于批次 [{hour_item}] 窗口，但自上次推送（{last_push_time}）以来无新增标讯（新增 0 条），跳过本次推送"
                        else:
                            # 尚无历史推送记录（首次运行）
                            if total_count > 0:
                                return True, f"首次执行定时推送：处于批次 [{hour_item}] 窗口，今日共有 {total_count} 条标讯"
                            else:
                                return False, f"首次执行定时推送：处于批次 [{hour_item}] 窗口，当前暂无标讯数据，跳过推送"
                    else:
                        # 未开启条件发送，只要在批次窗口即推送
                        return True, f"命中定时批次规则：当前处于批次时段 [{hour_item}] 窗口内"
            except Exception:
                continue
        return False, f"当前未处于任何设定的定时批次时段窗口 ({batch_hours_str})，当前时间 {cur_time.strftime('%H:%M')}"

    return False, f"当前未满足任何已启用的推送规则 (当前启用规则: {', '.join(active_rules) if active_rules else '无'})"


def record_push_success(pushed_infoids: list = None, batch_key: str = None):
    """记录成功推送的时间、批次与已推送标讯"""
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
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
            
    state["last_regular_push_time"] = now_str
    state["last_push_time"] = now_str
    if time(17, 0) <= cur_time <= time(19, 0):
        state["last_afternoon_push_date"] = today_str
    elif time(7, 30) <= cur_time <= time(9, 30):
        state["last_morning_push_date"] = today_str

    if batch_key:
        state[batch_key] = True

    # 自动标记当前时间所处批次
    batch_hours_str = getattr(config, "PUSH_BATCH_HOURS", "08:00, 17:30")
    for hour_item in batch_hours_str.replace("，", ",").split(","):
        hour_item = hour_item.strip()
        if not hour_item:
            continue
        try:
            parts = hour_item.split(":")
            bh = int(parts[0])
            bm = int(parts[1]) if len(parts) > 1 else 0
            start_sec = bh * 3600 + bm * 60
            end_sec = start_sec + 30 * 60
            now_sec = now.hour * 3600 + now.minute * 60 + now.second
            if start_sec <= now_sec <= end_sec:
                k = f"batch_{bh:02d}{bm:02d}_{today_str}"
                state[k] = True
        except Exception:
            pass

    # 维护推送标讯 ID 台账
    if pushed_infoids:
        pushed_set = set(state.get("pushed_infoids", []))
        pushed_set.update(pushed_infoids)
        # 保持最新 3000 个
        state["pushed_infoids"] = list(pushed_set)[-3000:]

        # 更新 SQLite 数据库中的 pushed_at
        db_path = getattr(config, "DB_PATH", None)
        if db_path and Path(db_path).exists():
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                placeholders = ",".join("?" for _ in pushed_infoids)
                cur.execute(f"UPDATE notices SET pushed_at = ? WHERE infoid IN ({placeholders})", [now_str] + list(pushed_infoids))
                conn.commit()
                conn.close()
            except Exception as exc:
                logger.warning(f"更新 SQLite notices.pushed_at 失败: {exc}")
        
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[微信推送] 保存推送状态失败: {e}")


def test_push() -> bool:
    """测试推送微信卡片消息"""
    print("开始执行微信服务号推送自检测试...")
    day = config.today()
    # 优先使用最新一天的实际数据
    json_path = config.DAILY_DIR / f"{day}.json"
    if not json_path.exists():
        all_jsons = sorted(config.DAILY_DIR.glob("*.json"), reverse=True)
        if all_jsons:
            day = all_jsons[0].stem
    total = 0
    focus = 0
    jp = config.DAILY_DIR / f"{day}.json"
    if jp.exists():
        try:
            with open(jp, "r", encoding="utf-8") as f:
                items = json.load(f)
                total = len(items)
                focus = sum(1 for it in items if it.get("is_focus"))
        except Exception:
            pass
    ok = send_daily_summary(day, total_count=total or 128, focus_count=focus or 3)
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
