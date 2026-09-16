# -*- coding: utf-8 -*-
"""基于归档样板（templates/index-sample.html）的视觉体系生成「归档首页」(<SITE_DIR>/index.html)。

- 完整复用样板内联 CSS（含设计变量）与 header / footer 视觉结构
- 月历视图 + 清单视图两套结构，均按日期跳转 ./YYYY-MM-DD.html
- 无数据日期置灰不可点；清单项展示日期 / 条数 / 地市数 / 类别分布标签
"""
import calendar
import datetime
import hashlib
import html
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
import config  # noqa: E402

# 归档首页样板（视觉体系来源）与数据源、产物目录全部走配置，无硬编码路径
SAMPLE = config.TEMPLATE_ARCHIVE_SAMPLE
ARCHIVE = config.DATA_DIR / "archive.json"
OUT_DIR = config.SITE_DIR

WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
CAT_COLORS = ["#0284c7", "#0891b2", "#6366f1", "#0d9488", "#64748b"]


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def density_of(count: int) -> int:
    if count <= 0:
        return 0
    if count <= 60:
        return 1
    if count <= 160:
        return 2
    if count <= 240:
        return 3
    return 4


def comma(n):
    return "{:,}".format(n)


sample_md5_before = md5(SAMPLE)
raw = SAMPLE.read_text(encoding="utf-8")

# ---------- 复用样板：内联 CSS 全量 + 内联 JS 全量 ----------
base_style = raw[raw.find("<style>"):raw.find("</style>") + len("</style>")]
base_script = raw[raw.find("<script>"):raw.find("</script>") + len("</script>")]

extra_style = """
<style>
        /* ===== Archive portal additions (category tags / month card meta) ===== */
        .cat-tag-row {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 8px;
        }

        .cat-tag {
            font-size: 0.72rem;
            font-weight: 600;
            padding: 2px 9px;
            border-radius: 9999px;
            border: 1px solid transparent;
            white-space: nowrap;
        }

        .metric-chip {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--primary-dark);
            background: var(--primary-light);
            border: 1px solid var(--primary-border);
            padding: 4px 10px;
            border-radius: 9999px;
            flex-shrink: 0;
        }

        .cal-meta-line {
            display: block;
            font-size: 0.66rem;
            color: var(--text-muted);
            margin-top: 3px;
            line-height: 1.3;
        }

        /* ===== 今日标识样式 (Today Highlight) ===== */
        .cal-today {
            border-color: var(--primary) !important;
            box-shadow: 0 0 0 2px rgba(2, 132, 199, 0.22), 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            position: relative;
        }

        .cal-today::after {
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: var(--primary);
            border-top-left-radius: var(--radius-md);
            border-top-right-radius: var(--radius-md);
        }

        .cal-pill-today {
            font-size: 0.65rem;
            padding: 1px 6px;
            background: #dc2626;
            color: #ffffff;
            border-radius: 4px;
            font-weight: 700;
            letter-spacing: 0.02em;
            box-shadow: 0 1px 2px rgba(220, 38, 38, 0.35);
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }

        .chip-today {
            background: #fef2f2;
            color: #dc2626;
            border: 1px solid #fecaca;
            font-weight: 700;
        }

        .history-row-today {
            border-left: 4px solid var(--primary) !important;
            background: linear-gradient(to right, rgba(240, 249, 255, 0.85), var(--card-bg)) !important;
        }

        .archive-hint {
            font-size: 0.8rem;
            font-weight: 600;
            color: var(--text-muted);
            white-space: nowrap;
        }

        .archive-hint strong {
            color: var(--primary-dark);
            font-weight: 800;
        }

        @media (max-width: 900px) {
            .history-row-item {
                flex-wrap: wrap;
            }
            .item-col-metrics {
                width: 100%;
                justify-content: space-between;
            }
        }
    </style>
"""

