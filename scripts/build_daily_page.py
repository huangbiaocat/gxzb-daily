# -*- coding: utf-8 -*-
"""每日明细页生成脚本（视觉设计基准：templates/index-preview.html）。

输入：<DATA_DIR>/daily/YYYY-MM-DD.json      当日入库条目（主键 = 官方 infoid）
输出：<SITE_DIR>/YYYY-MM-DD.html            当日明细页
      <LOG_DIR>/YYYY-MM-DD.jsonl            运行日志（以 infoid 为键）
      <DATA_DIR>/state/index.json           全局 infoid 台账（跨日判重）

唯一识别码：直接使用官方接口返回的 infoid，不自造任何编号，页面也不展示编号；
            日志 / 台账 / 数据文件统一以 infoid 为键，用于重跑判重与失败条目重查。
链接口径：不信任数据源 url（历史存在被截断 / 旧路径失效），统一按 infoid + categorynum
          拼官方详情页地址。
样式口径：样式在构建时从设计基准内联提取，并对设计基准做 MD5 断言，防止视觉漂移。

用法：
    python scripts/build_daily_page.py                      # 生成今天（按 .env 时区）
    python scripts/build_daily_page.py --date 2026-09-10    # 生成指定日期
    python scripts/build_daily_page.py --refetch            # 列出待重取条目（status != collected）
退出码：0 成功；1 自检未通过（页面已写盘，需人工检查）。
"""
import argparse
import collections
import datetime
import hashlib
import html as html_mod
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config      # noqa: E402
import logstore    # noqa: E402
from overtime_helper import annotate_item_overtime  # noqa: E402
try:
    from scripts.delayed_helper import annotate_delayed_item
    from scripts.scan_record_db import get_scan_record_db
except ImportError:
    from delayed_helper import annotate_delayed_item
    from scan_record_db import get_scan_record_db


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="生成每日明细页")
    ap.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--refetch", action="store_true", help="只列出日志中待重取的条目")
    ap.add_argument("--no-vps", action="store_true", help="跳过自动同步上传 VPS")
    ap.add_argument("--final", action="store_true", help="标记为全天最终版封存页面")
    ap.add_argument("--scan-time", default="", help="指定本次扫描/封版时间戳 (YYYY-MM-DD HH:MM:SS)")
    return ap.parse_args(argv)


ARGS = parse_args()

SITE_DIR = config.SITE_DIR          # 页面产物根目录
OUTDIR = SITE_DIR
TMPDIR = config.DATA_DIR / "backup"  # 旧产物备份目录（不参与发布）
DAY = ARGS.date or config.today()
DAY_KEY = DAY.replace("-", "")
DATA = config.DAILY_DIR / (DAY + ".json")
OUT = OUTDIR / (DAY + ".html")
LOG = logstore.log_path(DAY)
LOG_DIR = config.LOG_DIR
PREVIEW = config.TEMPLATE_PREVIEW
PREVIEW_MD5 = config.PREVIEW_MD5
DETAIL_TPL = config.DETAIL_URL_TPL


def neighbor_url(offset_days):
    """前 / 后一日的页面地址（缺页时由站点 404 兜底）。"""
    d = datetime.date.fromisoformat(DAY) + datetime.timedelta(days=offset_days)
    return "%s/%s.html" % (config.SITE_BASE_URL, d.isoformat())


if ARGS.refetch:
    _pend = logstore.pending_refetch(DAY)
    print("日期            : %s | 待重取条目数: %d" % (DAY, len(_pend)))
    for _rec in _pend:
        print(json.dumps(_rec, ensure_ascii=False))
    if _pend:
        _out = config.STATE_DIR / ("refetch-%s.json" % DAY)
        _out.write_text(json.dumps([r["infoid"] for r in _pend], ensure_ascii=False, indent=2), encoding="utf-8")
        print("待重取 infoid 清单:", _out)
    raise SystemExit(0)

config.ensure_dirs()

# ------------------------------------------------------------------ 0. 设计基准校验
prev_raw = PREVIEW.read_text(encoding="utf-8")
assert hashlib.md5(prev_raw.encode("utf-8")).hexdigest() == PREVIEW_MD5, \
    "设计基准 index-preview.html 已变更，请重新核对样式提取"

styles = re.findall(r"<style>(.*?)</style>", prev_raw, flags=re.S)
assert len(styles) >= 2, "未能从设计基准中提取到样式块"
PREVIEW_CSS = "\n".join(s.strip() for s in styles)

if not DATA.exists():
    # 当日暂无入库数据时，自动补空数据文件以支持生成并刷新页面时间
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text("[]", encoding="utf-8")

items = json.loads(DATA.read_text(encoding="utf-8"))
for it in items:
    it["infoid"] = str(it.get("infoid") or it.get("id") or "")
    assert it["infoid"], "条目缺少官方唯一识别码 infoid，拒绝生成页面：%r" % it.get("title", "")[:40]
    link_candidate = it.get("link") or it.get("url") or ""
    s_raw = it.get("source") or ""
    if "阳光" in s_raw or it.get("areaname") == "崇左阳光采购" or "cz.gxygcg.com" in link_candidate:
        it["source"] = "崇左阳光采购"
        it["areaname"] = "崇左市"
    else:
        it["source"] = "广西公共资源交易平台"

    if it.get("source") == "崇左阳光采购" and ("gxygcg.com" in link_candidate):
        it["link"] = link_candidate
    elif it.get("source") == "崇左阳光采购":
        pid = it.get("purchaseProjectsIds") or it.get("purchase_projects_ids") or ""
        ntype = it.get("noticeType") or it.get("notice_type") or 1
        it["link"] = f"https://cz.gxygcg.com/purchase/detail/?purchase_projects_ids={pid}&notice_type={ntype}&notice_id={it['infoid']}"
    else:
        it["link"] = DETAIL_TPL.format(infoid=it["infoid"], categorynum=it["categorynum"])

    # 规范化行业分类（崇左阳光采购的“房建市政”对齐为“房建市政工程”）
    if it.get("industry") in ("房建市政", "房建市政工程"):
        it["industry"] = "房建市政工程"

    # 依据最新配置动态补充重点预警标记
    title = it.get("title", "")
    industry = it.get("industry", "")
    owner = it.get("owner", "")
    reasons = []
    focus_tags = []
    
    proj_hits = [p for p in getattr(config, "FOCUS_PROJECTS", []) if config.fuzzy_match(p, title)]
    if proj_hits:
        for p in proj_hits:
            msg = "命中重点项目: %s" % p
            if msg not in reasons:
                reasons.append(msg)
        if "重点项目" not in focus_tags:
            focus_tags.append("重点项目")
                
    owner_hits = [o for o in getattr(config, "FOCUS_OWNERS", []) if ((owner and config.fuzzy_match(o, owner)) or config.fuzzy_match(o, title))]
    if owner_hits:
        for o in owner_hits:
            msg = "命中重点业主: %s" % o
            if msg not in reasons:
                reasons.append(msg)
        if "重点业主" not in focus_tags:
            focus_tags.append("重点业主")
                
    type_hits = [t for t in getattr(config, "FOCUS_PROJECT_TYPES", []) if (t in industry or config.fuzzy_match(t, title))]
    if type_hits:
        for t in type_hits:
            msg = "命中重点类型: %s" % t
            if msg not in reasons:
                reasons.append(msg)
        if "重点类型" not in focus_tags:
            focus_tags.append("重点类型")
                
    raw_kw_hits = [k for k in getattr(config, "FOCUS_KEYWORDS", []) if k in title]
    kw_hits = []
    if raw_kw_hits:
        min_amount = getattr(config, "FOCUS_MIN_AMOUNT", 0.0) or 0.0
        if min_amount > 0:
            # 优先从标题提取，提取不到可从 notice_fields 查询已提取金额
            amt = config.extract_amount_from_title(title)
            if amt is None:
                infoid = it.get("infoid", "")
                if infoid and getattr(config, "DB_PATH", None) and config.DB_PATH.exists():
                    try:
                        import sqlite3
                        with sqlite3.connect(str(config.DB_PATH)) as conn:
                            c = conn.cursor()
                            c.execute("SELECT value_num FROM notice_fields WHERE infoid = ? AND value_type = 'money' AND value_num IS NOT NULL ORDER BY value_num DESC LIMIT 1", (infoid,))
                            r = c.fetchone()
                            if r and r[0] is not None:
                                amt = float(r[0])
                    except Exception:
                        pass
            if amt is not None and amt >= min_amount:
                from extractors.normalize import format_money
                kw_hits = raw_kw_hits
                for k in kw_hits:
                    msg = "命中关键词: %s (金额%s >= 门槛%s)" % (k, format_money(amt), format_money(min_amount))
                    if msg not in reasons:
                        reasons.append(msg)
                if "重点关键词" not in focus_tags:
                    focus_tags.append("重点关键词")
        else:
            kw_hits = raw_kw_hits
            for k in kw_hits:
                msg = "命中关键词: %s" % k
                if msg not in reasons:
                    reasons.append(msg)
            if "重点关键词" not in focus_tags:
                focus_tags.append("重点关键词")
                
    if proj_hits or owner_hits or type_hits or kw_hits:
        it["is_focus"] = 1
        it["focus_reason"] = reasons
        it["focus_tags"] = focus_tags
    else:
        it["is_focus"] = 0
        it["focus_reason"] = []
        it["focus_tags"] = []

    annotate_item_overtime(it)
    # 滞后公开判定：直接以首次扫描记录存证库（ScanRecordDB）为不可篡改的证据准绳
    rec = get_scan_record_db().get_record(it.get("infoid"))
    if rec and rec.get("is_delayed"):
        it["is_delayed"] = 1
        it["delay_days"] = rec.get("delay_days", 0)
        it["delayed_type"] = "滞后公开"
        it["first_seen_date"] = rec.get("first_scan_date")
        it["delayed_reason"] = rec.get("evidence_text")
        it["is_focus"] = 1
        tags = it.setdefault("focus_tags", [])
        tags = [t for t in tags if t != "滞后补录"]
        if "滞后公开" not in tags:
            tags.append("滞后公开")
        it["focus_tags"] = tags
        reasons = it.setdefault("focus_reason", [])
        ev = rec.get("evidence_text") or f"滞后 {it.get('delay_days', 0)} 天公开现身"
        if ev not in reasons:
            reasons.append(ev)
    elif rec:
        it["is_delayed"] = 0
        it["delay_days"] = 0
        it.pop("delayed_reason", None)
        it.pop("first_seen_date", None)
        tags = it.get("focus_tags")
        if isinstance(tags, list):
            it["focus_tags"] = [t for t in tags if t not in ("滞后补录", "滞后公开")]
            if not it["focus_tags"]:
                it["is_focus"] = 0
                it["focus_reason"] = []
    else:
        annotate_delayed_item(it, DAY)

