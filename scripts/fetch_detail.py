# -*- coding: utf-8 -*-
"""抓取公告正文（详情页 → 清洗后的正文字符串）。

用法:
    python scripts/fetch_detail.py --date 2026-09-10              # 抓当日全部条目（缺则抓，已有缓存跳过）
    python scripts/fetch_detail.py --date 2026-09-10 --limit 20   # 只抓前 20 条（试跑）
    python scripts/fetch_detail.py --date 2026-09-10 --infoid <infoid> [...]
    python scripts/fetch_detail.py --date 2026-09-10 --force      # 忽略缓存重抓
    python scripts/fetch_detail.py --date 2026-09-10 --source daily

要点:
- 详情页地址优先取接口返回的 `linkurl`（静态 html，正文服务端渲染）；缺失时回退 `DETAIL_URL_TPL`
  （前端路由页，通常无正文，会记为 `parse_mode=fallback` 并可能 `status=empty`）。
- 正文容器 = `<div class="ewb-details-info">`，用 div 配平算法截取，再走 HTML→纯文本清洗；
  容器缺失时退化为整页清洗（同时剔除站点模板噪音），保证不因改版直接崩溃。
- 所有请求走 scripts/fetcher.py：同 Host ≥3 秒、403/429 熔断 15 分钟、5xx 指数退避、404 不重试。
- 增量落盘：每抓一条就写一个 JSON 缓存，中断后可续跑；`_progress.json` 供外部轮询进度。

产物:
    <DATA_DIR>/details/YYYY-MM-DD/<infoid>.json    单条公告正文（供 extract.py 使用）
    <DATA_DIR>/details/YYYY-MM-DD/_progress.json   进度（done/total/ok/empty/failed）
    <DATA_DIR>/details/YYYY-MM-DD/<infoid>.html    仅 --save-html 时保留原始页面（审计用）

退出码:
    0 正常；1 全部失败；2 触发熔断中断（冷却期内不应再请求）；4 没有待抓条目。
"""
import argparse
import json
import re
import sys
from html import unescape
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config    # noqa: E402
import fetcher   # noqa: E402

# 站点模板噪音（整页清洗时剔除；正文容器内的内容一般不含这些）
NOISE_PATTERNS = [
    r"您的浏览器禁用了脚本.*?体验！",
    r"【信息时间：[^】]*】",
    r"【阅读次数：[^】]*】",
    r"【字号\s*大\s*中\s*小\s*】",
    r"【打印】|【关闭】|【收藏】",
    r"首页\s*&gt;\s*交易信息.*?(?=\n|$)",
    r"上一篇[:：].*?(?=\n|$)",
    r"下一篇[:：].*?(?=\n|$)",
    r"相关链接[:：].*?(?=\n|$)",
]
ATTACH_EXT = ("pdf", "doc", "docx", "xls", "xlsx", "zip", "rar", "7z", "jpg", "jpeg", "png", "ofd")
ATTACH_EXT_SUFFIXES = tuple("." + e for e in ATTACH_EXT)
ATTACH_HINT = ("download", "attach", "file", "附件", "文件下载")


# ------------------------------------------------------------------ HTML 清洗
def _extract_class_block(html, cls):
    """按 div 配平截取 `<div class="...cls...">` 内部 HTML（cls 用 \\b 精确匹配）。"""
    m = re.search(r'<div[^>]*class="[^"]*\b%s\b[^"]*"[^>]*>' % re.escape(cls), html, re.I)
    if not m:
        return ""
    start = m.end()
    depth = 1
    for tok in re.finditer(r"<div\b[^>]*>|</div\s*>", html[start:], re.I):
        if tok.group(0).lower().startswith("</"):
            depth -= 1
            if depth == 0:
                return html[start:start + tok.start()]
        else:
            depth += 1
    return html[start:]


def html_to_text(frag):
    """HTML 片段 → 纯文本：保留段落/表格换行，压缩空白，去掉标签与实体。"""
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", frag, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</t[dh]\s*>", "\t", text, flags=re.I)
    text = re.sub(r"</(p|div|tr|li|h[1-6]|table|tbody|ul|ol)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ").replace("\u3000", " ")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _strip_noise(text):
    for pat in NOISE_PATTERNS:
        text = re.sub(pat, " ", text, flags=re.I | re.M)
    lines = [ln for ln in (l.strip() for l in text.splitlines()) if ln]
    return "\n".join(lines).strip()