# ---------- 数据 ----------
arc = json.loads(ARCHIVE.read_text(encoding="utf-8"))
days = sorted(arc["days"], key=lambda d: d["date"], reverse=True)
day_map = {d["date"]: d for d in days}
day_count = arc.get("day_count", len(days))
total_all = arc.get("total_all", sum(d["total"] for d in days))
latest = days[0] if days else {"date": "", "total": 0, "cities": 0, "cat_count": 0, "updated": ""}
latest_date = latest["date"]
latest_hhmm = latest.get("updated", "")[-5:]
last_month = latest_date[:7] if latest_date else ""
cities_all = max((d.get("cities", 0) for d in days), default=0)

# 物化的月份卡片（数据驱动：cover 数据中出现的最多 3 个月份）
months = sorted({d["date"][:7] for d in days}, reverse=True)[:3]

# ---------- 月历视图 ----------
month_cards = []
today_str = datetime.date.today().strftime("%Y-%m-%d")
for ym in months:
    y, m = int(ym[:4]), int(ym[5:7])
    m_days = [d for d in days if d["date"][:7] == ym]
    m_total = sum(d["total"] for d in m_days)
    cells = []
    lead = datetime.date(y, m, 1).weekday()
    cells.extend(['<div class="cal-cell cal-cell-empty"></div>'] * lead)
    ndays = calendar.monthrange(y, m)[1]
    for day in range(1, ndays + 1):
        ds = "%04d-%02d-%02d" % (y, m, day)
        is_today = (ds == today_str)
        today_cls = " cal-today" if is_today else ""
        today_pill = '<span class="cal-pill-today">今日</span>' if is_today else ''
        if ds in day_map:
            d = day_map[ds]
            dens = density_of(d["total"])
            cells.append(
                '<a class="cal-cell cal-cell-active density-{dens}{today_cls}" data-count="{cnt}" data-cities="{cities}" '
                'data-date="{ds}" href="./{ds}.html">\n'
                '<div class="cal-cell-header">\n'
                '<span class="cal-date-num">{day}</span>\n'
                '<div class="cal-pill-group">{today_pill}</div>\n'
                '</div>\n'
                '<div class="cal-cell-body">\n'
                '<span class="cal-count-badge">{cnt}<span class="unit">条</span></span>\n'
                '<span class="cal-meta-line">{cities} 个地市 · {cats} 个类别</span>\n'
                '</div>\n'
                '</a>'.format(dens=dens, cnt=comma(d["total"]), cities=d.get("cities", 0),
                              cats=d.get("cat_count", 0), ds=ds, day=day, today_cls=today_cls, today_pill=today_pill)
            )
        else:
            cells.append(
                '<div class="cal-cell cal-cell-disabled{today_cls}">\n'
                '<div class="cal-cell-header">\n'
                '<span class="cal-date-num">{day}</span>\n'
                '<div class="cal-pill-group">{today_pill}</div>\n'
                '</div>\n'
                '<div class="cal-cell-body">\n'
                '<span class="cal-no-data">-</span>\n'
                '</div>\n'
                '</div>'.format(day=day, today_cls=today_cls, today_pill=today_pill)
            )
    trail = (7 - (lead + ndays) % 7) % 7
    cells.extend(['<div class="cal-cell cal-cell-empty"></div>'] * trail)

    month_cards.append(
        '<div class="month-calendar-card" data-year-month="{ym}">\n'
        '<div class="month-card-header">\n'
        '<div class="month-title-group">\n'
        '<div class="month-icon">\n'
        '<svg fill="none" height="20" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="20"><rect height="18" rx="2" ry="2" width="18" x="3" y="4"></rect><line x1="16" x2="16" y1="2" y2="6"></line><line x1="8" x2="8" y1="2" y2="6"></line><line x1="3" x2="21" y1="10" y2="10"></line></svg>\n'
        '</div>\n'
        '<span class="month-heading">{y} 年 {m:02d} 月</span>\n'
        '<span class="month-chip">{mdays} 个简报日</span>\n'
        '</div>\n'
        '<div class="month-stats-group">\n'
        '<span class="stat-pill"><span class="label">本月收录:</span> <strong>{mtotal}</strong> 条</span>\n'
        '<span class="stat-pill"><span class="label">覆盖地市:</span> <strong>{mcities}</strong> 个</span>\n'
        '</div>\n'
        '</div>\n'
        '<div class="cal-weekdays-row">\n'
        '<span>周一</span><span>周二</span><span>周三</span><span>周四</span><span>周五</span><span class="wknd">周六</span><span class="wknd">周日</span>\n'
        '</div>\n'
        '<div class="cal-grid-body">\n{cells}\n</div>\n'
        '</div>'.format(
            ym=ym, y=y, m=m, mdays=len(m_days), mtotal=comma(m_total),
            mcities=max((d.get("cities", 0) for d in m_days), default=0),
            cells="\n".join(cells)
        )
    )