# 回写清洗更新后的 items 到当日 JSON 文件，彻底消除历史脏数据残留
if DATA.exists():
    try:
        DATA.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _e:
        pass

# ------------------------------------------------------------------ 0.5 运行日志与 infoid 台账
# 官方接口返回的 infoid 即公告唯一识别码，直接作为入库判重 / 失败重查 / 跨日去重的主键。
# 历史自造编号（GX + 日期 + 序号）已废弃，页面不再展示任何编号。
# 日志：<LOG_DIR>/YYYY-MM-DD.jsonl（一行一条，主键 infoid，含 status / updated_at）
#      重跑时同一条公告刷新状态而不重复入库；status != collected 的条目可由
#      `--refetch` 或 logstore.pending_refetch() 列出后按 infoid 重取。
# 台账：<DATA_DIR>/state/index.json（infoid -> 首次/最近出现日期），用于跨日判重，
#      避免同一公告在次日被当成新公告重复入库 / 重复推送。
log_rows, log_pending = 0, []
log_index = logstore.load_index()


# ------------------------------------------------------------------ 1. 数据统计
stage_counter = collections.Counter(it["stage"] for it in items)
total_n = len(items)
focus_n = sum(1 for it in items if it.get("is_focus"))
# 加载今日回扫捕获的滞后清单（若有）
today_delayed_file = config.get_delayed_today_path(DAY)
today_delayed_items = []
if today_delayed_file.is_file():
    try:
        today_delayed_items = json.loads(today_delayed_file.read_text(encoding="utf-8"))
    except Exception:
        today_delayed_items = []
for dit in today_delayed_items:
    annotate_delayed_item(dit, DAY)

delayed_n = len(today_delayed_items) + sum(1 for it in items if it.get("is_delayed"))
stat_map = {
    "stat-plan": stage_counter.get("招标计划", 0),
    "stat-notice": stage_counter.get("招标公告", 0),
    "stat-clarify": stage_counter.get("澄清/答疑", 0),
    "stat-control": stage_counter.get("控制价公示", 0),
    "stat-candidate": stage_counter.get("中标公示", 0),
    "stat-result": stage_counter.get("中标公告", 0),
    "stat-focus": focus_n,
}
city_n = len(set(it.get("areaname", "") for it in items))
cat_n = len(set(it.get("industry", "") for it in items))
valid_times = [it.get("pub_time") for it in items if it.get("pub_time")]
latest_pub = max(valid_times) if valid_times else "暂无"

# ------------------------------------------------------------------ 状态与时间口径：
# 1. 最新公告时间 (latest_pub)：当天已收录数据中最新的发布时间；若无数据则为“暂无”
# 2. 最近扫描时间 (scan_time)：
#    - 命令行 --scan-time 显式指定时优先；
#    - 若为已终版封存的归档页面且未重新加 --final 跑，则保留封存时写入的 finalized_at；
#    - 否则（日常定时扫描即使无新数据、或执行 --final 终版封装时），均记录当前时间 config.now_stamp()
final_state_file = config.STATE_DIR / ("final-%s.json" % DAY)
is_final_page = bool(getattr(ARGS, "final", False) or final_state_file.exists())
scan_time = getattr(ARGS, "scan_time", "").strip()
if not scan_time and is_final_page and not getattr(ARGS, "final", False) and final_state_file.exists():
    try:
        fin_info = json.loads(final_state_file.read_text(encoding="utf-8"))
        scan_time = fin_info.get("finalized_at", "").strip()
    except Exception:
        pass
if not scan_time:
    scan_time = config.now_stamp()

