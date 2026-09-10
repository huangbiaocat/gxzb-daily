# -*- coding: utf-8 -*-
"""采集当日「工程建设」类公告（15 个交易中心 → 去重 → 规范化）。

用法:
    python scripts/collect.py                      # 采集今天（按 .env 时区）
    python scripts/collect.py --date 2026-09-10    # 采集指定日期
    python scripts/collect.py --no-raw             # 不落原始响应（仅产出去重结果）

产物:
    <DATA_DIR>/raw/YYYY-MM-DD/center-001.json ...  原始响应（审计用）
    <DATA_DIR>/collect/YYYY-MM-DD.json             去重 + 规范化后的当日全量公告

退出码:
    0 全部中心成功；3 部分中心失败（结果可能不全）；2 全部失败。
无任何第三方依赖，可直接由 cron / launchd 调度。
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config      # noqa: E402
import logstore    # noqa: E402


# ------------------------------------------------------------------ 规范化
def normalize(rec):
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
    hits = [k for k in config.FOCUS_KEYWORDS if k in title]
    return {
        "infoid": infoid,
        "title": title,
        "categorynum": categorynum,
        "industry": config.INDUSTRY_MAP[industry_code],
        "stage": stage,
        "stage_key": config.STAGE_KEY_MAP[stage],
        "badge_class": config.BADGE_CLASS_MAP[stage],
        "areacode": str(rec.get("areacode") or ""),
        "areaname": str(rec.get("areaname") or "").strip(),
        "pub_time": str(rec.get("infodatepx") or "").strip(),
        "link": config.DETAIL_URL_TPL.format(infoid=infoid, categorynum=categorynum),
        "is_focus": 1 if hits else 0,
        "focus_reason": ["命中关键词: %s" % k for k in hits],
        "owner": "",
    }


# ------------------------------------------------------------------ 接口
def build_payload(center):
    return {
        "token": "", "pn": 0, "rn": config.API_PAGE_SIZE,
        "sdt": "", "edt": "", "wd": "", "inc_wd": "", "exc_wd": "",
        "fields": "title", "cnum": center,
        "sort": json.dumps({"infodatepx": "0"}), "ssort": "title", "cl": 200,
        "terminal": "", "condition": [], "time": [], "highlights": "",
        "statistics": None, "unionCondition": [], "accuracy": "",
        "noParticiple": "0", "searchRange": None, "isBusiness": "1",
    }


def fetch_center(center):
    """返回 (原始 JSON, 错误信息)。失败自动重试。"""
    body = json.dumps(build_payload(center)).encode("utf-8")
    last_err = ""
    for attempt in range(config.API_RETRY + 1):
        try:
            req = urllib.request.Request(API_URL, data=body, headers={
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": "Mozilla/5.0 (compatible; gxzb-daily/1.0)",
                "Referer": config.API_REFERER,
            })
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace")), ""
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_err = "%s: %s" % (type(exc).__name__, exc)
            if attempt < config.API_RETRY:
                time.sleep(2 * (attempt + 1))
    return None, last_err


API_URL = config.API_URL


# ------------------------------------------------------------------ 主流程
def collect(day, centers=None, save_raw=True, quiet=False):
    config.ensure_dirs()
    centers = centers or config.CENTERS
    raw_dir = config.RAW_DIR / day
    if save_raw:
        raw_dir.mkdir(parents=True, exist_ok=True)

    merged, failed, per_center = {}, [], {}
    for center in centers:
        payload, err = fetch_center(center)
        if payload is None:
            failed.append(center)
            per_center[center] = -1
            if not quiet:
                print("[warn] 交易中心 %s 采集失败：%s" % (center, err))
            continue
        if save_raw:
            (raw_dir / ("center-%s.json" % center)).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        kept = 0
        for rec in (payload.get("result") or {}).get("records") or []:
            if not str(rec.get("infodatepx") or "").startswith(day):
                continue
            row = normalize(rec)
            if row is None:
                continue
            merged.setdefault(row["infoid"], row)
            kept += 1
        per_center[center] = kept

    rows = sorted(merged.values(), key=lambda r: (r["pub_time"], r["infoid"]))
    target = config.COLLECT_DIR / ("%s.json" % day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    fresh = logstore.unseen([r["infoid"] for r in rows])
    if not quiet:
        print("采集日期        :", day)
        print("交易中心        : %d 个（成功 %d / 失败 %d）" % (len(centers), len(centers) - len(failed), len(failed)))
        if failed:
            print("失败中心        :", ",".join(failed))
        print("去重后条数      :", len(rows), "| 首次出现(跨日新公告):", len(fresh))
        print("产出            :", target)
    result = {"date": day, "total": len(rows), "fresh": len(fresh),
              "centers_ok": len(centers) - len(failed), "centers_failed": failed,
              "per_center": per_center, "path": str(target)}
    if failed and len(failed) == len(centers):
        return result, 2
    return result, (3 if failed else 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description="采集当日工程建设类公告")
    ap.add_argument("--date", default=None, help="采集日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--centers", default=None, help="交易中心代码，逗号分隔，默认取配置")
    ap.add_argument("--no-raw", action="store_true", help="不保存原始响应")
    args = ap.parse_args(argv)
    day = args.date or config.today()
    centers = [c.strip() for c in args.centers.split(",")] if args.centers else None
    result, code = collect(day, centers, save_raw=not args.no_raw)
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