calendar_html = "\n".join(month_cards)

# ---------- 清单视图 ----------
rows = []
for d in days:
    ds = d["date"]
    dt = datetime.datetime.strptime(ds, "%Y-%m-%d")
    groups = d.get("groups", {}) or {}
    tags = []
    for i, (gname, gcount) in enumerate(groups.items()):
        c = CAT_COLORS[i % len(CAT_COLORS)]
        tags.append('<span class="cat-tag" style="color:{c}; background:{c}14; border-color:{c}33;">{n} {cnt}</span>'.format(
            c=c, n=html.escape(gname), cnt=gcount))
    is_today = (ds == today_str)
    today_chip = '<span class="status-chip chip-today">今日</span>' if is_today else ''
    row_today_cls = " history-row-today" if is_today else ""
    rows.append(
        '<a class="history-row-item{row_today_cls}" data-cities="{cities}" data-count="{cnt}" data-date="{ds}" data-month="{m}" data-year="{y}" href="./{ds}.html">\n'
        '<div class="item-col-date">\n'
        '<div class="date-calendar-box">\n'
        '<span class="dc-month">{m:02d}月</span>\n'
        '<span class="dc-day">{day:02d}</span>\n'
        '</div>\n'
        '<div class="date-meta-box">\n'
        '<div class="dm-primary">\n'
        '<span class="dm-datestr">{ds}</span>\n'
        '<span class="dm-weekday">{wk}</span>\n'
        '{today_chip}\n'
        '</div>\n'
        '<div class="dm-secondary">\n'
        '                        广西全区公共资源交易信息 · 当日简报全量归档\n'
        '                    </div>\n'
        '<div class="cat-tag-row">\n{tags}\n</div>\n'
        '</div>\n'
        '</div>\n'
        '<div class="item-col-metrics">\n'
        '<span class="metric-chip">{cities} 个地市</span>\n'
        '<span class="metric-chip">{cats} 个类别</span>\n'
        '<div class="metrics-visual">\n'
        '<div class="num-wrapper">\n'
        '<span class="count-value">{cnt}</span>\n'
        '<span class="count-unit">条公告</span>\n'
        '</div>\n'
        '</div>\n'
        '<div class="action-arrow">\n'
        '<svg fill="none" height="20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" viewbox="0 0 24 24" width="20"><path d="M5 12h14"></path><path d="M12 5l7 7-7 7"></path></svg>\n'
        '</div>\n'
        '</div>\n'
        '</a>'.format(
            ds=ds, y=dt.year, m=dt.month, day=dt.day, wk=WEEK[dt.weekday()],
            cnt=comma(d["total"]), cities=d.get("cities", 0), cats=d.get("cat_count", 0),
            tags="\n".join(tags),
            today_chip=today_chip,
            row_today_cls=row_today_cls
        )
    )