# ------------------------------------------------------------------ 2. 每日页定制样式（preview 未覆盖的组件）
DAILY_CSS = """
        /* ===== 每日明细页定制（建立在 preview 设计系统之上） ===== */
        /* 业务环节与重点信息统计卡：沿用 preview .stat-box，自适应 7 列栅格 */
        .stats-grid.cols-7 { grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 12px; }
        .stats-grid.cols-6 { grid-template-columns: repeat(6, minmax(0, 1fr)); }
        .stats-grid.cols-7 .stat-box, .stats-grid.cols-6 .stat-box { padding: 13px 14px; }
        .stats-grid.cols-7 .stat-box .sb-value, .stats-grid.cols-6 .stat-box .sb-value { font-size: 1.45rem; }
        .stats-grid.cols-7 .stat-box.clickable, .stats-grid.cols-6 .stat-box.clickable {
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
            user-select: none;
        }
        .stats-grid.cols-7 .stat-box.clickable:hover, .stats-grid.cols-6 .stat-box.clickable:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 16px -4px rgba(15, 23, 42, 0.12);
        }
        .stats-grid.cols-7 .stat-box.clickable.active, .stats-grid.cols-6 .stat-box.clickable.active {
            box-shadow: 0 0 0 2px #0f172a, 0 6px 20px -4px rgba(15, 23, 42, 0.2);
            transform: translateY(-2px);
        }
        .stats-grid.cols-7 .stat-box.stage-focus.clickable.active {
            box-shadow: 0 0 0 2px #dc2626, 0 6px 20px -4px rgba(220, 38, 38, 0.25);
        }
        @media (max-width: 1200px) {
            .stats-grid.cols-7 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
            .stats-grid.cols-6 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
        }
        @media (max-width: 760px) {
            .stats-grid.cols-7, .stats-grid.cols-6 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        @media (max-width: 520px) {
            .stats-grid.cols-7, .stats-grid.cols-6 { grid-template-columns: 1fr; }
        }

        /* 页头胶囊导航组 */
        .nav-caps { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .nav-caps .btn-back, .nav-caps .btn-latest { padding: 7px 14px; font-size: 0.84rem; }
        .nav-caps .btn-back svg { opacity: 0.8; }

        /* 检索区：上行搜索 + 3 个筛选下拉，下行重点预警 / 重置 / 匹配统计 */
        .filter-section.daily { flex-direction: column; align-items: stretch; gap: 14px; }
        .filter-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
        .filter-row .search-input-wrap { flex: 1 1 300px; max-width: 420px; }
        .select-input {
            padding: 9px 34px 9px 12px;
            font-size: 0.88rem;
            font-weight: 500;
            color: var(--text-secondary);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            background-color: #f8fafc;
            appearance: none;
            -webkit-appearance: none;
            cursor: pointer;
            outline: none;
            transition: all 0.2s ease;
            background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>");
            background-repeat: no-repeat;
            background-position: right 11px center;
        }
        .select-input:hover { background-color: #f1f5f9; }
        .select-input:focus { background-color: #ffffff; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(2, 132, 199, 0.12); }
        .filter-row.bottom { justify-content: space-between; border-top: 1px solid var(--border-light); padding-top: 12px; }
        .filter-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .btn-focus, .btn-overtime, .btn-delayed, .btn-reset {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 7px 14px;
            border-radius: 9999px;
            font-size: 0.82rem;
            font-weight: 600;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .btn-focus { color: var(--danger-text); background: var(--danger-light); border: 1px solid #fecaca; }
        .btn-focus:hover { background: #fee2e2; }
        .btn-focus.active {
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
            color: #ffffff;
            border-color: transparent;
            box-shadow: 0 4px 12px rgba(239, 68, 68, 0.25);
        }
        .btn-overtime { color: var(--text-secondary); background: #f8fafc; border: 1px solid #e2e8f0; }
        .btn-overtime:hover { background: #f1f5f9; color: var(--text-primary); }
        .btn-overtime.active {
            background: #475569;
            color: #ffffff;
            border-color: transparent;
            box-shadow: 0 2px 6px rgba(71, 85, 105, 0.2);
        }
        .btn-delayed {
            color: #c2410c;
            background: #fff7ed;
            border: 1px solid #fed7aa;
        }
        .btn-delayed:hover {
            background: #ffedd5;
            color: #9a3412;
        }
        .btn-delayed.active {
            background: linear-gradient(135deg, #ea580c 0%, #c2410c 100%);
            color: #ffffff;
            border-color: transparent;
            box-shadow: 0 4px 12px rgba(234, 88, 12, 0.25);
        }
        .btn-delayed .delayed-count { font-weight: 800; }

        .delayed-chip {
            flex-shrink: 0;
            font-size: 0.7rem;
            font-weight: 700;
            line-height: 1.6;
            padding: 2px 9px;
            border-radius: 9999px;
            margin-top: 1px;
            letter-spacing: 0.02em;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            background: #fff7ed;
            color: #c2410c;
            border: 1px solid #fed7aa;
            cursor: default;
            box-shadow: 0 1px 2px rgba(194, 65, 12, 0.08);
            transition: all 0.15s ease;
        }
        .delayed-chip:hover {
            background: #ffedd5;
            color: #9a3412;
            border-color: #fdba74;
        }
        .focus-chip-delayed {
            background: #fff7ed;
            color: #c2410c;
            border: 1px solid #fed7aa;
            border-radius: 9999px !important;
        }

        .delayed-alert-box {
            background: linear-gradient(180deg, #fffaf5 0%, #fff7ed 100%);
            border: 1px solid #fed7aa;
            border-left: 4px solid #ea580c;
            border-radius: 8px;
            padding: 14px 18px;
            margin-bottom: 20px;
            box-shadow: 0 1px 4px rgba(234, 88, 12, 0.08);
        }
        .delayed-alert-header {
            margin-bottom: 10px;
        }
        .delayed-alert-title-row {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.95rem;
            font-weight: 700;
            color: #9a3412;
        }
        .delayed-alert-badge {
            display: inline-flex;
            align-items: center;
            background: #ea580c;
            color: #ffffff;
            font-size: 0.72rem;
            font-weight: 800;
            padding: 2px 8px;
            border-radius: 4px;
            letter-spacing: 0.04em;
        }
        .delayed-alert-desc {
            font-size: 0.8rem;
            color: #c2410c;
            margin-top: 3px;
        }
        .delayed-alert-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .delayed-item-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #ffffff;
            border: 1px solid #fed7aa;
            border-radius: 6px;
            padding: 9px 12px;
            gap: 12px;
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
            text-decoration: none;
            color: inherit;
        }
        .delayed-item-row:hover {
            border-color: #ea580c;
            box-shadow: 0 2px 6px rgba(234, 88, 12, 0.12);
        }
        .delayed-item-main {
            display: flex;
            align-items: center;
            gap: 10px;
            min-width: 0;
            flex: 1;
        }
        .delayed-days-tag {
            background: #fff1f2;
            color: #e11d48;
            border: 1px solid #fecdd3;
            font-size: 0.72rem;
            font-weight: 800;
            padding: 2px 8px;
            border-radius: 4px;
            white-space: nowrap;
            flex-shrink: 0;
        }
        .delayed-item-title {
            font-size: 0.88rem;
            font-weight: 600;
            color: #1e293b;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .delayed-item-meta {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 0.75rem;
            color: #64748b;
            white-space: nowrap;
            flex-shrink: 0;
        }
        .btn-toggle-delayed-more {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            width: 100%;
            padding: 8px 12px;
            margin-top: 6px;
            background: #ffffff;
            border: 1px dashed #fed7aa;
            border-radius: 6px;
            color: #ea580c;
            font-size: 0.8rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .btn-toggle-delayed-more:hover {
            background: #fff7ed;
            border-color: #ea580c;
            color: #c2410c;
        }
        .btn-overtime .overtime-count { font-weight: 800; }
        .btn-reset { color: var(--text-secondary); background: #f1f5f9; border: 1px solid transparent; }
        .btn-reset:hover { background: #e2e8f0; color: var(--text-primary); }
        .btn-focus .focus-count { font-weight: 800; }

        /* 重点预警条目角标 */
        .focus-chip {
            flex-shrink: 0;
            font-size: 0.7rem;
            font-weight: 700;
            line-height: 1.6;
            padding: 1px 8px;
            border-radius: 9999px;
            background: var(--danger-light);
            color: var(--danger-text);
            border: 1px solid #fecaca;
            margin-top: 1px;
            letter-spacing: 0.02em;
            display: inline-flex;
            align-items: center;
        }
        .focus-chip-project {
            background: #fef2f2;
            color: #b91c1c;
            border: 1px solid #fecaca;
        }
        .focus-chip-owner {
            background: #fffbeb;
            color: #b45309;
            border: 1px solid #fde68a;
        }
        .focus-chip-keyword {
            background: #f5f3ff;
            color: #6d28d9;
            border: 1px solid #ddd6fe;
        }
        .focus-chip-type {
            background: #eff6ff;
            color: #1d4ed8;
            border: 1px solid #bfdbfe;
        }
        .overtime-chip {
            flex-shrink: 0;
            font-size: 0.7rem;
            font-weight: 600;
            line-height: 1.6;
            padding: 1px 8px;
            border-radius: 9999px;
            margin-top: 1px;
            letter-spacing: 0.02em;
            display: inline-flex;
            align-items: center;
            gap: 3px;
            background: #f1f5f9;
            color: #475569;
            border: 1px solid #e2e8f0;
            cursor: default;
            transition: all 0.15s ease;
        }
        .overtime-chip:hover {
            background: #e2e8f0;
            border-color: #cbd5e1;
        }

        @media (max-width: 640px) {
            .filter-row.bottom { flex-direction: column; align-items: flex-start; }
            .nav-caps { width: 100%; justify-content: flex-start; }
        }

        /* ===== 主题11：工程类别标题栏 / 公告类型胶囊标签 / 地市标签（对齐参考图） ===== */
        /* ① 工程类别块头：白底扁平卡片 + 深蓝圆角矩形图标（白色图案） + 黑色粗体标题 */
        .cat-block { box-shadow: none; }
        .cat-block .cat-icon {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
            color: #ffffff;
            border-radius: 10px;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.10);
        }
        .cat-block .cat-icon svg { stroke-width: 2.3; }
        .cat-block .cat-heading { color: #0f172a; font-weight: 800; }

        /* ② 公告类型标签：胶囊形，浅色底 + 同色系深色文字，数量用浅一档同色 */
        .type-block { --chip-bg: #f1f5f9; --chip-fg: #334155; --chip-num: #94a3b8; }
        .type-block[data-type="招标计划"] { --chip-bg: #e0f2fe; --chip-fg: #0284c7; --chip-border: #bae6fd; --chip-num: #0284c7; }
        .type-block[data-type="招标公告"] { --chip-bg: #dcfce7; --chip-fg: #16a34a; --chip-border: #bbf7d0; --chip-num: #16a34a; }
        .type-block[data-type="澄清/答疑"] { --chip-bg: #fef3c7; --chip-fg: #d97706; --chip-border: #fde68a; --chip-num: #d97706; }
        .type-block[data-type="控制价公示"] { --chip-bg: #f3e8ff; --chip-fg: #9333ea; --chip-border: #e9d5ff; --chip-num: #9333ea; }
        .type-block[data-type="中标公示"] { --chip-bg: #e0e7ff; --chip-fg: #4338ca; --chip-border: #c7d2fe; --chip-num: #4338ca; }
        .type-block[data-type="中标公告"] { --chip-bg: #fdf4ff; --chip-fg: #c026d3; --chip-border: #f0abfc; --chip-num: #a21caf; }

        /* 统一阶段统计卡色彩 (Unified Stage Colors for Stat Boxes) */
        .stat-box.stage-plan { background: #e0f2fe !important; border-color: #bae6fd !important; }
        .stat-box.stage-plan .sb-label { color: #0284c7 !important; }
        .stat-box.stage-plan .sb-value { color: #0284c7 !important; }
        .stat-box.stage-notice { background: #dcfce7 !important; border-color: #bbf7d0 !important; }
        .stat-box.stage-notice .sb-label { color: #16a34a !important; }
        .stat-box.stage-notice .sb-value { color: #16a34a !important; }
        .stat-box.stage-clarify { background: #fef3c7 !important; border-color: #fde68a !important; }
        .stat-box.stage-clarify .sb-label { color: #d97706 !important; }
        .stat-box.stage-clarify .sb-value { color: #d97706 !important; }
        .stat-box.stage-control { background: #f3e8ff !important; border-color: #e9d5ff !important; }
        .stat-box.stage-control .sb-label { color: #9333ea !important; }
        .stat-box.stage-control .sb-value { color: #9333ea !important; }
        .stat-box.stage-candidate { background: #e0e7ff !important; border-color: #c7d2fe !important; }
        .stat-box.stage-candidate .sb-label { color: #4338ca !important; }
        .stat-box.stage-candidate .sb-value { color: #4338ca !important; }
        .stat-box.stage-award { background: #fdf4ff !important; border-color: #f0abfc !important; }
        .stat-box.stage-award .sb-label { color: #c026d3 !important; }
        .stat-box.stage-award .sb-value { color: #a21caf !important; }
        .stat-box.stage-focus { background: #fef2f2 !important; border-color: #fecaca !important; }
        .stat-box.stage-focus .sb-label { color: #dc2626 !important; }
        .stat-box.stage-focus .sb-value { color: #dc2626 !important; }

        .hero-badge-final {
            background: #ecfdf5 !important;
            color: #065f46 !important;
            border-color: #a7f3d0 !important;
        }
        .hero-badge-final svg {
            color: #059669 !important;
        }
        .final-tag-chip {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 9999px;
            background: #ecfdf5;
            color: #065f46;
            border: 1px solid #a7f3d0;
            margin-left: 8px;
            vertical-align: middle;
        }

        .cat-block .type-head {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 3px 12px;
            border-radius: 9999px;
            background: var(--chip-bg);
            border: 1px solid var(--chip-border, transparent);
            margin-bottom: 12px;
        }
        .cat-block .type-head .type-bar { display: none; }
        .cat-block .type-head .type-name {
            font-size: 0.82rem;
            font-weight: 700;
            line-height: 1.35;
            color: var(--chip-fg);
        }
        .cat-block .type-head .type-count {
            font-size: 0.78rem;
            font-weight: 600;
            line-height: 1.35;
            color: var(--chip-num);
            background: transparent;
            border: none;
            padding: 0;
        }

        /* ③ 条目内地市标签与信源标签：对齐参考图取色 */
        .cat-block .notice-item .city-tag {
            background: #f1f5f9;
            color: #334155;
            border: 1px solid #e2e8f0;
            border-radius: 5px;
            font-size: 0.72rem;
            font-weight: 600;
            line-height: 1.25;
            padding: 2px 8px;
            flex-shrink: 0;
        }

        .cat-block .notice-item .source-tag {
            background: #eff6ff;
            color: #1d4ed8;
            border: 1px solid #bfdbfe;
            border-radius: 5px;
            font-size: 0.72rem;
            font-weight: 600;
            line-height: 1.25;
            padding: 2px 8px;
            white-space: nowrap;
            flex-shrink: 0;
        }

"""

