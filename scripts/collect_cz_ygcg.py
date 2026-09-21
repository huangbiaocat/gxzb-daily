# -*- coding: utf-8 -*-
"""崇左阳光采购平台采集模块（仅采工程类）。

平台地址: https://cz.gxygcg.com/purchase/list
列表接口: https://cz.gxygcg.com/bbw_prod/bbw_notice/list
详情接口: https://cz.gxygcg.com/bbw_prod/bbw_notice/notice_detail
前端详情: https://cz.gxygcg.com/purchase/detail/?purchase_projects_ids={pid}&notice_type={ntype}&notice_id={nid}
"""

import sys
# 控制台编码保护
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import datetime
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import config  # noqa: E402

CZ_BASE_URL = "https://cz.gxygcg.com"
CZ_LIST_API = f"{CZ_BASE_URL}/bbw_prod/bbw_notice/list"
CZ_DETAIL_API = f"{CZ_BASE_URL}/bbw_prod/bbw_notice/notice_detail"
CZ_REGION_CODE = "451400000000"  # 崇左市行政区划代码

# 公告类型映射:
# 1: 采购/招标公告 -> 招标公告 (001001001002)
# 2: 变更公告 -> 答疑澄清/变更 (001001001004)
# 3: 答疑澄清 -> 答疑澄清/变更 (001001001004)
# 4: 候选人公示 -> 中标候选人公示 (001001001005)
# 5: 结果公告 -> 中标公告 (001001001006)
CZ_NOTICE_TYPE_MAP = {
    1: {"stage": "招标公告", "categorynum": "001001001002"},
    2: {"stage": "澄清/答疑", "categorynum": "001001001003"},
    3: {"stage": "澄清/答疑", "categorynum": "001001001003"},
    4: {"stage": "中标公示", "categorynum": "001001001005"},
    5: {"stage": "中标公告", "categorynum": "001001001006"},
}

def get_cz_stage_and_category(ntype: int, title: str = ""):
    if ntype in CZ_NOTICE_TYPE_MAP:
        return CZ_NOTICE_TYPE_MAP[ntype]["stage"], CZ_NOTICE_TYPE_MAP[ntype]["categorynum"]
    t = title.lower()
    if any(k in t for k in ["候选人", "候选"]):
        return "中标公示", "001001001005"
    if any(k in t for k in ["中标", "成交", "结果"]):
        return "中标公告", "001001001006"
    if any(k in t for k in ["变更", "澄清", "答疑", "补充", "更正", "修改"]):
        return "澄清/答疑", "001001001003"
    return "招标公告", "001001001002"