list_html = "\n".join(rows)

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>广西招投标监控看板 - 导航与历史中心</title>
{base_style}
{extra_style}
</head>
<body>
<!-- Header -->
<header class="site-header">
<div class="header-inner">
<a class="brand-logo" href="./index.html">
<div class="brand-icon">
<svg fill="none" height="22" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" viewbox="0 0 24 24" width="22">
<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
<polyline points="14 2 14 8 20 8"></polyline>
<line x1="16" x2="8" y1="13" y2="13"></line>
<line x1="16" x2="8" y1="17" y2="17"></line>
<polyline points="10 9 9 9 8 9"></polyline>
</svg>
</div>
<div class="brand-text">
<div style="display: flex; align-items: center; gap: 8px;">
<h1 style="margin: 0;">招投标每日简报</h1>
                <span style="font-size: 11px; font-weight: 700; color: #0284c7; background: #e0f2fe; padding: 2px 7px; border-radius: 9999px; border: 1px solid #bae6fd;">v0.0.4</span>
</div>
<p>历史归档与数据追溯中心</p>
</div>
</a>
<div class="header-actions">
<a class="btn-latest" href="./{latest_date}.html" style="background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%); color: white; border-color: transparent; box-shadow: 0 4px 12px rgba(2, 132, 199, 0.25);">
<svg fill="none" height="16" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="16"><rect height="18" rx="2" ry="2" width="18" x="3" y="4"></rect><line x1="16" x2="16" y1="2" y2="6"></line><line x1="8" x2="8" y1="2" y2="6"></line><line x1="3" x2="21" y1="10" y2="10"></line></svg>
<span>查看最新简报 ({latest_date})</span>
<svg fill="none" height="16" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="16"><polyline points="9 18 15 12 9 6"></polyline></svg>
</a>
</div>
</div>
</header>
<main class="page-container">
<!-- Hero Overview -->
<section class="hero-banner">
<div class="hero-content">
<div class="hero-badge">
<svg fill="currentColor" height="12" viewbox="0 0 24 24" width="12"><circle cx="12" cy="12" r="10"></circle></svg>
<span>PORTAL &amp; ARCHIVE DATABASE</span>
</div>
<h2 class="hero-title">{site}</h2>
<p class="hero-desc">{subtitle}。可按月历或清单检索历史简报，点击任意有数据的日期即可直达当日公告明细页。</p>
<div class="stats-grid">
<div class="stat-box">
<div class="sb-label">累计收录天数</div>
<div class="sb-value">{day_count}<span class="sb-unit">天</span></div>
</div>
<div class="stat-box">
<div class="sb-label">累计收录公告</div>
<div class="sb-value">{total_all}<span class="sb-unit">条</span></div>
</div>
<div class="stat-box">
<div class="sb-label">最近更新日期</div>
<div class="sb-value" style="font-size: 1.35rem;">{latest_date}<span class="sb-unit">{latest_hhmm}</span></div>
</div>
<div class="stat-box">
<div class="sb-label">最近覆盖地市</div>
<div class="sb-value">{cities_all}<span class="sb-unit">个</span></div>
</div>
</div>
</div>
</section>
<!-- Filter / View Switcher -->
<section class="filter-section">
<div class="filter-left">
<div class="search-input-wrap">
<svg class="search-icon" fill="none" height="16" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="16"><circle cx="11" cy="11" r="8"></circle><line x1="21" x2="16.65" y1="21" y2="16.65"></line></svg>
<input class="search-input" id="archiveSearch" placeholder="输入日期检索（如 2026-09、09-10、周四）..." type="text"/>
</div>
</div>
<div class="filter-right">
<div class="density-legend">
<span>发布热度:</span>
<span class="legend-dot" style="background:#f8fafc; border:1px solid #e2e8f0;" title="0条"></span>
<span class="legend-dot" style="background:#f0f7ff; border:1px solid #dbeafe;" title="1-60条"></span>
<span class="legend-dot" style="background:#e0eefe; border:1px solid #bfdbfe;" title="61-160条"></span>
<span class="legend-dot" style="background:#bae0fd; border:1px solid #93c5fd;" title="161-240条"></span>
<span class="legend-dot" style="background:#3b82f6; border:1px solid #2563eb;" title="&gt;240条"></span>
</div>
<div class="view-switcher">
<button class="view-btn active" id="btnViewCalendar" onclick="switchView('calendar')">
<svg fill="none" height="15" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="15"><rect height="18" rx="2" ry="2" width="18" x="3" y="4"></rect><line x1="16" x2="16" y1="2" y2="6"></line><line x1="8" x2="8" y1="2" y2="6"></line><line x1="3" x2="21" y1="10" y2="10"></line></svg>
<span>月历视图</span>
</button>
<button class="view-btn" id="btnViewList" onclick="switchView('list')">
<svg fill="none" height="15" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="15"><line x1="8" x2="21" y1="6" y2="6"></line><line x1="8" x2="21" y1="12" y2="12"></line><line x1="8" x2="21" y1="18" y2="18"></line><line x1="3" x2="3.01" y1="6" y2="6"></line><line x1="3" x2="3.01" y1="12" y2="12"></line><line x1="3" x2="3.01" y1="18" y2="18"></line></svg>
<span>清单列表</span>
</button>
</div>
</div>
</section>
<!-- Archive summary hint -->
<section class="filter-section" style="padding-top: 0; border: none; background: transparent; box-shadow: none;">
<div class="filter-left">
<span class="archive-hint" id="archiveHint">共 <strong>{day_count}</strong> 个简报日 · 累计 <strong>{total_all}</strong> 条公告 · 最近更新 {latest_date}</span>
</div>
</section>
<!-- Empty search state -->
<div class="empty-search-state" id="emptySearch" style="display: none;">
<svg fill="none" height="48" stroke="currentColor" stroke-width="1.5" viewbox="0 0 24 24" width="48"><circle cx="11" cy="11" r="8"></circle><line x1="21" x2="16.65" y1="21" y2="16.65"></line></svg>
<h3>未找到匹配的归档简报</h3>
<p>请尝试搜索年份、月份（如 2026-09）或具体日期</p>
</div>
<!-- View 1: Calendar View -->
<div class="view-panel active" id="viewCalendar">
<div class="calendar-grid-container">
{calendar_html}
</div>
</div>
<!-- View 2: List View -->
<div class="view-panel" id="viewList">
<div class="history-list-wrap">
{list_html}
</div>
</div>
</main>
<footer class="site-footer">
<p>广西招投标自动化监控分析看板系统 · 每日定时自动采集与归档</p>
</footer>
{base_script}
<script>
(function() {
    function formatLocalDate(d) {
        var year = d.getFullYear();
        var month = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        return year + '-' + month + '-' + day;
    }
    var todayStr = formatLocalDate(new Date());

    // 1. 清理静态已存在的 cal-today、cal-pill-today、history-row-today、chip-today
    document.querySelectorAll('.cal-today').forEach(function(el) {
        el.classList.remove('cal-today');
    });
    document.querySelectorAll('.cal-pill-today').forEach(function(el) {
        el.remove();
    });
    document.querySelectorAll('.history-row-today').forEach(function(el) {
        el.classList.remove('history-row-today');
    });
    document.querySelectorAll('.chip-today').forEach(function(el) {
        el.remove();
    });

    // 2. 日历网格：根据客户端实际今日动态定位并高亮
    var activeCell = document.querySelector('.cal-cell-active[href*="' + todayStr + '.html"]');
    if (activeCell) {
        activeCell.classList.add('cal-today');
        var pillGroup = activeCell.querySelector('.cal-pill-group');
        if (pillGroup && !pillGroup.querySelector('.cal-pill-today')) {
            var pill = document.createElement('span');
            pill.className = 'cal-pill-today';
            pill.textContent = '今日';
            pillGroup.appendChild(pill);
        }
    } else {
        // 如果今日尚无采集数据，在置灰日期格中查找并添加今日提示
        var parts = todayStr.split('-');
        var ym = parts[0] + '-' + parts[1];
        var dayNum = parseInt(parts[2], 10);
        var monthCard = document.querySelector('.month-calendar-card[data-year-month="' + ym + '"]');
        if (monthCard) {
            var disabledCells = monthCard.querySelectorAll('.cal-cell-disabled');
            disabledCells.forEach(function(cell) {
                var numElem = cell.querySelector('.cal-date-num');
                if (numElem && parseInt(numElem.textContent.trim(), 10) === dayNum) {
                    cell.classList.add('cal-today');
                    var pillGroup = cell.querySelector('.cal-pill-group');
                    if (pillGroup && !pillGroup.querySelector('.cal-pill-today')) {
                        var pill = document.createElement('span');
                        pill.className = 'cal-pill-today';
                        pill.textContent = '今日';
                        pillGroup.appendChild(pill);
                    }
                }
            });
        }
    }

    // 3. 清单视图高亮
    var listRow = document.querySelector('.history-row-item[href*="' + todayStr + '.html"]');
    if (listRow) {
        listRow.classList.add('history-row-today');
        var titleElem = listRow.querySelector('.item-date-title');
        if (titleElem && !titleElem.querySelector('.chip-today')) {
            var chip = document.createElement('span');
            chip.className = 'status-chip chip-today';
            chip.textContent = '今日';
            titleElem.appendChild(chip);
        }
    }
})();
</script>
</body>
</html>
"""

_FIELDS = {
    "base_style": base_style,
    "extra_style": extra_style,
    "base_script": base_script,
    "site": html.escape(arc.get("site", "广西全区招投标数据监控中心")),
    "subtitle": html.escape(arc.get("subtitle", "广西公共资源交易 · 工程建设类公告每日归档")),
    "day_count": comma(day_count),
    "total_all": comma(total_all),
    "latest_date": latest_date,
    "latest_hhmm": latest_hhmm or "",
    "cities_all": cities_all,
    "calendar_html": calendar_html,
    "list_html": list_html,
}

page = re.sub(r"\{(\w+)\}", lambda m: str(_FIELDS.get(m.group(1), m.group(0))), PAGE)

if not SAMPLE.exists():
    raise SystemExit("归档样板不存在：%s（请确认 TEMPLATE_ARCHIVE_SAMPLE 配置）" % SAMPLE)
if not ARCHIVE.exists():
    raise SystemExit("归档数据不存在：%s（请先运行 run_daily.py 刷新归档数据）" % ARCHIVE)

OUT_DIR.mkdir(parents=True, exist_ok=True)
out = OUT_DIR / "index.html"
out.write_text(page, encoding="utf-8")

# ---------- 校验 ----------
print("样板 MD5 生成前:", sample_md5_before)
print("样板 MD5 生成后:", md5(SAMPLE), "(未修改)" if md5(SAMPLE) == sample_md5_before else "(!!! 被修改 !!!)")
print("输出:", out, "%.1f KB" % (out.stat().st_size / 1024))
print("未替换占位符:", sorted(set(re.findall(r"\{[a-z_]+\}", page))))
print("月历卡片:", page.count('class="month-calendar-card"'), "| 可点日期:", page.count("cal-cell-active"),
      "| 置灰日期:", page.count("cal-cell-disabled"))
print("清单行:", page.count("history-row-item"), "| 类别标签:", page.count('class="cat-tag"'))
print("交叉链接: 指向单日页", len(re.findall(r'href="\./\d{4}-\d{2}-\d{2}\.html"', page)))
print("div balance:", page.count("<div"), page.count("</div>"))