# ------------------------------------------------------------------ 3. 页面骨架（preview 结构与类名）
PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>广西招投标公告日报 · __DATE__ · 业务环节明细</title>
<style>
__PREVIEW_CSS__
</style>
<style>
__DAILY_CSS__
</style>
</head>
<body>
<!-- Header -->
<header class="site-header">
<div class="header-inner">
<a class="brand-logo" href="./index.html">
<div class="brand-icon">
<svg fill="none" height="22" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="22">
<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
<polyline points="14 2 14 8 20 8"></polyline>
<line x1="16" x2="8" y1="13" y2="13"></line>
<line x1="16" x2="8" y1="17" y2="17"></line>
<polyline points="10 9 9 9 8 9"></polyline>
</svg>
</div>
<div class="brand-text">
<h1 style="margin: 0;">广西全区招投标公告日报（__DATE__）</h1>
<p>全区公共资源交易 · 崇左阳光采购公告每日归档</p>
</div>
</a>
<div class="header-actions nav-caps">
<a class="btn-back" href="__PREV_URL__">
<svg fill="none" height="15" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="15"><polyline points="15 18 9 12 15 6"></polyline></svg>
<span>前一日</span>
</a>
<a class="btn-back" href="./index.html">
<svg fill="none" height="15" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="15"><path d="M3 10.5 12 3l9 7.5"></path><path d="M5 9.5V20h14V9.5"></path></svg>
<span>首页</span>
</a>
<a class="btn-back" href="__NEXT_URL__">
<span>后一日</span>
<svg fill="none" height="15" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="15"><polyline points="9 18 15 12 9 6"></polyline></svg>
</a>
</div>
</div>
</header>