def _open_url(url, timeout=5):
    """带重试与绕过系统代理的请求。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"{CZ_BASE_URL}/purchase/list",
        "Accept": "application/json, text/plain, */*",
    }
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(2):
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            if attempt == 1:
                raise e
            time.sleep(0.5)


def normalize_cz_record(it, day=None):
    """将崇左阳光采购的一条工程记录规范化为统一结构。"""
    infoid = str(it.get("noticeId") or "").strip()
    title = str(it.get("noticeTitle") or "").strip()
    if not infoid or not title:
        return None

    ntype = int(it.get("noticeType") or 1)
    stage, categorynum = get_cz_stage_and_category(ntype, title)

    # 阳光采购平台工程类均归入房建市政/工程类别
    industry = "房建市政"
    stage_key = config.STAGE_KEY_MAP.get(stage, "other")
    badge_class = config.BADGE_CLASS_MAP.get(stage, "badge-gray")

    # 发布时间
    ntime = it.get("noticeTime")
    if ntime:
        try:
            tz_bj = datetime.timezone(datetime.timedelta(hours=8))
            pub_time = datetime.datetime.fromtimestamp(ntime / 1000, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pub_time = str(it.get("publishDate") or "")
    else:
        pub_time = str(it.get("publishDate") or "")

    owner = str(it.get("tendererName") or it.get("creatorName") or "").strip()

    # 目标链接与详情 API 链接
    pid = str(it.get("purchaseProjectsIds") or "")
    link = f"{CZ_BASE_URL}/purchase/detail/?purchase_projects_ids={pid}&notice_type={ntype}&notice_id={infoid}"
    detail_url = f"{CZ_DETAIL_API}?purchase_projects_ids={pid}&notice_type={ntype}&notice_id={infoid}"

    reasons = []
    # 1. 重点跟踪项目（无任何金额门槛，命中即触发）
    proj_hits = [p for p in getattr(config, "FOCUS_PROJECTS", []) if config.fuzzy_match(p, title)]
    if proj_hits:
        reasons.extend(["命中重点项目: %s" % p for p in proj_hits])

    # 2. 重点跟踪业主（无任何金额门槛，命中即触发）
    owner_hits = [o for o in getattr(config, "FOCUS_OWNERS", []) if (config.fuzzy_match(o, title) or (owner and config.fuzzy_match(o, owner)))]
    if owner_hits:
        reasons.extend(["命中重点业主: %s" % o for o in owner_hits])

    # 3. 重点类型
    type_hits = [t for t in getattr(config, "FOCUS_PROJECT_TYPES", []) if t in industry or config.fuzzy_match(t, title)]
    if type_hits:
        reasons.extend(["命中重点类型: %s" % t for t in type_hits])

    # 4. 重点预警关键词（若设置了金额门槛则需达到金额门槛）
    raw_kw_hits = [k for k in getattr(config, "FOCUS_KEYWORDS", []) if k in title]
    kw_hits = []
    if raw_kw_hits:
        min_amount = getattr(config, "FOCUS_MIN_AMOUNT", 0.0) or 0.0
        if min_amount > 0:
            amt = config.extract_amount_from_title(title)
            if amt is not None and amt >= min_amount:
                from extractors.normalize import format_money
                kw_hits = raw_kw_hits
                reasons.extend(["命中关键词: %s (金额%s >= 门槛%s)" % (k, format_money(amt), format_money(min_amount)) for k in kw_hits])
        else:
            kw_hits = raw_kw_hits
            reasons.extend(["命中关键词: %s" % k for k in kw_hits])

    is_focus = 1 if (proj_hits or owner_hits or type_hits or kw_hits) else 0
    focus_tags = []
    if proj_hits:
        focus_tags.append("重点项目")
    if owner_hits:
        focus_tags.append("重点业主")
    if kw_hits:
        focus_tags.append("重点关键词")
    if type_hits:
        focus_tags.append("重点类型")
    if is_focus and not focus_tags:
        focus_tags.append("重点关注")

    return {
        "infoid": infoid,
        "title": title,
        "categorynum": categorynum,
        "industry": industry,
        "stage": stage,
        "stage_key": stage_key,
        "badge_class": badge_class,
        "areacode": "451400",
        "areaname": "崇左市",
        "source": "崇左阳光采购",
        "pub_time": pub_time,
        "link": link,
        "detail_url": detail_url,
        "is_focus": is_focus,
        "focus_tags": focus_tags,
        "focus_reason": reasons,
        "owner": owner,
    }


def collect_cz_ygcg(day, max_pages=25):
    """从崇左阳光采购平台采集当日【工程类】公告。
    
    严格锁定 project_type: '工程'，其下项目有一个算一个全部采集，不做标题二次过滤。
    """
    params = {
        "region_code": CZ_REGION_CODE,
        "project_type": "工程",
        "page_size": 10,
    }
    
    collected_rows = []
    raw_records = []

    print(f"-> 正在采集【崇左阳光采购】工程类公告 (日期: {day})...", flush=True)
    for page in range(1, max_pages + 1):
        params["page"] = page
        query_str = urllib.parse.urlencode(params)
        url = f"{CZ_LIST_API}?{query_str}"
        try:
            content = _open_url(url)
            resp = json.loads(content.decode("utf-8"))
        except Exception as e:
            print(f"   [提示] 崇左阳光采购 第 {page} 页请求结束/异常: {e}", flush=True)
            break

        data_obj = resp.get("data")
        if isinstance(data_obj, dict):
            items = data_obj.get("data") or []
        elif isinstance(data_obj, list):
            items = data_obj
        else:
            items = []
        if not items:
            break

        raw_records.extend(items)
        has_current_or_newer = False

        for it in items:
            ntime = it.get("noticeTime")
            if not ntime:
                continue
            tz_bj = datetime.timezone(datetime.timedelta(hours=8))
            item_date = datetime.datetime.fromtimestamp(ntime / 1000, tz=tz_bj).strftime("%Y-%m-%d")
           
            if item_date == day:
                has_current_or_newer = True
                norm = normalize_cz_record(it, day=day)
                if norm:
                    collected_rows.append(norm)
            elif item_date > day:
                has_current_or_newer = True

        # 如果本页所有记录的发布日期都已早于 day，则无需再请求下一页（列表按时间倒序）
        if not has_current_or_newer:
            break

    print(f"   崇左阳光采购采集完成：获取 {len(raw_records)} 条原始记录，匹配当日 {len(collected_rows)} 条", flush=True)
    return collected_rows, raw_records


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="采集崇左阳光采购平台工程类公告")
    parser.add_argument("--date", default=config.today(), help="采集日期 YYYY-MM-DD")
    args = parser.parse_args()
    rows, _ = collect_cz_ygcg(args.date)
    for r in rows:
        print(f"[{r['stage']}] {r['areaname']} - {r['pub_time']} - {r['title']} - {r['link']}")
