# -*- coding: utf-8 -*-
"""采集当日「工程建设」类公告（服务端前缀过滤 + offset 分页 + totalcount 对账）。

用法:
    python scripts/collect.py                      # 采集今天（按 .env 时区）
    python scripts/collect.py --date 2026-09-10    # 采集指定日期
    python scripts/collect.py --no-raw             # 不落原始响应（仅产出去重结果）
    python scripts/collect.py --page-size 200      # 覆盖每页条数（默认 API_PAGE_SIZE）

相对上一版的加固点（P1）:
    1. **服务端前缀过滤**：condition 用站点前端口径
       `{"fieldName":"categorynum","equal":<前缀>,"isLike":true,"likeType":2}`，
       过滤在服务端完成，不再依赖客户端逐条判断；
    2. **offset 分页**：`pn` 为 offset 语义，逐页递增 `pn += rn`，直到取满 `totalcount`
       （旧版固定 `pn=0/rn=500`，当某中心当日条数超过 500 时会静默漏采）；
    3. **服务端时间窗**：`time` 数组限定 `infodatepx` 当日 00:00:00 ~ 23:59:59，
       配合 `totalcount` 即可拿到"当日应有多少条"的权威口径；
    4. **对账**：逐中心比较 `已拉取条数 == totalcount`，并把对账结果落盘到
       `<REPORT_DIR>/collect-reconcile-YYYY-MM-DD.json`，不一致即视为漏采告警；
    5. **限流与熔断**：所有请求走 scripts/fetcher.py（同 Host ≥3 秒、403/429 熔断 15 分钟、
       5xx 指数退避、404 不重试）；
    6. 记录额外带上 `detail_url`（接口返回的静态详情页地址，供抓正文用），
       `link` 字段口径保持不变，页面与模板无需改动。

产物:
    <DATA_DIR>/raw/YYYY-MM-DD/center-001.json ...  原始响应（审计用，含分页元信息）
    <DATA_DIR>/collect/YYYY-MM-DD.json             去重 + 规范化后的当日全量公告
    <REPORT_DIR>/collect-reconcile-YYYY-MM-DD.json 拉取条数 vs totalcount 对账结果

退出码:
    0 全部中心成功且对账一致；3 部分中心失败 / 对账不一致（结果可能不全）；2 全部失败。
无任何第三方依赖，可直接由 cron / launchd 调度。
"""
import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config      # noqa: E402
import fetcher     # noqa: E402
import logstore    # noqa: E402

JSON_HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Referer": config.API_REFERER,
}


# ------------------------------------------------------------------ 规范化
def detail_url_of(rec):
    """由接口返回的 linkurl 解析出静态详情页地址（该页含公告正文）。"""
    linkurl = str(rec.get("linkurl") or "").strip()
    if not linkurl:
        return ""
    if linkurl.startswith("http://") or linkurl.startswith("https://"):
        return linkurl
    if linkurl.startswith("/"):
        return config.DETAIL_URL_HOST + linkurl
    return config.DETAIL_URL_HOST + "/" + linkurl