def _absolute(base_host, href):
    href = (href or "").strip()
    if not href or href.lower().startswith(("javascript:", "#", "mailto:")):
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "http:" + href
    if href.startswith("/"):
        return base_host + href
    return base_host + "/" + href


def parse_detail(html, infoid="", url=""):
    """从详情页 HTML 解析标题 / 发布时间 / 正文 / 附件。返回 dict。"""
    title = ""
    m = re.search(r'<div[^>]*class="[^"]*\b%s\b[^"]*"[^>]*>(.*?)</div>' % re.escape(config.DETAIL_TITLE_CLASS),
                  html, re.S | re.I)
    if m:
        title = html_to_text(m.group(1))[:200]
    if not title:
        m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        title = html_to_text(m.group(1))[:200] if m else ""

    pub_time = ""
    m = re.search(r"【信息时间[:：]\s*([0-9]{4}[-/年][0-9]{1,2}[-/月][0-9]{1,2}[^】]*?)】", html)
    if m:
        pub_time = m.group(1).strip()

    body_html = _extract_class_block(html, config.DETAIL_BODY_CLASS)
    parse_mode = "container"
    if not body_html.strip():
        body_html = html
        parse_mode = "fallback"
    body = _strip_noise(html_to_text(body_html))

    host = config.DETAIL_URL_HOST
    attachments = []
    for href, label in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        low = href.lower()
        if low.endswith(ATTACH_EXT_SUFFIXES) or any(h in low for h in ATTACH_HINT):
            name = html_to_text(label)[:80] or href.rsplit("/", 1)[-1]
            full = _absolute(host, href)
            if full and not any(a["url"] == full for a in attachments):
                attachments.append({"name": name, "url": full})
    return {"infoid": infoid, "url": url, "title": title, "pub_time": pub_time,
            "body": body, "body_len": len(body), "attachments": attachments[:20],
            "parse_mode": parse_mode}