<main class="page-container">
    <!-- Hero Overview -->
    <section class="hero-banner">
        <div class="hero-content">
            <div class="hero-badge">
                <svg fill="currentColor" height="12" viewBox="0 0 24 24" width="12"><circle cx="12" cy="12" r="10"></circle></svg>
                <span>DAILY BRIEFING · __DATE__</span>
            </div>
            <h2 class="hero-title">广西全区招投标公告日报（__DATE__）</h2>
            <p class="hero-desc">按 6 大业务环节与工程类别归集当日全区公共资源交易公告，每条公告以官方唯一识别码（infoid）入库，便于溯源与查重，点击标题可跳转至官方公告页面查看原文。支持关键词检索、工程大类 / 地市 / 业务环节筛选与重点预警；无公告更新的类别与类型不在此页展示。</p>
            <div class="stats-grid cols-7">
                <div class="stat-box stage-plan clickable" data-stage="招标计划" title="点击筛选 招标计划">
                    <div class="sb-label">招标计划</div>
                    <div class="sb-value" id="stat-plan">0<span class="sb-unit">条</span></div>
                </div>
                <div class="stat-box stage-notice clickable" data-stage="招标公告" title="点击筛选 招标公告">
                    <div class="sb-label">招标公告</div>
                    <div class="sb-value" id="stat-notice">0<span class="sb-unit">条</span></div>
                </div>
                <div class="stat-box stage-clarify clickable" data-stage="澄清/答疑" title="点击筛选 澄清/答疑">
                    <div class="sb-label">澄清/答疑</div>
                    <div class="sb-value" id="stat-clarify">0<span class="sb-unit">条</span></div>
                </div>
                <div class="stat-box stage-control clickable" data-stage="控制价公示" title="点击筛选 控制价公示">
                    <div class="sb-label">控制价公示</div>
                    <div class="sb-value" id="stat-control">0<span class="sb-unit">条</span></div>
                </div>
                <div class="stat-box stage-candidate clickable" data-stage="中标公示" title="点击筛选 中标公示">
                    <div class="sb-label">中标公示</div>
                    <div class="sb-value" id="stat-candidate">0<span class="sb-unit">条</span></div>
                </div>
                <div class="stat-box stage-award clickable" data-stage="中标公告" title="点击筛选 中标公告">
                    <div class="sb-label">中标公告</div>
                    <div class="sb-value" id="stat-result">0<span class="sb-unit">条</span></div>
                </div>
                <div class="stat-box stage-focus clickable" data-stage="__focus__" title="点击筛选 重点信息">
                    <div class="sb-label">重点信息</div>
                    <div class="sb-value" id="stat-focus">0<span class="sb-unit">条</span></div>
                </div>
            </div>
        </div>
    </section>

    <!-- Filter / Search -->
    <section class="filter-section daily">
        <div class="filter-row top">
            <div class="search-input-wrap">
                <svg class="search-icon" fill="none" height="16" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="16"><circle cx="11" cy="11" r="8"></circle><line x1="21" x2="16.65" y1="21" y2="16.65"></line></svg>
                <input class="search-input" id="searchInput" placeholder="搜索标题、地市或关键词（如 水库、公路、学校）..." type="text"/>
            </div>
            <select class="select-input" id="industryFilter">
                <option value="">全部工程大类</option>
                <option value="水利工程">水利工程</option>
                <option value="交通工程">交通工程</option>
                <option value="铁路工程">铁路工程</option>
                <option value="房建市政工程">房建市政工程</option>
                <option value="其他项目">其他项目</option>
            </select>
            <select class="select-input" id="sourceFilter">
                <option value="">全部网站 (来源平台)</option>
                <option value="广西公共资源交易平台">广西公共资源交易平台</option>
                <option value="崇左阳光采购">崇左阳光采购</option>
            </select>
            <select class="select-input" id="cityFilter">
                <option value="">全部地市 (全区15个交易中心)</option>
                <option value="自治区">自治区本级</option>
                <option value="南宁市">南宁市</option>
                <option value="柳州市">柳州市</option>
                <option value="桂林市">桂林市</option>
                <option value="梧州市">梧州市</option>
                <option value="北海市">北海市</option>
                <option value="防城港市">防城港市</option>
                <option value="钦州市">钦州市</option>
                <option value="贵港市">贵港市</option>
                <option value="玉林市">玉林市</option>
                <option value="百色市">百色市</option>
                <option value="贺州市">贺州市</option>
                <option value="河池市">河池市</option>
                <option value="来宾市">来宾市</option>
                <option value="崇左市">崇左市</option>
            </select>
            <select class="select-input" id="stageFilter">
                <option value="">全部环节 (6大业务环节)</option>
                <option value="招标计划">招标计划</option>
                <option value="招标公告">招标公告</option>
                <option value="澄清/答疑">澄清/答疑</option>
                <option value="控制价公示">控制价公示</option>
                <option value="中标公示">中标公示</option>
                <option value="中标公告">中标公告</option>
            </select>
        </div>
        <div class="filter-row bottom">
            <div class="filter-actions">
                <button class="btn-focus" id="btnFocusOnly" type="button">
                    <svg fill="none" height="13" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="13"><path d="M12 3.5 14.6 9.2l6.4.8-4.7 4.3 1.2 6.2L12 17.6 6.5 20.5l1.2-6.2L3 10l6.4-.8z"></path></svg>
                    仅看重点预警 (<span class="focus-count" id="focusCount">0</span>)
                </button>
                <button class="btn-overtime" id="btnOvertimeOnly" type="button" title="点击仅筛选法定节假日、周末或下班后非工作时间发布的公告">
                    <svg fill="none" height="13" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="13"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                    仅看加班发布 (<span class="overtime-count" id="overtimeCount">0</span>)
                </button>
                <button class="btn-delayed" id="btnDelayedOnly" type="button" title="点击筛选官方标称发布于数天前、但在近期回扫中才首次捕获的滞后公开项目">
                    <svg fill="none" height="13" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="13"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 3"></path></svg>
                    仅看滞后公开 (<span class="delayed-count" id="delayedCount">0</span>)
                </button>
                <button class="btn-reset" id="btnReset" type="button">
                    <svg fill="none" height="13" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="13"><path d="M3 12a9 9 0 1 0 3-6.7"></path><polyline points="3 4 3 9 8 9"></polyline></svg>
                    重置所有筛选
                </button>
            </div>
            <span class="filter-hint" id="filterResultCount">当前筛选匹配 <strong>0</strong> 条标讯</span>
        </div>
    </section>

    __DELAYED_ALERT_BOX__

    <!-- Empty search state -->
    <div class="empty-search-state" id="emptySearch">
        <svg fill="none" height="48" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" width="48"><circle cx="11" cy="11" r="8"></circle><line x1="21" x2="16.65" y1="21" y2="16.65"></line></svg>
        <h3>未找到匹配的公告</h3>
        <p>请尝试其它关键词，或重置工程大类 / 地市 / 业务环节筛选条件</p>
    </div>

    <!-- 三级明细：工程类别 → 公告类型 → 条目 -->
    <div id="recordsContainer"></div>
</main>

<footer class="site-footer">
<p>广西公共资源交易平台体系自动化监控系统 · 工程大类及业务环节多层级结构视图</p>
<p style="margin-top: 6px;">最新公告时间：__LATEST_PUB__ · 最近扫描时间：__SCAN_TIME__ · 由自动化采集监控系统 __APP_VERSION__ 生成</p>
</footer>

<script>
const DELAYED_DATA = __DELAYED_DATA__;
const RAW_DATA = [
__RAW_DATA__
];

const IND_ORDER = ['水利工程', '交通工程', '铁路工程', '房建市政工程', '其他项目'];
const STAGE_ORDER = ['招标计划', '招标公告', '澄清/答疑', '控制价公示', '中标公示', '中标公告'];
const EXT_ICON = '<svg class="ext-icon" fill="none" height="12" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="12"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><path d="M15 3h6v6"></path><path d="M10 14 21 3"></path></svg>';
const OT_ICON = '<svg fill="none" height="11" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="11" style="flex-shrink:0;"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>';
const DEL_ICON = '<svg fill="none" height="11" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewBox="0 0 24 24" width="11" style="flex-shrink:0;"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 3"></path></svg>';
const CAT_ICON = {
    '水利工程': '<svg fill="none" height="20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.3" viewBox="0 0 24 24" width="20"><path d="M3 7.5c2-1.7 4-1.7 6 0s4 1.7 6 0 4-1.7 6 0"></path><path d="M3 12c2-1.7 4-1.7 6 0s4 1.7 6 0 4-1.7 6 0"></path><path d="M3 16.5c2-1.7 4-1.7 6 0s4 1.7 6 0 4-1.7 6 0"></path></svg>',
    '交通工程': '<svg fill="none" height="20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" viewBox="0 0 24 24" width="20"><path d="M4.5 20 8 4h8l3.5 16"></path><path d="M12 4v4"></path><path d="M12 12v3"></path><path d="M6.6 10h10.8"></path></svg>',
    '铁路工程': '<svg fill="none" height="20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" viewBox="0 0 24 24" width="20"><rect height="13" rx="3" width="14" x="5" y="3"></rect><path d="M9 7.5h6"></path><path d="M9 11.5h6"></path><path d="M8 16l-2 5"></path><path d="M16 16l2 5"></path><path d="M9.5 21h5"></path></svg>',
    '房建市政工程': '<svg fill="none" height="20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" viewBox="0 0 24 24" width="20"><path d="M4 21V6a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v15"></path><path d="M13 10.5h6a1 1 0 0 1 1 1V21"></path><path d="M3 21h18"></path><path d="M7 9h3M7 13h3M7 17h3M16.5 14h.01M16.5 17.5h.01"></path></svg>',
    '其他项目': '<svg fill="none" height="20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" viewBox="0 0 24 24" width="20"><path d="M12 3 3 7.6l9 4.6 9-4.6z"></path><path d="M3 12.4 12 17l9-4.6"></path><path d="M3 16.8 12 21.4l9-4.6"></path></svg>'
};

function shortArea(name) {
    if (!name) { return '全区'; }
    if (name === '崇左阳光采购') { return '崇左阳光采购'; }
    if (name.indexOf('自治区') === 0) { return '区中心'; }
    return name.replace(/市$/, '');
}

function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

const container = document.getElementById('recordsContainer');
const empty = document.getElementById('emptySearch');
const hint = document.getElementById('filterResultCount');
const searchInput = document.getElementById('searchInput');
const industryFilter = document.getElementById('industryFilter');
const sourceFilter = document.getElementById('sourceFilter');
const cityFilter = document.getElementById('cityFilter');
const stageFilter = document.getElementById('stageFilter');
const btnFocus = document.getElementById('btnFocusOnly');
let focusOnly = false;