def normalize(rec, day=None):
    """把接口原始记录转成入库结构；无法识别行业/环节的返回 None。"""
    categorynum = str(rec.get("categorynum") or "")
    stage_code = categorynum[-3:]
    industry_code = categorynum[6:9]
    if not categorynum.startswith(config.CATEGORY_PREFIX):
        return None
    if stage_code not in config.STAGE_MAP or industry_code not in config.INDUSTRY_MAP:
        return None
    infoid = str(rec.get("infoid") or "").strip()
    title = str(rec.get("title") or rec.get("customtitle") or "").strip()
    if not infoid or not title:
        return None
    stage = config.STAGE_MAP[stage_code]
    industry = config.INDUSTRY_MAP[industry_code]
    reasons = []
    
    # 1. 重点跟踪项目（模糊匹配标题）
    proj_hits = [p for p in getattr(config, "FOCUS_PROJECTS", []) if config.fuzzy_match(p, title)]
    if proj_hits:
        reasons.extend(["命中重点项目: %s" % p for p in proj_hits])

    # 2. 重点业主（模糊匹配标题）
    owner_hits = [o for o in getattr(config, "FOCUS_OWNERS", []) if config.fuzzy_match(o, title)]
    if owner_hits:
        reasons.extend(["命中重点业主: %s" % o for o in owner_hits])
        
    # 3. 重点跟踪项目类型（匹配工程类型/行业）
    type_hits = [t for t in getattr(config, "FOCUS_PROJECT_TYPES", []) if t in industry or config.fuzzy_match(t, title)]
    if type_hits:
        reasons.extend(["命中重点类型: %s" % t for t in type_hits])
        
    # 4. 重点预警关键词
    kw_hits = [k for k in config.FOCUS_KEYWORDS if k in title]
    if kw_hits:
        reasons.extend(["命中关键词: %s" % k for k in kw_hits])

    is_focus = 1 if (proj_hits or owner_hits or type_hits or kw_hits) else 0

    return {
        "infoid": infoid,
        "title": title,
        "categorynum": categorynum,
        "industry": industry,
        "stage": stage,
        "stage_key": config.STAGE_KEY_MAP[stage],
        "badge_class": config.BADGE_CLASS_MAP[stage],
        "areacode": str(rec.get("areacode") or ""),
        "areaname": str(rec.get("areaname") or "").strip(),
        "pub_time": str(rec.get("infodatepx") or "").strip(),
        "link": config.DETAIL_URL_TPL.format(infoid=infoid, categorynum=categorynum),
        "detail_url": detail_url_of(rec),
        "is_focus": is_focus,
        "focus_reason": reasons,
        "owner": "",
    }


# ------------------------------------------------------------------ 接口
def build_payload(center, day, pn, rn):
    """构造列表接口请求体：服务端前缀过滤 + 服务端时间窗 + offset 分页。"""
    condition = [{
        "fieldName": "categorynum",
        "equal": config.CATEGORY_PREFIX,
        "notEqual": None,
        "equalList": None,
        "notEqualList": None,
        "isLike": True,
        "likeType": config.CATEGORY_LIKE_TYPE,
    }]
    window = []
    if config.API_DAY_WINDOW:
        window = [{
            "fieldName": "infodatepx",
            "startTime": "%s 00:00:00" % day,
            "endTime": "%s 23:59:59" % day,
        }]
    return {
        "token": "", "pn": pn, "rn": rn,
        "sdt": "", "edt": "", "wd": "", "inc_wd": "", "exc_wd": "",
        "fields": "title", "cnum": center,
        "sort": json.dumps({"infodatepx": "0"}), "ssort": "title", "cl": 200,
        "terminal": "", "condition": condition, "time": window, "highlights": "",
        "statistics": None, "unionCondition": [], "accuracy": "",
        "noParticiple": "0", "searchRange": None, "isBusiness": "1",
    }


def fetch_center(center, day, page_size=None):
    """按 offset 分页拉完一个中心当日的全部公告。

    返回 dict：{"center", "totalcount", "pages", "fetched", "records"(原始记录), "errors", "partial"}
    - 终止条件：取满 totalcount / 本页返回不足一页 / 返回空页 / 触及 API_MAX_PAGES 保护
    - 失败：HttpError 记入 errors 并中断该中心；CircuitOpen 直接向上抛（整体熔断）
    """
    rn = int(page_size or config.API_PAGE_SIZE)
    stats = {"center": center, "totalcount": 0, "pages": 0, "fetched": 0,
             "records": [], "page_log": [], "errors": [], "partial": False}
    pn = 0
    while True:
        payload = build_payload(center, day, pn, rn)
        try:
            resp = fetcher.post_json(config.API_URL, payload, headers=dict(JSON_HEADERS))
        except fetcher.HttpError as exc:
            stats["errors"].append(str(exc))
            break
        result = resp.get("result") or {}
        totalcount = int(result.get("totalcount") or 0)
        if stats["pages"] == 0:
            stats["totalcount"] = totalcount
        recs = result.get("records") or []
        stats["pages"] += 1
        stats["fetched"] += len(recs)
        stats["records"].extend(recs)
        stats["page_log"].append({"pn": pn, "rn": rn, "returned": len(recs), "totalcount": totalcount})
        if not recs:
            break
        pn += rn
        if len(recs) < rn:                       # 最后一页
            break
        if stats["fetched"] >= stats["totalcount"]:
            break
        if stats["pages"] >= config.API_MAX_PAGES:
            stats["partial"] = True
            stats["errors"].append("已达单中心翻页上限 API_MAX_PAGES=%d，可能未取满" % config.API_MAX_PAGES)
            break
    return stats