# ------------------------------------------------------------------ 读取入口
def load_details(day):
    """读取某日已抓取的正文缓存，返回 {infoid: record}。"""
    out = {}
    folder = config.DETAIL_DIR / day
    if not folder.exists():
        return out
    for p in sorted(folder.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if rec.get("infoid"):
            out[rec["infoid"]] = rec
    return out


def load_detail(day, infoid):
    p = config.DETAIL_DIR / day / ("%s.json" % infoid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_rows(day, source):
    if source == "daily":
        p = config.DAILY_DIR / ("%s.json" % day)
    else:
        p = config.COLLECT_DIR / ("%s.json" % day)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _detail_url_of(row):
    url = str(row.get("detail_url") or "").strip()
    if url:
        return url
    return config.DETAIL_URL_TPL.format(infoid=row.get("infoid"), categorynum=row.get("categorynum"))


# ------------------------------------------------------------------ 主流程
def fetch_details(day, rows=None, source="collect", limit=None, infoids=None, force=False,
                  save_html=False, quiet=False):
    config.ensure_dirs()
    rows = rows if rows is not None else _load_rows(day, source)
    if infoids:
        wanted = set(infoids)
        rows = [r for r in rows if str(r.get("infoid")) in wanted]
        missing = wanted - {str(r.get("infoid")) for r in rows}
        if missing and not quiet:
            print("[warn] 以下 infoid 不在 %s 的当日清单中：%s" % (source, ",".join(sorted(missing))))
    rows = [r for r in rows if r.get("infoid")]
    if limit:
        rows = rows[:int(limit)]

    folder = config.DETAIL_DIR / day
    folder.mkdir(parents=True, exist_ok=True)
    progress_path = folder / "_progress.json"

    stats = {"date": day, "source": source, "total": len(rows), "done": 0, "fetched": 0,
             "cached": 0, "ok": 0, "empty": 0, "failed": 0, "aborted": False,
             "errors": [], "started_at": config.now_stamp()}

    def write_progress():
        payload = dict(stats)
        payload["updated_at"] = config.now_stamp()
        try:
            progress_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    write_progress()
    for idx, row in enumerate(rows, 1):
        infoid = str(row["infoid"])
        target = folder / ("%s.json" % infoid)
        if target.exists() and not force:
            stats["cached"] += 1
            stats["done"] += 1
            if not quiet:
                print("  [%d/%d] 缓存命中 %s" % (idx, len(rows), infoid[:8]))
            continue
        url = _detail_url_of(row)
        try:
            if "bbw_prod/bbw_notice/notice_detail" in url:
                raw_json = fetcher.get_text(url, headers={"User-Agent": "Mozilla/5.0"})
                jdata = json.loads(raw_json)
                notices = (jdata.get("data") or {}).get("notices") or []
                html_url = None
                if notices and notices[0].get("htmlUrl"):
                    html_url = notices[0]["htmlUrl"]
                if html_url:
                    html = fetcher.get_text(html_url, headers={"User-Agent": "Mozilla/5.0"})
                else:
                    html = raw_json
            else:
                html = fetcher.get_text(url, headers={"Referer": config.API_REFERER})
        except fetcher.HttpError as exc:
            stats["failed"] += 1
            stats["done"] += 1
            stats["errors"].append({"infoid": infoid, "url": url, "error": str(exc)})
            if not quiet:
                print("  [%d/%d] 失败 %s -> %s" % (idx, len(rows), infoid[:8], exc))
            write_progress()
            if exc.status in (403, 429):
                stats["aborted"] = True
                break
            continue
        except fetcher.CircuitOpen as exc:
            stats["aborted"] = True
            stats["errors"].append({"infoid": infoid, "url": url, "error": str(exc)})
            if not quiet:
                print("  [%d/%d] %s" % (idx, len(rows), exc))
            break

        rec = parse_detail(html, infoid=infoid, url=url)
        rec["source_row"] = {k: row.get(k) for k in ("title", "stage", "stage_key", "industry",
                                                    "areaname", "pub_time", "categorynum")}
        rec["fetched_at"] = config.now_stamp()
        rec["http_len"] = len(html)
        rec["status"] = "ok" if rec["body_len"] >= 20 else "empty"
        if rec["status"] == "ok":
            stats["ok"] += 1
        else:
            stats["empty"] += 1
        try:
            target.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            if save_html:
                (folder / ("%s.html" % infoid)).write_text(html, encoding="utf-8")
        except OSError as exc:
            stats["failed"] += 1
            stats["errors"].append({"infoid": infoid, "url": url, "error": "写盘失败: %s" % exc})
        stats["fetched"] += 1
        stats["done"] += 1
        if not quiet:
            print("  [%d/%d] %s body=%d 字 mode=%s" % (
                idx, len(rows), infoid[:8], rec["body_len"], rec["parse_mode"]))
        write_progress()

    stats["aborted"] = bool(stats["aborted"])
    stats["finished_at"] = config.now_stamp()
    write_progress()
    _record_run(day, stats)
    return stats


def _record_run(day, stats):
    """把本次抓正文写进 runs 表（监控用）；DB 不可用时静默跳过。"""
    try:
        import store
        conn = store.connect()
        store.init_db(conn)
        payload = {k: v for k, v in stats.items() if k != "errors"}
        payload["error_count"] = len(stats.get("errors") or [])
        store.record_run(conn, day, "fetch_detail", payload)
        conn.close()
    except Exception:                                              # noqa: BLE001
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓取公告正文（详情页 → 清洗文本）")
    ap.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--source", choices=["collect", "daily"], default="collect",
                    help="从哪个清单取待抓条目（默认 collect 全量采集）")
    ap.add_argument("--limit", type=int, default=None, help="最多抓多少条（试跑用）")
    ap.add_argument("--infoid", action="append", default=None, help="只抓指定 infoid，可重复")
    ap.add_argument("--force", action="store_true", help="忽略缓存重新抓取")
    ap.add_argument("--save-html", action="store_true", help="同时保留原始 HTML（审计用）")
    ap.add_argument("--quiet", action="store_true", help="不打印逐条进度")
    args = ap.parse_args(argv)
    day = args.date or config.today()
    stats = fetch_details(day, source=args.source, limit=args.limit, infoids=args.infoid,
                          force=args.force, save_html=args.save_html, quiet=args.quiet)
    print(json.dumps({k: v for k, v in stats.items() if k != "errors"} | {"error_count": len(stats["errors"])},
                     ensure_ascii=False))
    if stats["aborted"]:
        return 2
    if stats["total"] and stats["failed"] == stats["total"]:
        return 1
    if not stats["total"]:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