/* 三级明细渲染：工程类别 → 公告类型（业务环节）→ 条目 */
function build() {
    let html = '';
    // 预先归一化 industry，防止意外值导致公告被跳过
    RAW_DATA.forEach(function(d) {
        if (d.industry === '房建市政') { d.industry = '房建市政工程'; }
        if (IND_ORDER.indexOf(d.industry) === -1) { d.industry = '其他项目'; }
    });
    IND_ORDER.forEach(function (ind) {
        const list = RAW_DATA.filter(function (d) { return d.industry === ind; });
        if (!list.length) { return; }
        const citySet = {};
        list.forEach(function (d) { citySet[shortArea(d.areaname)] = 1; });
        const cityCount = Object.keys(citySet).length;

        html += '<section class="cat-block" data-cat="' + esc(ind) + '">';
        html += '<div class="cat-header"><div class="cat-title-group">';
        html += '<div class="cat-icon">' + CAT_ICON[ind] + '</div>';
        html += '<span class="cat-heading">' + esc(ind) + '</span>';
        html += '<span class="cat-chip" data-cities="' + cityCount + '" data-total="' + list.length + '">'
              + list.length + ' 条 · ' + cityCount + ' 个地市</span>';
        html += '</div></div>';

        STAGE_ORDER.forEach(function (stage) {
            const sub = list.filter(function (d) { return d.stage === stage; });
            if (!sub.length) { return; }
            html += '<div class="type-block" data-type="' + esc(stage) + '">';
            html += '<div class="type-head"><span class="type-bar"></span>'
                  + '<span class="type-name">' + esc(stage) + '</span>'
                  + '<span class="type-count" data-total="' + sub.length + '">(' + sub.length + ' 条)</span></div>';
            html += '<div class="notice-list">';
            sub.forEach(function (d) {
                var isCz = (d.source === '崇左阳光采购' || (d.link && d.link.indexOf('gxygcg.com') !== -1) || d.areaname === '崇左阳光采购');
                html += '<a class="notice-item"'
                      + ' data-title="' + esc(d.title) + '"'
                      + ' data-industry="' + esc(d.industry) + '"'
                      + ' data-source="' + (isCz ? '崇左阳光采购' : '广西公共资源交易平台') + '"'
                      + ' data-city="' + esc(d.areaname || '崇左市') + '"'
                      + ' data-stage="' + esc(d.stage) + '"'
                      + ' data-focus="' + (d.is_focus ? 1 : 0) + '"'
                      + ' data-overtime="' + (d.is_overtime ? 1 : 0) + '"'
                      + ' data-delayed="' + (d.is_delayed ? 1 : 0) + '"'
                      + ' href="' + esc(d.link) + '" rel="noopener noreferrer" target="_blank"'
                      + ' title="' + esc(d.title) + '">';
                html += '<span class="city-tag">' + esc(shortArea(d.areaname || '崇左市')) + '</span>';
                if (isCz) {
                    html += '<span class="city-tag source-tag" style="background:#fef3c7;color:#92400e;border-color:#fde68a;">崇左阳光采购</span>';
                }
                html += '<span class="notice-title">' + esc(d.title) + EXT_ICON + '</span>';
                if (d.is_focus) {
                    var tags = d.focus_tags;
                    if (!tags || !tags.length) {
                        tags = ["重点预警"];
                    }
                    for (var ti = 0; ti < tags.length; ti++) {
                        var tname = tags[ti];
                        // 若已渲染专门的滞后公开胶囊，避免在 focus tags 中重复显示
                        if (d.is_delayed && (tname === "滞后公开" || tname === "滞后补录")) {
                            continue;
                        }
                        var cls = "focus-chip";
                        if (tname === "滞后公开" || tname === "滞后补录") cls += " focus-chip-delayed";
                        else if (tname === "重点项目") cls += " focus-chip-project";
                        else if (tname === "重点业主") cls += " focus-chip-owner";
                        else if (tname === "重点关键词") cls += " focus-chip-keyword";
                        else if (tname === "重点类型") cls += " focus-chip-type";
                        else cls += " focus-chip-project";
                        var rtip = Array.isArray(d.focus_reason) ? d.focus_reason.join("; ") : (d.focus_reason || tname);
                        html += '<span class="' + cls + '" title="' + esc(rtip) + '">' + esc(tname) + '</span>';
                    }
                }
                if (d.is_overtime) {
                    var otTip = d.overtime_reason || '加班/非工作时间发布';
                    html += '<span class="overtime-chip" title="' + esc(otTip) + '">' + OT_ICON + '加班发布</span>';
                }
                if (d.is_delayed) {
                    var delTip = d.delayed_reason || ('【存证判定】官网标称发布于 ' + (d.pub_time || '') + '，滞后公开 ' + d.delay_days + ' 天');
                    html += '<span class="delayed-chip" title="' + esc(delTip) + '">' + DEL_ICON + '滞后公开 · ' + d.delay_days + '天</span>';
                }
                html += '<span class="notice-time">' + esc(String(d.pub_time || '').substring(5, 16)) + '</span>';
                html += '</a>';
            });
            html += '</div></div>';
        });

        const otherStages = list.filter(function (d) { return STAGE_ORDER.indexOf(d.stage) === -1; });
        if (otherStages.length > 0) {
            html += '<div class="type-block" data-type="其他环节">';
            html += '<div class="type-head"><span class="type-bar"></span>'
                  + '<span class="type-name">其他公告</span>'
                  + '<span class="type-count" data-total="' + otherStages.length + '">(' + otherStages.length + ' 条)</span></div>';
            html += '<div class="notice-list">';
            otherStages.forEach(function (d) {
                var isCz = (d.source === '崇左阳光采购' || (d.link && d.link.indexOf('gxygcg.com') !== -1) || d.areaname === '崇左阳光采购');
                html += '<a class="notice-item"'
                      + ' data-title="' + esc(d.title) + '"'
                      + ' data-industry="' + esc(d.industry) + '"'
                      + ' data-source="' + (isCz ? '崇左阳光采购' : '广西公共资源交易平台') + '"'
                      + ' data-city="' + esc(d.areaname || '崇左市') + '"'
                      + ' data-stage="' + esc(d.stage || '其他') + '"'
                      + ' data-focus="' + (d.is_focus ? 1 : 0) + '"'
                      + ' data-overtime="' + (d.is_overtime ? 1 : 0) + '"'
                      + ' data-delayed="' + (d.is_delayed ? 1 : 0) + '"'
                      + ' href="' + esc(d.link) + '" rel="noopener noreferrer" target="_blank"'
                      + ' title="' + esc(d.title) + '">';
                html += '<span class="city-tag">' + esc(shortArea(d.areaname || '崇左市')) + '</span>';
                if (isCz) {
                    html += '<span class="city-tag source-tag" style="background:#fef3c7;color:#92400e;border-color:#fde68a;">崇左阳光采购</span>';
                }
                html += '<span class="notice-title">' + esc(d.title) + EXT_ICON + '</span>';
                if (d.is_focus) {
                    var tags = d.focus_tags;
                    if (!tags || !tags.length) { tags = ["重点预警"]; }
                    for (var ti = 0; ti < tags.length; ti++) {
                        var tname = tags[ti];
                        // 若已渲染专门的滞后公开胶囊，避免在 focus tags 中重复显示
                        if (d.is_delayed && (tname === "滞后公开" || tname === "滞后补录")) {
                            continue;
                        }
                        var cls = "focus-chip";
                        if (tname === "滞后公开" || tname === "滞后补录") cls += " focus-chip-delayed";
                        else if (tname === "重点项目") cls += " focus-chip-project";
                        else if (tname === "重点业主") cls += " focus-chip-owner";
                        else if (tname === "重点关键词") cls += " focus-chip-keyword";
                        else if (tname === "重点类型") cls += " focus-chip-type";
                        else cls += " focus-chip-project";
                        var rtip = Array.isArray(d.focus_reason) ? d.focus_reason.join("; ") : (d.focus_reason || tname);
                        html += '<span class="' + cls + '" title="' + esc(rtip) + '">' + esc(tname) + '</span>';
                    }
                }
                if (d.is_overtime) {
                    var otTip = d.overtime_reason || '加班/非工作时间发布';
                    html += '<span class="overtime-chip" title="' + esc(otTip) + '">' + OT_ICON + '加班发布</span>';
                }
                if (d.is_delayed) {
                    var delTip = d.delayed_reason || ('【存证判定】官网标称发布于 ' + (d.pub_time || '') + '，滞后公开 ' + d.delay_days + ' 天');
                    html += '<span class="delayed-chip" title="' + esc(delTip) + '">' + DEL_ICON + '滞后公开 · ' + d.delay_days + '天</span>';
                }
                html += '<span class="notice-time">' + esc(String(d.pub_time || '').substring(5, 16)) + '</span>';
                html += '</a>';
            });
            html += '</div></div>';
        }

        html += '</section>';
    });
    container.innerHTML = html;
}