# ------------------------------------------------------------------ 主流程
def collect(day, centers=None, save_raw=True, quiet=False, page_size=None):
    config.ensure_dirs()
    started_at = config.now_stamp()
    centers = centers or config.CENTERS
    raw_dir = config.RAW_DIR / day
    if save_raw:
        raw_dir.mkdir(parents=True, exist_ok=True)

    merged, failed, reconcile_centers = {}, [], {}
    fetched_sum = raw_records = off_day = dup_in_center = 0

    for center in centers:
        try:
            stat = fetch_center(center, day, page_size=page_size)
        except fetcher.CircuitOpen as exc:
            stat = {"center": center, "totalcount": 0, "pages": 0, "fetched": 0, "records": [],
                    "page_log": [], "errors": ["%s" % exc], "partial": True}
            failed.append(center)
            reconcile_centers[center] = _center_reconcile(stat, 0, 0, 0)
            if not quiet:
                print("[warn] %s" % exc)
            break                                  # 熔断：后续中心不再请求

        if stat["errors"] or stat["fetched"] != stat["totalcount"]:
            failed.append(center)

        if save_raw:
            (raw_dir / ("center-%s.json" % center)).write_text(
                json.dumps({"center": center, "date": day, "totalcount": stat["totalcount"],
                            "pages": stat["pages"], "fetched": stat["fetched"],
                            "page_log": stat["page_log"], "errors": stat["errors"],
                            "records": stat["records"]}, ensure_ascii=False),
                encoding="utf-8")

        seen = set()
        kept = 0
        off_center = 0
        fetched_sum += stat["fetched"]
        raw_records += len(stat["records"])
        for rec in stat["records"]:
            infoid = str(rec.get("infoid") or "")
            if infoid in seen:
                dup_in_center += 1
                continue
            seen.add(infoid)
            if day and not str(rec.get("infodatepx") or "").startswith(day):
                off_day += 1
                off_center += 1
                continue
            row = normalize(rec, day)
            if row is None:
                continue
            merged.setdefault(row["infoid"], row)
            kept += 1
        detail = _center_reconcile(stat, kept, len(seen), len(stat["records"]) - len(seen))
        detail["off_day"] = off_center
        reconcile_centers[center] = detail

    rows = sorted(merged.values(), key=lambda r: (r["pub_time"], r["infoid"]))
    target = config.COLLECT_DIR / ("%s.json" % day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    totalcount_sum = sum(int(c.get("totalcount") or 0) for c in reconcile_centers.values())
    mismatch = [c for c, v in reconcile_centers.items() if not v["aligned"]]
    reconcile = {
        "date": day,
        "prefix": config.CATEGORY_PREFIX,
        "like_type": config.CATEGORY_LIKE_TYPE,
        "window": {"field": "infodatepx", "start": "%s 00:00:00" % day,
                   "end": "%s 23:59:59" % day} if config.API_DAY_WINDOW else None,
        "page_size": int(page_size or config.API_PAGE_SIZE),
        "centers_requested": len(centers),
        "centers": reconcile_centers,
        "totalcount_sum": totalcount_sum,
        "fetched_sum": fetched_sum,
        "fetched_minus_totalcount": fetched_sum - totalcount_sum,
        "raw_records": raw_records,
        "off_day_records": off_day,
        "duplicate_records": dup_in_center,
        "unique_rows": len(rows),
        "failed_centers": failed,
        "mismatch_centers": mismatch,
        "partial_centers": [c for c, v in reconcile_centers.items() if v["partial"]],
        "aligned": (not mismatch) and (not failed),
        "checked_at": config.now_stamp(),
    }
    report = config.REPORT_DIR / ("collect-reconcile-%s.json" % day)
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(reconcile, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass

    fresh = logstore.unseen([r["infoid"] for r in rows])
    _record_run(day, started_at, reconcile, fetched_sum, len(failed), len(rows))
    if not quiet:
        print("采集日期        :", day)
        print("过滤口径        : categorynum 前缀 %s (likeType=%s) + 发布时间窗 %s ~ %s" % (
            config.CATEGORY_PREFIX, config.CATEGORY_LIKE_TYPE,
            "%s 00:00:00" % day if config.API_DAY_WINDOW else "不限",
            "%s 23:59:59" % day if config.API_DAY_WINDOW else "不限"))
        print("交易中心        : %d 个（成功 %d / 失败 %d）" % (
            len(centers), len(centers) - len(failed), len(failed)))
        print("%-8s %-10s %-9s %-6s %-8s %-6s %s" % ("中心", "totalcount", "已拉取", "页数", "入库", "重复", "对账"))
        for c in centers:
            v = reconcile_centers.get(c)
            if not v:
                print("%-8s %-10s %-9s %-6s %-8s %-6s %s" % (c, "-", "-", "-", "-", "-", "未请求(熔断中断)"))
                continue
            print("%-8s %-10s %-9s %-6s %-8s %-6s %s" % (
                c, v["totalcount"], v["fetched"], v["pages"], v["kept"], v["duplicate"],
                "一致" if v["aligned"] else "不一致(缺 %d)" % max(0, v["totalcount"] - v["fetched"])))
        print("对账合计        : totalcount %d | 已拉取 %d | 差 %+d | 去重入库 %d 条 | 首次出现 %d 条" % (
            totalcount_sum, fetched_sum, fetched_sum - totalcount_sum, len(rows), len(fresh)))
        if failed:
            print("异常中心        :", ",".join(failed))
        print("产出            :", target)
        print("对账报告        :", report)

    result = {"date": day, "total": len(rows), "fresh": len(fresh),
              "centers_ok": len([c for c in centers if c in reconcile_centers and not reconcile_centers[c]["errors"]]),
              "centers_failed": failed, "per_center": {c: v["kept"] for c, v in reconcile_centers.items()},
              "reconcile": reconcile, "reconcile_path": str(report), "path": str(target)}
    if len(failed) == len(centers):
        return result, 2
    return result, (3 if failed else 0)


def _record_run(day, started_at, reconcile, fetched_sum, failed_centers, unique_rows):
    """把本次采集写进 runs 表（监控用）。DB 不可用时静默跳过，绝不影响采集主流程。"""
    try:
        import store
        conn = store.connect()
        store.init_db(conn)
        store.record_run(conn, day, "collect", {
            "started_at": started_at, "finished_at": config.now_stamp(),
            "total": int(reconcile.get("totalcount_sum") or 0), "ok": int(fetched_sum),
            "failed": int(failed_centers), "skipped": int(reconcile.get("duplicate_records") or 0),
            "aborted": bool(reconcile.get("partial_centers")),
            "unique_rows": unique_rows, "aligned": reconcile.get("aligned")})
        conn.close()
    except Exception:                                              # noqa: BLE001
        pass


def _center_reconcile(stat, kept, unique, duplicate):
    """单中心对账明细：totalcount 是服务端口径，fetched 是实际拉取条数。"""
    totalcount = int(stat.get("totalcount") or 0)
    fetched = int(stat.get("fetched") or 0)
    errors = list(stat.get("errors") or [])
    return {
        "totalcount": totalcount,
        "fetched": fetched,
        "pages": int(stat.get("pages") or 0),
        "kept": kept,
        "unique": unique,
        "duplicate": duplicate,
        "off_day": 0,
        "partial": bool(stat.get("partial")),
        "errors": errors,
        "aligned": (not errors) and fetched == totalcount and not stat.get("partial"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="采集当日工程建设类公告（前缀过滤 + offset 分页 + 对账）")
    ap.add_argument("--date", default=None, help="采集日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--centers", default=None, help="交易中心代码，逗号分隔，默认取配置")
    ap.add_argument("--page-size", type=int, default=None, help="每页条数，默认 API_PAGE_SIZE")
    ap.add_argument("--no-raw", action="store_true", help="不保存原始响应")
    args = ap.parse_args(argv)
    day = args.date or config.today()
    centers = [c.strip() for c in args.centers.split(",")] if args.centers else None
    result, code = collect(day, centers, save_raw=not args.no_raw, page_size=args.page_size)
    summary = {k: v for k, v in result.items() if k != "reconcile"}
    summary["reconcile_ok"] = result["reconcile"]["aligned"]
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