/* 筛选：搜索词 + 工程大类 + 地市 + 业务环节 + 重点预警 */
function apply() {
    const q = (searchInput.value || '').trim().toLowerCase();
    const ind = industryFilter.value;
    const src = sourceFilter ? sourceFilter.value : '';
    const city = cityFilter.value;
    const stage = stageFilter.value;
    let shown = 0;

    Array.prototype.forEach.call(container.querySelectorAll('.notice-item'), function (it) {
        let ok = true;
        if (q) {
            const hay = (it.getAttribute('data-title') || '').toLowerCase();
            if (hay.indexOf(q) === -1) { ok = false; }
        }
        if (ok && ind && it.getAttribute('data-industry') !== ind) { ok = false; }
        if (ok && src && it.getAttribute('data-source') !== src) { ok = false; }
        if (ok && city) {
            const itemCity = it.getAttribute('data-city') || '';
            if (city === '崇左市') {
                if (itemCity.indexOf('崇左') === -1 && itemCity.indexOf('龙州') === -1 && itemCity.indexOf('扶绥') === -1 && itemCity.indexOf('宁明') === -1 && itemCity.indexOf('凭祥') === -1 && itemCity.indexOf('大新') === -1 && itemCity.indexOf('天等') === -1) {
                    ok = false;
                }
            } else if (itemCity.indexOf(city) === -1) {
                ok = false;
            }
        }
        if (ok && stage && it.getAttribute('data-stage') !== stage) { ok = false; }
        if (ok && focusOnly && it.getAttribute('data-focus') !== '1') { ok = false; }
        if (ok && overtimeOnly && it.getAttribute('data-overtime') !== '1') { ok = false; }
        if (ok && delayedOnly && it.getAttribute('data-delayed') !== '1') { ok = false; }
        it.hidden = !ok;
        if (ok) { shown++; }
    });

    Array.prototype.forEach.call(container.querySelectorAll('.type-block'), function (tb) {
        const vis = tb.querySelectorAll('.notice-item:not([hidden])').length;
        tb.hidden = vis === 0;
        const chip = tb.querySelector('.type-count');
        if (chip) {
            const total = Number(chip.getAttribute('data-total'));
            chip.textContent = '(' + (vis === total ? total : vis + ' / ' + total) + ' 条)';
        }
    });

    Array.prototype.forEach.call(container.querySelectorAll('.cat-block'), function (cb) {
        const vis = cb.querySelectorAll('.notice-item:not([hidden])').length;
        cb.hidden = vis === 0;
        const chip = cb.querySelector('.cat-chip');
        if (chip) {
            const total = Number(chip.getAttribute('data-total'));
            chip.textContent = (vis === total ? total : vis + ' / ' + total)
                             + ' 条 · ' + chip.getAttribute('data-cities') + ' 个地市';
        }
    });

    hint.innerHTML = '当前筛选匹配 <strong>' + shown + '</strong> 条标讯';
    empty.style.display = shown === 0 ? 'block' : 'none';

    /* 同步高亮业务环节与重点信息统计卡激活状态 */
    document.querySelectorAll('.stat-box.clickable').forEach(function(box) {
        var st = box.getAttribute('data-stage');
        if (st === '__focus__') {
            if (focusOnly) {
                box.classList.add('active');
            } else {
                box.classList.remove('active');
            }
        } else if (stage && st === stage) {
            box.classList.add('active');
        } else {
            box.classList.remove('active');
        }
    });
}

searchInput.addEventListener('input', apply);
industryFilter.addEventListener('change', apply);
if (sourceFilter) sourceFilter.addEventListener('change', apply);
cityFilter.addEventListener('change', apply);
stageFilter.addEventListener('change', apply);

btnFocus.addEventListener('click', function () {
    focusOnly = !focusOnly;
    this.classList.toggle('active', focusOnly);
    apply();
});

let overtimeOnly = false;
const btnOvertime = document.getElementById('btnOvertimeOnly');
if (btnOvertime) {
    btnOvertime.addEventListener('click', function () {
        overtimeOnly = !overtimeOnly;
        this.classList.toggle('active', overtimeOnly);
        apply();
    });
}

let delayedOnly = false;
const btnDelayed = document.getElementById('btnDelayedOnly');
if (btnDelayed) {
    btnDelayed.addEventListener('click', function () {
        delayedOnly = !delayedOnly;
        this.classList.toggle('active', delayedOnly);
        apply();
        var alertBox = document.getElementById('delayedAlertBox');
        if (delayedOnly && alertBox) {
            alertBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    });
}

document.getElementById('btnReset').addEventListener('click', function () {
    searchInput.value = '';
    industryFilter.value = '';
    if (sourceFilter) sourceFilter.value = '';
    cityFilter.value = '';
    stageFilter.value = '';
    focusOnly = false;
    btnFocus.classList.remove('active');
    overtimeOnly = false;
    if (btnOvertime) btnOvertime.classList.remove('active');
    delayedOnly = false;
    if (btnDelayed) btnDelayed.classList.remove('active');
    apply();
});

/* 统计卡点击联动筛选与平滑滚动 */
document.querySelectorAll('.stat-box.clickable').forEach(function(box) {
    box.addEventListener('click', function() {
        var targetStage = this.getAttribute('data-stage');
        if (targetStage === '__focus__') {
            focusOnly = !focusOnly;
            btnFocus.classList.toggle('active', focusOnly);
        } else if (stageFilter.value === targetStage) {
            stageFilter.value = '';
        } else {
            stageFilter.value = targetStage;
        }
        apply();
        var filterEl = document.querySelector('.filter-section.daily');
        if (filterEl) {
            filterEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    });
});

document.getElementById('focusCount').textContent = RAW_DATA.filter(function (d) { return d.is_focus; }).length;
const otCountEl = document.getElementById('overtimeCount');
if (otCountEl) otCountEl.textContent = RAW_DATA.filter(function (d) { return d.is_overtime; }).length;
const delCountEl = document.getElementById('delayedCount');
if (delCountEl) {
    var rawDelCnt = RAW_DATA.filter(function (d) { return d.is_delayed; }).length;
    var extraDelCnt = (typeof DELAYED_DATA !== 'undefined' && Array.isArray(DELAYED_DATA)) ? DELAYED_DATA.length : 0;
    delCountEl.textContent = rawDelCnt + extraDelCnt;
}
const statFocusEl = document.getElementById('stat-focus');
if (statFocusEl && statFocusEl.firstChild) {
    statFocusEl.firstChild.textContent = RAW_DATA.filter(function (d) { return d.is_focus; }).length;
}

build();
apply();
</script>
</body>
</html>
"""

# ------------------------------------------------------------------ 4. 变量注入
html = PAGE
html = html.replace("__PREVIEW_CSS__", PREVIEW_CSS)
html = html.replace("__DAILY_CSS__", DAILY_CSS.strip())
html = html.replace("__LATEST_PUB__", latest_pub)
html = html.replace("__SCAN_TIME__", scan_time)
html = html.replace("__LATEST__", latest_pub)
html = html.replace("__APP_VERSION__", getattr(config, "APP_VERSION", "v0.3.1"))
html = html.replace("__DATE__", DAY)
html = html.replace("__PREV_URL__", neighbor_url(-1))
html = html.replace("__NEXT_URL__", neighbor_url(1))

# 构建今日回扫发现滞后公告的横幅 HTML
if today_delayed_items:
    rows_h = []
    for dit in today_delayed_items:
        d_delay = dit.get("delay_days", 0)
        d_title = html_mod.escape(dit.get("title", ""))
        d_link = html_mod.escape(dit.get("link") or dit.get("detail_url") or "#")
        d_pub = html_mod.escape(str(dit.get("pub_time", ""))[:16])
        d_area = html_mod.escape(str(dit.get("areaname", "") or dit.get("source", "")))
        d_stage = html_mod.escape(str(dit.get("stage", "") or "公告"))
        rows_h.append(
            f'<a class="delayed-item-row" href="{d_link}" target="_blank" rel="noopener noreferrer">\n'
            f'    <div class="delayed-item-main">\n'
            f'        <span class="delayed-days-tag" style="border-radius:9999px;padding:2px 8px;">滞后公开 {d_delay} 天</span>\n'
            f'        <span class="delayed-item-title">{d_title}</span>\n'
            f'    </div>\n'
            f'    <div class="delayed-item-meta">\n'
            f'        <span>【{d_area}·{d_stage}】</span>\n'
            f'        <span>官方标称: {d_pub}</span>\n'
            f'        <span style="color:#ea580c;font-weight:700;">今日回扫捕获</span>\n'
            f'    </div>\n'
            f'</a>'
        )
    
    total_d_cnt = len(rows_h)
    if total_d_cnt > 5:
        top_rows = "".join(rows_h[:5])
        more_rows = "".join(rows_h[5:])
        list_inner = (
            f'{top_rows}\n'
            f'<div id="delayedAlertMore" style="display:none;flex-direction:column;gap:8px;">{more_rows}</div>\n'
            f'<button type="button" class="btn-toggle-delayed-more" id="btnToggleDelayedMore"'
            f' onclick="var el=document.getElementById(\'delayedAlertMore\');'
            f'if(el.style.display===\'none\'){{el.style.display=\'flex\';this.textContent=\'收起其余 {total_d_cnt - 5} 条 ▲\';}}'
            f'else{{el.style.display=\'none\';this.textContent=\'展开查看全部 {total_d_cnt} 条滞后公开项目（已显示前 5 条） ▼\';}}">'
            f'展开查看全部 {total_d_cnt} 条滞后公开项目（已显示前 5 条） ▼</button>'
        )
    else:
        list_inner = "".join(rows_h)

    delayed_alert_html = (
        f'<div class="delayed-alert-box" id="delayedAlertBox">\n'
        f'    <div class="delayed-alert-header">\n'
        f'        <div class="delayed-alert-title-row">\n'
        f'            <span class="delayed-alert-badge" style="border-radius:9999px;">🚨 历史回扫特别预警</span>\n'
        f'            <span class="delayed-alert-title">今日回扫捕获 <strong>{len(today_delayed_items)}</strong> 条被官方滞后公开的项目</span>\n'
        f'        </div>\n'
        f'        <div class="delayed-alert-desc">官方标称发布于 2~30 天前，此前巡检未见该条目，在今日定时回扫中首次扫描捕获并确证存证</div>\n'
        f'    </div>\n'
        f'    <div class="delayed-alert-list">\n'
        f'        {list_inner}\n'
        f'    </div>\n'
        f'</div>'
    )
else:
    delayed_alert_html = ""

html = html.replace("__DELAYED_ALERT_BOX__", delayed_alert_html)
html = html.replace("__DELAYED_DATA__", json.dumps(today_delayed_items, ensure_ascii=False), 1)

lines = ",\n".join("    " + json.dumps(it, ensure_ascii=False) for it in items)
html = html.replace("__RAW_DATA__", lines, 1)

for sid, val in stat_map.items():
    html, n = re.subn(r'(id="%s">)0(<span class="sb-unit")' % sid, lambda m: m.group(1) + str(val) + m.group(2), html)
    assert n == 1, sid

html, n = re.subn(r'(id="focusCount">)0(<)', lambda m: m.group(1) + str(focus_n) + m.group(2), html)
assert n == 1, "focusCount"
overtime_n = sum(1 for it in items if it.get("is_overtime"))
html, n = re.subn(r'(id="overtimeCount">)0(<)', lambda m: m.group(1) + str(overtime_n) + m.group(2), html)
assert n == 1, "overtimeCount"
html, n = re.subn(r'(id="delayedCount">)0(<)', lambda m: m.group(1) + str(delayed_n) + m.group(2), html)
assert n == 1, "delayedCount"
html = html.replace("当前筛选匹配 <strong>0</strong>", "当前筛选匹配 <strong>%d</strong>" % total_n, 1)

is_final_page = bool(getattr(ARGS, "final", False) or (config.STATE_DIR / ("final-%s.json" % DAY)).exists())
if is_final_page:
    html = html.replace(
        '<div class="hero-badge">',
        '<div class="hero-badge hero-badge-final">'
    )
    html = html.replace(
        '<span>DAILY BRIEFING · %s</span>' % DAY,
        '<span>DAILY BRIEFING · %s · 终版封存</span>' % DAY
    )
    html = html.replace(
        '<h2 class="hero-title">广西全区招投标公告日报（%s）</h2>' % DAY,
        '<h2 class="hero-title">广西全区招投标公告日报（%s）<span class="final-tag-chip"><svg fill="none" stroke="currentColor" stroke-width="2.5" height="12" viewBox="0 0 24 24" width="12" style="display:inline-block"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg> 终版</span></h2>' % DAY
    )

# ------------------------------------------------------------------ 5. 备份旧产物并写盘
if OUT.exists():
    TMPDIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUT, TMPDIR / ("backup_old_" + OUT.name))

OUT.write_text(html, encoding="utf-8")

# 运行日志（主键 infoid）与全局台账
stamp = config.now_stamp()
log_rows, log_pending = logstore.write_log(DAY, items, stamp)
log_index = logstore.update_index(items, DAY, stamp)

# ------------------------------------------------------------------ 6. 自检（无人值守运行时的健康检查）
problems = []


def check(label, ok, detail=""):
    print("%-16s: %s%s" % (label, "OK" if ok else "FAIL", (" | " + detail) if detail else ""))
    if not ok:
        problems.append(label)


out = OUT.read_text(encoding="utf-8")
static = out.split("<script>")[0]
raw_block = out.split("const RAW_DATA = [")[1].split("\n];")[0]
raw_rows = json.loads("[" + raw_block.strip().rstrip(",") + "]")

check("设计基准未变", hashlib.md5(PREVIEW.read_text(encoding="utf-8").encode("utf-8")).hexdigest() == PREVIEW_MD5)
check("数据条数", len(raw_rows) == total_n, "RAW_DATA=%d 期望=%d" % (len(raw_rows), total_n))
check("唯一识别码", len({r["infoid"] for r in raw_rows}) == total_n, "infoid 唯一")
check("官方链接", all(("projectDetails.html?infoid=" in r["link"] or "cz.gxygcg.com" in r["link"]) for r in raw_rows))
check("无自造编号", ("code-tag" not in out) and ("data-code" not in out) and ("GX%s" % DAY_KEY) not in out)
check("指标卡", static.count('class="sb-label"') == 7, json.dumps(stat_map, ensure_ascii=False))
check("筛选控件", static.count('class="select-input"') == 4 and "仅看重点预警" in static and "仅看加班发布" in static and "重置所有筛选" in static)
check("静态 div 配平", static.count("<div") == static.count("</div>"))
check("外部依赖", not any(k in out.lower() for k in ("tailwind", "all.min.css", "saved_resource", "file://")))
check("运行日志", log_rows == total_n, "%s 行=%d 待重取=%d" % (LOG, log_rows, len(log_pending)))
check("全局台账", all(it["infoid"] in log_index for it in items), "index.json 共 %d 条" % len(log_index))
print("产物            :", OUT)
if problems:
    print("自检未通过      :", "、".join(problems))
    raise SystemExit(1)
print("自检通过")

# ------------------------------------------------------------------ 联动更新归档首页
print("正在同步更新首页/归档索引...")
try:
    import run_daily
    run_daily.refresh_archive(DAY)
    try:
        import run_archive
        rc = run_archive.main() if hasattr(run_archive, 'main') else 0
    except ImportError:
        archive_script = _REPO / "scripts" / "build_archive_page.py"
        if getattr(sys, 'frozen', False):
            import runpy
            runpy.run_path(str(archive_script), run_name="__main__")
            rc = 0
        else:
            rc = subprocess.run([sys.executable, str(archive_script)]).returncode
    if rc == 0:
        print("归档首页同步完成")
    else:
        print("WARN: 归档首页生成返回异常码 %d" % rc)

    # 联动同步 VPS
    if getattr(config, "AUTO_UPLOAD_VPS", True) and not getattr(ARGS, "no_vps", False):
        upload_script = _REPO / "scripts" / "upload_vps.py"
        if upload_script.exists():
            subprocess.run([sys.executable, str(upload_script)], check=False)
except Exception as exc:
    print("WARN: 同步归档首页失败: %s" % exc)
