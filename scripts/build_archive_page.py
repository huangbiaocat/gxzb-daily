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
try:
    from scripts.overtime_helper import is_overtime_publication
except ImportError:
    from overtime_helper import is_overtime_publication
import config  # noqa: E402

# 归档首页样板（视觉体系来源）与数据源、产物目录全部走配置，无硬编码路径
SAMPLE = config.TEMPLATE_ARCHIVE_SAMPLE
ARCHIVE = config.DATA_DIR / "archive.json"
OUT_DIR = config.SITE_DIR

def rebuild_archive_json():
    """全量扫描 daily 目录，彻底重新生成 archive.json，确保已删除日期被彻底清除，天数与总数绝对准确"""
    daily_dir = getattr(config, "DAILY_DIR", config.DATA_DIR / "daily")
    archive = {
        "site": "广西全区招投标公告日报",
        "subtitle": "广西公共资源交易 · 崇左阳光采购平台公告每日归档",
        "generated": config.now_stamp(),
        "day_count": 0,
        "total_all": 0,
        "days": []
    }
    days_list = []
    if daily_dir.exists():
        for f in sorted(daily_dir.glob("*.json"), reverse=True):
            day = f.stem
            if not (len(day) == 10 and day[4] == "-" and day[7] == "-"):
                continue
            try:
                rows = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(rows, list):
                    continue
                groups = {}
                for r in rows:
                    ind = r.get("industry", "") or "其他"
                    groups[ind] = groups.get(ind, 0) + 1
                valid_pubs = [r.get("pub_time") for r in rows if r.get("pub_time")]
                latest_pub = max(valid_pubs) if valid_pubs else ""
                final_file = config.STATE_DIR / f"final-{day}.json"
                is_past = (day < config.today())
                is_final = final_file.exists() or is_past
                finalized_at = ""
                if is_final:
                    if not final_file.exists() and is_past:
                        try:
                            final_file.parent.mkdir(parents=True, exist_ok=True)
                            fin_data = {
                                "date": day,
                                "is_final": True,
                                "finalized_at": config.now_stamp(),
                                "total_items": len(rows),
                                "sealed_reason": "past_date_auto_sealed"
                            }
                            final_file.write_text(json.dumps(fin_data, ensure_ascii=False, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                    try:
                        if final_file.exists():
                            fin_data = json.loads(final_file.read_text(encoding="utf-8"))
                            finalized_at = fin_data.get("finalized_at", "")
                    except Exception:
                        pass
                focus_count = sum(1 for r in rows if r.get("is_focus"))
                overtime_count = sum(
                    1 for r in rows
                    if is_overtime_publication(r.get("pub_time", ""))[0]
                )
                # 滞后公开数：严格统计发布日期归属于当天的公告中，被判定为滞后公开的条目数
                # 不再叠加 today_del（那是当天回扫捕获的历史前期公告，已回填至前期各自日期的 json 中）
                delayed_count = sum(1 for r in rows if r.get("is_delayed"))
                entry = {
                    "date": day,
                    "file": f"{day}.html",
                    "focus_count": focus_count,
                    "overtime_count": overtime_count,
                    "delayed_count": delayed_count,
                    "total": len(rows),
                    "cities": len({r.get("areaname", "") for r in rows if r.get("areaname")}),
                    "cat_count": len(groups),
                    "groups": groups,
                    "latest_pub": latest_pub,
                    "updated": config.now_stamp(),
                    "is_final": is_final,
                    "finalized_at": finalized_at
                }
                days_list.append(entry)
            except Exception as e:
                print(f"[警告] 读取每日归档文件 {f} 异常: {e}")
    days_list.sort(key=lambda d: d.get("date", ""), reverse=True)
    archive["days"] = days_list
    archive["day_count"] = len(days_list)
    archive["total_all"] = sum(int(d.get("total") or 0) for d in days_list)
    archive["total_focus"] = sum(int(d.get("focus_count") or 0) for d in days_list)
    archive["total_overtime"] = sum(int(d.get("overtime_count") or 0) for d in days_list)
    archive["total_delayed"] = sum(int(d.get("delayed_count") or 0) for d in days_list)
    archive["generated"] = config.now_stamp()
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_text(json.dumps(archive, ensure_ascii=False, indent=1), encoding="utf-8")
    return archive

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
            padding: 2px 7px;
            background: #dc2626;
            color: #ffffff;
            border-radius: 9999px;
            font-weight: 700;
            letter-spacing: 0.02em;
            box-shadow: 0 1px 2px rgba(220, 38, 38, 0.35);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            line-height: 1.2;
        }

        .chip-today {
            background: #fef2f2;
            color: #dc2626;
            border: 1px solid #fecaca;
            font-weight: 700;
        }

        .cal-pill-final {
            background: #ecfdf5;
            color: #065f46;
            border: 1px solid #a7f3d0;
            border-radius: 9999px;
            font-size: 9px;
            padding: 1px 5px;
            font-weight: 700;
        }

        .chip-final {
            background: #ecfdf5;
            color: #065f46;
            border: 1px solid #a7f3d0;
            font-weight: 700;
        }

        /* ===== 重点提醒胶囊样式 ===== */
        .chip-alert {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            background: #fef2f2;
            border: 1px solid #fecaca;
            color: #dc2626;
            padding: 3px 9px;
            border-radius: 9999px;
            font-size: 0.76rem;
            font-weight: 700;
            flex-shrink: 0;
            transition: all 0.15s ease;
        }
        .chip-alert.chip-zero {
            background: #f8fafc;
            border-color: #e2e8f0;
            color: #94a3b8;
            font-weight: 600;
        }
        .chip-delayed {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            background: #fff7ed;
            border: 1px solid #fed7aa;
            color: #ea580c;
            padding: 3px 9px;
            border-radius: 9999px;
            font-size: 0.76rem;
            font-weight: 700;
            flex-shrink: 0;
            transition: all 0.15s ease;
        }
        .cal-pill-delayed {
            font-size: 0.65rem;
            padding: 2px 7px;
            background: #ea580c;
            color: white;
            border-radius: 9999px;
            font-weight: 700;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            white-space: nowrap;
            box-shadow: 0 1px 2px rgba(234, 88, 12, 0.25);
            line-height: 1.2;
        }

        .cal-pill-group {
            display: flex;
            align-items: center;
            gap: 3px;
            flex-wrap: wrap;
            justify-content: flex-end;
        }

        .cal-pill-alert {
            font-size: 0.65rem;
            padding: 2px 7px;
            background: #dc2626;
            color: white;
            border-radius: 9999px;
            font-weight: 700;
            box-shadow: 0 1px 2px rgba(220, 38, 38, 0.3);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            white-space: nowrap;
            line-height: 1.2;
        }

        .cal-meta-focus {
            color: #dc2626;
            font-weight: 700;
        }
        .cal-meta-zero {
            color: var(--text-muted);
            font-weight: 500;
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
                justify-content: flex-start;
                gap: 8px;
                flex-wrap: wrap;
            }
        }

        /* ===== Admin Login Entry & Modal Styles ===== */
        .btn-admin-entry {
            background: #ffffff;
            color: #475569;
            border: 1px solid var(--border-color, #e2e8f0);
            box-shadow: var(--shadow-sm, 0 1px 2px 0 rgba(0, 0, 0, 0.05));
            margin-right: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .btn-admin-entry:hover {
            border-color: #0284c7;
            color: #0284c7;
            background: #f0f9ff;
        }
        .admin-modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.65);
            backdrop-filter: blur(5px);
            -webkit-backdrop-filter: blur(5px);
            z-index: 99999;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }
        .admin-modal-card {
            background: #ffffff;
            width: 100%;
            max-width: 410px;
            border-radius: 12px;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
            border: 1px solid #e2e8f0;
            padding: 26px 28px;
            position: relative;
            box-sizing: border-box;
            animation: adminModalIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes adminModalIn {
            from { opacity: 0; transform: scale(0.96) translateY(-10px); }
            to { opacity: 1; transform: scale(1) translateY(0); }
        }
        .admin-modal-close {
            position: absolute;
            top: 14px;
            right: 16px;
            background: none;
            border: none;
            font-size: 24px;
            line-height: 1;
            color: #94a3b8;
            cursor: pointer;
            padding: 4px;
            border-radius: 6px;
            transition: all 0.15s;
        }
        .admin-modal-close:hover {
            color: #0f172a;
            background: #f1f5f9;
        }
        .admin-modal-header {
            text-align: center;
            margin-bottom: 20px;
        }
        .admin-modal-icon {
            width: 48px;
            height: 48px;
            border-radius: 12px;
            background: #e0f2fe;
            color: #0284c7;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 12px;
        }
        .admin-modal-title {
            margin: 0 0 6px 0;
            font-size: 1.18rem;
            font-weight: 700;
            color: #0f172a;
        }
        .admin-modal-subtitle {
            margin: 0;
            font-size: 0.82rem;
            color: #64748b;
        }
        .admin-form-group {
            margin-bottom: 16px;
        }
        .admin-form-label {
            display: block;
            font-size: 0.84rem;
            font-weight: 600;
            color: #334155;
            margin-bottom: 6px;
        }
        .admin-input-wrapper {
            position: relative;
            display: flex;
            align-items: center;
        }
        .admin-form-input {
            width: 100%;
            height: 42px;
            padding: 0 42px 0 14px;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            font-size: 0.92rem;
            color: #0f172a;
            outline: none;
            box-sizing: border-box;
            transition: all 0.2s;
            background: #f8fafc;
        }
        .admin-form-input:focus {
            background: #ffffff;
            border-color: #0284c7;
            box-shadow: 0 0 0 3px rgba(2, 132, 199, 0.18);
        }
        .btn-toggle-pwd {
            position: absolute;
            right: 8px;
            background: none;
            border: none;
            color: #94a3b8;
            cursor: pointer;
            padding: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 6px;
        }
        .btn-toggle-pwd:hover {
            color: #475569;
        }
        .admin-login-tip {
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.82rem;
            margin-bottom: 14px;
            line-height: 1.4;
        }
        .tip-error {
            background: #fef2f2;
            color: #b91c1c;
            border: 1px solid #fecaca;
        }
        .tip-success {
            background: #f0fdf4;
            color: #15803d;
            border: 1px solid #bbf7d0;
        }
        .admin-btn-submit {
            width: 100%;
            height: 42px;
            background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
            color: #ffffff;
            border: none;
            border-radius: 8px;
            font-size: 0.92rem;
            font-weight: 600;
            cursor: pointer;
            box-shadow: 0 2px 4px rgba(2, 132, 199, 0.25);
            transition: all 0.15s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }
        .admin-btn-submit:hover:not(:disabled) {
            opacity: 0.95;
            box-shadow: 0 4px 8px rgba(2, 132, 199, 0.35);
        }
        .admin-btn-submit:disabled {
            opacity: 0.65;
            cursor: not-allowed;
        }
        .admin-modal-footer {
            margin-top: 16px;
            text-align: center;
        }
        .admin-direct-link {
            font-size: 0.78rem;
            color: #64748b;
            text-decoration: none;
            transition: color 0.15s;
        }
        .admin-direct-link:hover {
            color: #0284c7;
            text-decoration: underline;
        }

        /* ===== Auth & Favorites System Styles ===== */
        .auth-tab-bar {
            display: flex;
            background: #f1f5f9;
            padding: 3px;
            border-radius: 8px;
            margin-bottom: 18px;
            gap: 2px;
        }
        .auth-tab-btn {
            flex: 1;
            text-align: center;
            padding: 6px 10px;
            font-size: 0.8rem;
            font-weight: 600;
            color: #64748b;
            background: transparent;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.15s;
        }
        .auth-tab-btn.active {
            background: #ffffff;
            color: #0284c7;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }
        .fav-section-title {
            font-size: 0.82rem;
            font-weight: 700;
            color: #1e293b;
            margin: 12px 0 6px 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .fav-tag-list {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 8px;
        }
        .fav-chip {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 0.76rem;
            padding: 4px 8px;
            border-radius: 6px;
            font-weight: 500;
        }
        .fav-chip-project {
            background: #eff6ff;
            color: #1d4ed8;
            border: 1px solid #bfdbfe;
        }
        .fav-chip-owner {
            background: #fffbeb;
            color: #b45309;
            border: 1px solid #fde68a;
        }
        .fav-chip-del {
            cursor: pointer;
            color: #94a3b8;
            font-size: 0.85rem;
            line-height: 1;
            margin-left: 2px;
        }
        .fav-chip-del:hover {
            color: #ef4444;
        }
        .fav-empty-hint {
            font-size: 0.76rem;
            color: #94a3b8;
            font-style: italic;
            padding: 6px 0;
        }
        .fav-add-row {
            display: flex;
            gap: 6px;
            margin-top: 10px;
        }
        .fav-add-row input, .fav-add-row select {
            font-size: 0.78rem;
            padding: 6px 8px;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            outline: none;
        }
        .fav-add-row input:focus, .fav-add-row select:focus {
            border-color: #0284c7;
            box-shadow: 0 0 0 2px rgba(2, 132, 199, 0.15);
        }
        .fav-add-btn {
            background: #0284c7;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 0 12px;
            font-size: 0.78rem;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            transition: background 0.15s;
        }
        .fav-add-btn:hover {
            background: #0369a1;
        }

        /* ===== Responsive & Mobile Optimizations ===== */
        .btn-text-desktop { display: inline; }
        .btn-text-mobile { display: none; }

        @media (max-width: 640px) {
            .btn-text-desktop { display: none !important; }
            .btn-text-mobile { display: inline !important; }
            .btn-arrow-icon { display: none !important; }

            .site-header {
                position: sticky;
                top: 0;
                z-index: 100;
                background: rgba(255, 255, 255, 0.96);
                backdrop-filter: blur(8px);
                border-bottom: 1px solid var(--border-color, #e2e8f0);
            }
            .header-inner {
                height: auto !important;
                min-height: 52px;
                padding: 8px 12px !important;
                flex-wrap: wrap;
                gap: 8px;
                justify-content: space-between;
            }
            .brand-logo {
                gap: 8px;
                flex-shrink: 1;
                min-width: 0;
            }
            .brand-icon {
                width: 30px !important;
                height: 30px !important;
                border-radius: 8px !important;
            }
            .brand-icon svg {
                width: 17px !important;
                height: 17px !important;
            }
            .brand-text h1 {
                font-size: 0.95rem !important;
                font-weight: 700;
                line-height: 1.25;
                margin: 0;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            .brand-text p {
                display: none !important;
            }
            .header-actions {
                display: flex !important;
                align-items: center !important;
                gap: 5px !important;
                flex-shrink: 0 !important;
            }
            .btn-latest {
                padding: 6px 9px !important;
                font-size: 0.76rem !important;
                gap: 4px !important;
                border-radius: 6px !important;
                white-space: nowrap !important;
                margin-right: 0 !important;
                box-shadow: none !important;
            }
            .btn-latest svg {
                width: 13px !important;
                height: 13px !important;
            }

            .page-container {
                padding: 12px 10px 48px !important;
            }

            .hero-banner {
                padding: 14px 12px !important;
                margin-bottom: 12px !important;
                border-radius: 12px !important;
            }
            .hero-badge {
                font-size: 0.65rem !important;
                padding: 2px 7px !important;
                margin-bottom: 6px !important;
            }
            .hero-title {
                font-size: 1.2rem !important;
                line-height: 1.3 !important;
                margin-bottom: 4px !important;
            }
            .hero-desc {
                font-size: 0.78rem !important;
                line-height: 1.45 !important;
                margin-bottom: 10px !important;
            }

            /* Stats 2x2 grid on mobile */
            .stats-grid {
                grid-template-columns: repeat(2, 1fr) !important;
                gap: 8px !important;
                margin-top: 10px !important;
            }
            .stat-box {
                padding: 9px 10px !important;
                border-radius: 8px !important;
            }
            .sb-label {
                font-size: 0.7rem !important;
                margin-bottom: 2px !important;
            }
            .sb-value {
                font-size: 1.15rem !important;
            }
            .sb-unit {
                font-size: 0.72rem !important;
                margin-left: 2px !important;
            }

            /* Filter Section on mobile */
            .filter-section {
                padding: 10px !important;
                margin-bottom: 12px !important;
                border-radius: 10px !important;
                flex-direction: column !important;
                align-items: stretch !important;
                gap: 8px !important;
            }
            .filter-left {
                width: 100% !important;
                flex-direction: column !important;
                gap: 8px !important;
            }
            .search-input-wrap {
                width: 100% !important;
                min-width: 0 !important;
                max-width: 100% !important;
                position: relative;
            }
            .search-input {
                width: 100% !important;
                height: 36px !important;
                font-size: 0.8rem !important;
                padding-left: 32px !important;
                box-sizing: border-box !important;
            }
            .filter-right {
                width: 100% !important;
                justify-content: stretch !important;
            }
            .density-legend {
                display: none !important;
            }
            .view-switcher {
                width: 100% !important;
                display: flex !important;
                background: #f1f5f9;
                padding: 3px;
                border-radius: 8px;
            }
            .view-btn {
                flex: 1 !important;
                justify-content: center !important;
                padding: 7px 10px !important;
                font-size: 0.8rem !important;
                border-radius: 6px !important;
            }

            /* Month Calendar Card on mobile */
            .month-calendar-card {
                padding: 10px 6px !important;
                border-radius: 10px !important;
                margin-bottom: 12px !important;
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch !important;
                box-sizing: border-box !important;
            }
            .year-calendar-grid {
                padding: 8px 4px !important;
                gap: 12px !important;
            }

            .archive-hint {
                white-space: normal !important;
                word-break: break-all !important;
                line-height: 1.45 !important;
                font-size: 0.74rem !important;
                display: block !important;
                padding: 2px 2px !important;
            }

            .month-card-header {
                margin-bottom: 8px !important;
                padding-bottom: 8px !important;
                flex-direction: column !important;
                align-items: flex-start !important;
                gap: 6px !important;
            }
            .month-stats-group {
                width: 100% !important;
                display: flex !important;
                flex-wrap: wrap !important;
                gap: 4px !important;
            }
            .stat-pill {
                font-size: 0.68rem !important;
                padding: 2px 6px !important;
            }
            .cal-weekdays-row {
                display: grid !important;
                grid-template-columns: repeat(7, minmax(0, 1fr)) !important;
                gap: 2px !important;
                margin-bottom: 4px !important;
                padding: 0 !important;
                text-align: center !important;
            }
            .cal-weekdays-row span {
                padding: 3px 0 !important;
                font-size: 0.68rem !important;
                display: block !important;
                text-align: center !important;
            }
            .cal-grid-body {
                display: grid !important;
                grid-template-columns: repeat(7, minmax(0, 1fr)) !important;
                gap: 2px !important;
                width: 100% !important;
            }
            .cal-cell {
                min-height: 48px !important;
                padding: 2px 1px !important;
                border-radius: 4px !important;
                min-width: 0 !important;
                overflow: hidden !important;
                box-sizing: border-box !important;
                display: flex !important;
                flex-direction: column !important;
                justify-content: space-between !important;
            }
            .cal-cell-header {
                display: flex !important;
                justify-content: space-between !important;
                align-items: center !important;
                width: 100% !important;
                margin-bottom: 2px !important;
            }
            .cal-date-num {
                font-size: 0.76rem !important;
                font-weight: 700 !important;
                line-height: 1 !important;
            }
            .cal-pill-group {
                display: flex !important;
                align-items: center !important;
                gap: 2px !important;
            }
            .cal-pill-today {
                font-size: 0.5rem !important;
                padding: 1px 3px !important;
                border-radius: 3px !important;
                line-height: 1 !important;
            }
            .cal-pill-delayed, .cal-pill-final {
                font-size: 0.5rem !important;
                padding: 1px 2px !important;
                line-height: 1 !important;
            }
            .cal-cell-body {
                display: flex !important;
                flex-direction: column !important;
                align-items: stretch !important;
                justify-content: flex-end !important;
                gap: 1px !important;
                width: 100% !important;
                margin-top: auto !important;
            }
            .cal-count-badge {
                font-size: 0.60rem !important;
                padding: 1px 0 !important;
                text-align: center !important;
                border-radius: 3px !important;
                width: 100% !important;
                box-sizing: border-box !important;
                display: block !important;
                white-space: nowrap !important;
                line-height: 1.15 !important;
                letter-spacing: -0.2px !important;
            }
            .cal-count-badge .unit {
                font-size: 0.52rem !important;
            }
            .cal-meta-line {
                font-size: 0.52rem !important;
                text-align: center !important;
                line-height: 1.1 !important;
                display: block !important;
                width: 100% !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
                white-space: nowrap !important;
                margin-top: 1px !important;
            }
            .cal-meta-zero {
                display: none !important;
            }
            .cal-meta-focus {
                color: #dc2626 !important;
                font-weight: 800 !important;
                font-size: 0.52rem !important;
                background: rgba(220, 38, 38, 0.08) !important;
                border-radius: 2px !important;
                padding: 0 1px !important;
                white-space: nowrap !important;
            }

            /* History row list view on mobile */
            .history-row-item {
                padding: 10px 10px !important;
                margin-bottom: 8px !important;
                border-radius: 8px !important;
                gap: 8px !important;
            }
            .date-calendar-box {
                width: 42px !important;
                height: 42px !important;
                border-radius: 6px !important;
                flex-shrink: 0 !important;
            }
            .dc-month {
                font-size: 0.62rem !important;
            }
            .dc-day {
                font-size: 1.1rem !important;
            }
            .dm-primary {
                font-size: 0.85rem !important;
            }
            .dm-secondary {
                display: none !important;
            }
            .cat-tag {
                font-size: 0.68rem !important;
                padding: 2px 5px !important;
            }
            .item-col-metrics {
                width: 100% !important;
                justify-content: space-between !important;
                padding-top: 6px !important;
                border-top: 1px dashed #e2e8f0 !important;
                margin-top: 4px !important;
            }
            .metric-chip {
                font-size: 0.7rem !important;
                padding: 2px 5px !important;
            }
            .metrics-visual .count-value {
                font-size: 1.1rem !important;
            }
            .action-arrow {
                display: none !important;
            }
        }

        /* ===== 年度分组折叠/展开样式 ===== */
        .calendar-years-wrapper {
            display: flex;
            flex-direction: column;
            gap: 20px;
            width: 100%;
        }

        .year-archive-group {
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            background: #ffffff;
            overflow: hidden;
            transition: all 0.25s ease;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
        }

        .year-archive-group.current-year {
            border-color: rgba(2, 132, 199, 0.35);
            box-shadow: 0 4px 12px -2px rgba(2, 132, 199, 0.08);
        }

        .year-group-summary {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 13px 20px;
            background: linear-gradient(180deg, #f8fafc, #f1f5f9);
            cursor: pointer;
            user-select: none;
            list-style: none;
            transition: background 0.2s ease;
        }

        .year-group-summary::-webkit-details-marker {
            display: none;
        }

        .year-group-summary:hover {
            background: linear-gradient(180deg, #f1f5f9, #e2e8f0);
        }

        .year-archive-group.current-year .year-group-summary {
            background: linear-gradient(180deg, #f0f9ff, #e0f2fe);
        }

        .year-summary-left {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }

        .year-title {
            font-size: 1.12rem;
            font-weight: 700;
            color: #0f172a;
            letter-spacing: -0.01em;
        }

        .year-badge {
            font-size: 0.72rem;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 9999px;
            line-height: 1.4;
        }

        .year-badge-current {
            background: #0284c7;
            color: #ffffff;
        }

        .year-badge-past {
            background: #64748b;
            color: #ffffff;
        }

        .year-stat-text {
            font-size: 0.82rem;
            color: #64748b;
            margin-left: 4px;
        }

        .year-summary-right {
            display: flex;
            align-items: center;
            gap: 8px;
            color: #64748b;
            font-size: 0.82rem;
        }

        .year-toggle-arrow {
            width: 18px;
            height: 18px;
            stroke: currentColor;
            stroke-width: 2.2;
            fill: none;
            stroke-linecap: round;
            stroke-linejoin: round;
            transition: transform 0.25s ease;
        }

        .year-archive-group[open] .year-toggle-arrow {
            transform: rotate(180deg);
        }

        .year-archive-group[open] .year-toggle-hint-open {
            display: inline;
        }
        .year-archive-group[open] .year-toggle-hint-closed {
            display: none;
        }
        .year-archive-group:not([open]) .year-toggle-hint-open {
            display: none;
        }
        .year-archive-group:not([open]) .year-toggle-hint-closed {
            display: inline;
        }

        .year-calendar-grid {
            padding: 20px;
            background: #f8fafc;
            border-top: 1px solid #e2e8f0;
        }
    </style>
"""

# ---------- 数据 ----------
rebuild_archive_json()
arc = json.loads(ARCHIVE.read_text(encoding="utf-8"))
days = sorted(arc["days"], key=lambda d: d["date"], reverse=True)
day_map = {d["date"]: d for d in days}
day_count = arc.get("day_count", len(days))
total_all = arc.get("total_all", sum(d["total"] for d in days))
latest = days[0] if days else {"date": "", "total": 0, "cities": 0, "cat_count": 0, "updated": ""}
latest_date = latest["date"]
latest_updated = latest.get("updated", "")
latest_pub_time = latest.get("latest_pub", "")
footer_time_info = (
    f"最新公告时间：{latest_pub_time} · 最近扫描时间：{latest_updated}"
    if latest_pub_time
    else f"最近扫描时间：{latest_updated}"
)

def extract_time_24h(updated_str: str) -> str:
    """提取 24 小时制时间 'HH:mm'（严格按时分解析，避免切片截成分秒）"""
    if not updated_str:
        return ""
    parts = updated_str.strip().split()
    time_part = parts[1] if len(parts) > 1 else parts[0]
    t_tokens = time_part.split(":")
    if len(t_tokens) >= 2:
        try:
            return f"{int(t_tokens[0]):02d}:{int(t_tokens[1]):02d}"
        except ValueError:
            return f"{t_tokens[0]}:{t_tokens[1]}"
    return time_part

latest_hhmm = extract_time_24h(latest_updated)
last_month = latest_date[:7] if latest_date else ""
cities_all = max((d.get("cities", 0) for d in days), default=0)

# 物化的月份卡片（数据驱动：cover 数据中出现的最多 3 个月份）
today_str = datetime.date.today().strftime("%Y-%m-%d")
current_ym = today_str[:7]
month_set = {d["date"][:7] for d in days}
month_set.add(current_ym)
months = sorted(month_set, reverse=True)

# ---------- 月历视图 ----------
month_cards = []
for ym in months:
    y, m = int(ym[:4]), int(ym[5:7])
    m_days = [d for d in days if d["date"][:7] == ym]
    m_total = sum(d["total"] for d in m_days)
    m_focus = sum(d.get("focus_count", 0) for d in m_days)
    cells = []
    lead = datetime.date(y, m, 1).weekday()
    cells.extend(['<div class="cal-cell cal-cell-empty"></div>'] * lead)
    ndays = calendar.monthrange(y, m)[1]
    for day in range(1, ndays + 1):
        ds = "%04d-%02d-%02d" % (y, m, day)
        is_today = (ds == today_str)
        today_cls = " cal-today" if is_today else ""
        if is_today:
            today_pill = '<span class="cal-pill-today">今日</span>'
        else:
            today_pill = ''
        if ds in day_map:
            d = day_map[ds]
            dens = density_of(d["total"])
            focus_cnt = d.get("focus_count", 0)
            delayed_cnt = d.get("delayed_count", 0)

            pills = []
            if delayed_cnt > 0:
                pills.append(f'<span class="cal-pill-delayed" title="滞后公开 {delayed_cnt} 条">滞后{delayed_cnt}</span>')
            if today_pill:
                pills.append(today_pill)
            pill_html = "".join(pills)

            f_cls = "cal-meta-focus" if focus_cnt > 0 else "cal-meta-zero"

            cells.append(
                '<a class="cal-cell cal-cell-active density-{dens}{today_cls}" data-count="{cnt}" data-cities="{cities}" data-focus="{focus_cnt}" data-date="{ds}" href="./{ds}.html" title="{ds}：收录 {cnt} 条（重点信息 {focus_cnt} 条）">\n'
                '<div class="cal-cell-header">\n'
                '<span class="cal-date-num">{day}</span>\n'
                '<div class="cal-pill-group">{pill_html}</div>\n'
                '</div>\n'
                '<div class="cal-cell-body">\n'
                '<span class="cal-count-badge">{cnt}<span class="unit">条</span></span>\n'
                '<span class="cal-meta-line"><span class="{f_cls}">重点 {focus_cnt}</span></span>\n'
                '</div>\n'
                '</a>'.format(dens=dens, cnt=comma(d["total"]), cities=d.get("cities", 0),
                              focus_cnt=focus_cnt,
                              f_cls=f_cls,
                              ds=ds, day=day, today_cls=today_cls, pill_html=pill_html)
            )
        else:
            cells.append(
                '<div class="cal-cell cal-cell-disabled{today_cls}" data-date="{ds}">\n'
                '<div class="cal-cell-header">\n'
                '<span class="cal-date-num">{day}</span>\n'
                '<div class="cal-pill-group">{today_pill}</div>\n'
                '</div>\n'
                '<div class="cal-cell-body">\n'
                '<span class="cal-no-data">-</span>\n'
                '</div>\n'
                '</div>'.format(day=day, ds=ds, today_cls=today_cls, today_pill=today_pill)
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
        '<span class="stat-pill alert-pill"><span class="label">重点信息:</span> <strong>{mfocus}</strong> 条</span>\n'
        '</div>\n'
        '</div>\n'
        '<div class="cal-weekdays-row">\n'
        '<span>周一</span><span>周二</span><span>周三</span><span>周四</span><span>周五</span><span class="wknd">周六</span><span class="wknd">周日</span>\n'
        '</div>\n'
        '<div class="cal-grid-body">\n{cells}\n</div>\n'
        '</div>'.format(
            ym=ym, y=y, m=m, mdays=len(m_days), mtotal=comma(m_total),
            mfocus=comma(m_focus),
            cells="\n".join(cells)
        )
    )

# 按年份分组构建折叠面板（本年展开，往年折叠）
current_year = today_str[:4]
years_in_data = sorted({ym[:4] for ym in months}, reverse=True)
if current_year not in years_in_data and years_in_data:
    current_year = years_in_data[0]

year_card_map = {}
for card in month_cards:
    m_ym = card.split('data-year-month="')[1][:7]
    yr = m_ym[:4]
    year_card_map.setdefault(yr, []).append(card)

year_sections = []
for yr in years_in_data:
    cards = year_card_map.get(yr, [])
    if not cards:
        continue
    is_curr = (yr == current_year)
    yr_days = [d for d in days if d["date"].startswith(yr)]
    yr_day_cnt = len(yr_days)
    yr_notices = sum(d["total"] for d in yr_days)
    yr_focus = sum(d.get("focus_count", 0) for d in yr_days)

    open_attr = " open" if is_curr else ""
    curr_cls = " current-year" if is_curr else " past-year"
    badge_html = (
        '<span class="year-badge year-badge-current">本年</span>'
        if is_curr else
        '<span class="year-badge year-badge-past">往年归档</span>'
    )
    stat_desc = f"{len(cards)} 个月份 · {yr_day_cnt} 天已归档 · {comma(yr_notices)} 条公告"
    if yr_focus > 0:
        stat_desc += f" · 重点关注 {comma(yr_focus)}"

    group_html = f"""<details class="year-archive-group{curr_cls}"{open_attr} data-year="{yr}">
  <summary class="year-group-summary">
    <div class="year-summary-left">
      <span class="year-title">{yr} 年度</span>
      {badge_html}
      <span class="year-stat-text">{stat_desc}</span>
    </div>
    <div class="year-summary-right">
      <span class="year-toggle-hint year-toggle-hint-open">点击折叠</span>
      <span class="year-toggle-hint year-toggle-hint-closed">点击展开</span>
      <svg class="year-toggle-arrow" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"></polyline></svg>
    </div>
  </summary>
  <div class="calendar-grid-container year-calendar-grid">
    {''.join(cards)}
  </div>
</details>"""
    year_sections.append(group_html)

calendar_html = "\n".join(year_sections)

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
    if is_today:
        today_chip = '<span class="status-chip chip-today">今日</span>'
    else:
        today_chip = ''
    row_today_cls = " history-row-today" if is_today else ""

    focus_cnt = d.get("focus_count", 0)
    f_chip_cls = "chip-alert" if focus_cnt > 0 else "chip-alert chip-zero"
    delayed_cnt = d.get("delayed_count", 0)
    delayed_chip_html = ""
    if delayed_cnt > 0:
        delayed_chip_html = (
            f'<span class="chip-delayed" title="历史回扫确证滞后公开 {delayed_cnt} 条">\n'
            f'<svg fill="none" height="12" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24" width="12"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 3"></path></svg>\n'
            f'<span>滞后公开 {delayed_cnt}</span>\n'
            f'</span>\n'
        )

    rows.append(
        '<a class="history-row-item{row_today_cls}" data-cities="{cities}" data-count="{cnt}" data-focus="{focus_cnt}" data-date="{ds}" data-month="{m}" data-year="{y}" href="./{ds}.html">\n'
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
        '{delayed_chip_html}'
        '<span class="{f_chip_cls}" title="当日重点信息 {focus_cnt} 条">\n'
        '<svg fill="currentColor" height="12" viewBox="0 0 24 24" width="12"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>\n'
        '<span>重点 {focus_cnt}</span>\n'
        '</span>\n'
        '<span class="metric-chip">{cities} 个地市</span>\n'
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
            focus_cnt=focus_cnt,
            f_chip_cls=f_chip_cls,
            delayed_chip_html=delayed_chip_html,
            cnt=comma(d["total"]), cities=d.get("cities", 0),
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
<h1 style="margin: 0;">广西全区招投标公告日报（{latest_date}）</h1>
<p>全区公共资源交易 · 历史归档与数据追溯中心</p>
</div>
</a>
<div class="header-actions">
<a class="btn-latest btn-user-entry" id="btnUserPortal" href="javascript:void(0)" onclick="openUserPortalModal()" style="background: #ffffff; color: #0284c7; border: 1px solid #bae6fd;">
<svg fill="none" height="16" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="16"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
<span class="btn-text-desktop" id="userBtnText">登录 / 重点关注</span>
<span class="btn-text-mobile" id="userBtnTextMobile">关注</span>
</a>
<a class="btn-latest btn-admin-entry" id="btnAdminLogin" href="javascript:void(0)" onclick="openAdminModal()">
<svg fill="none" height="16" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="16"><rect height="11" rx="2" ry="2" width="18" x="3" y="11"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>
<span class="btn-text-desktop" id="adminBtnText">管理后台</span>
<span class="btn-text-mobile" id="adminBtnTextMobile">管理</span>
</a>
<a class="btn-latest" id="btnGlobalSearch" href="./search.html" style="background: #ffffff; color: var(--primary); border: 1px solid var(--border-color); box-shadow: var(--shadow-sm);">
<svg fill="none" height="16" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="16"><circle cx="11" cy="11" r="8"></circle><line x1="21" x2="16.65" y1="21" y2="16.65"></line></svg>
<span class="btn-text-desktop">全站搜索</span>
<span class="btn-text-mobile">搜索</span>
</a>
<a class="btn-latest" id="btnTodayBrief" href="./{latest_date}.html" data-latest-date="{latest_date}" style="background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%); color: white; border-color: transparent; box-shadow: 0 4px 12px rgba(2, 132, 199, 0.25);">
<svg fill="none" height="16" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="16"><rect height="18" rx="2" ry="2" width="18" x="3" y="4"></rect><line x1="16" x2="16" y1="2" y2="6"></line><line x1="8" x2="8" y1="2" y2="6"></line><line x1="3" x2="21" y1="10" y2="10"></line></svg>
<span class="btn-text-desktop btn-latest-text">{latest_btn_text}</span>
<span class="btn-text-mobile btn-latest-mobile-text">今日简报</span>
<svg class="btn-arrow-icon" fill="none" height="16" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="16"><polyline points="9 18 15 12 9 6"></polyline></svg>
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
<div class="sb-value" style="font-size: 1.35rem;" title="最近更新时间：{latest_updated}（24小时制）">{latest_date}<span class="sb-unit">{latest_hhmm}</span></div>
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
<div class="search-input-wrap" style="position: relative; max-width: 320px;">
<svg class="search-icon" fill="none" height="16" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="16"><circle cx="11" cy="11" r="8"></circle><line x1="21" x2="16.65" y1="21" x2="16.65"></line></svg>
<input class="search-input" id="globalProjectSearch" placeholder="全站搜项目/业主/标段（回车直达）..." type="text" onkeydown="if(event.key==='Enter'&&this.value.trim()){window.location.href='./search.html?q='+encodeURIComponent(this.value.trim());}"/>
<button type="button" onclick="const v=document.getElementById('globalProjectSearch').value.trim();if(v){window.location.href='./search.html?q='+encodeURIComponent(v);}" style="position: absolute; right: 5px; top: 50%; transform: translateY(-50%); background: var(--primary); color: #fff; border: none; border-radius: 6px; font-size: 11px; padding: 4px 10px; cursor: pointer; font-weight: 500;">全站搜</button>
</div>
<div class="search-input-wrap">
<svg class="search-icon" fill="none" height="16" stroke="currentColor" stroke-width="2" viewbox="0 0 24 24" width="16"><circle cx="11" cy="11" r="8"></circle><line x1="21" x2="16.65" y1="21" y2="16.65"></line></svg>
<input class="search-input" id="archiveSearch" placeholder="输入日期检索（如 2026-09、09-10、周四）..." type="text"/>
</div>
</div>
<div class="filter-right">
<div class="density-legend">
<span>发布热度:</span>
<span class="legend-dot density-0" title="0条"></span>
<span class="legend-dot density-1" title="1-60条"></span>
<span class="legend-dot density-2" title="61-160条"></span>
<span class="legend-dot density-3" title="161-240条"></span>
<span class="legend-dot density-4" title="&gt;240条"></span>
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
<span class="archive-hint" id="archiveHint">共 <strong>{day_count}</strong> 个简报日 · 累计 <strong>{total_all}</strong> 条公告 · 重点信息 <strong>{total_focus}</strong> 条 · 最近更新 {latest_date}</span>
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
<div class="calendar-years-wrapper">
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
<p>广西公共资源交易平台体系自动化监控系统 · 历史归档与数据追溯中心</p>
<p style="margin-top: 6px;">{footer_time_info} · 由自动化采集监控系统 {app_version} 生成</p>
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

    // 2. 日历网格：根据客户端实际今日动态定位并高亮（支持已采集和待采集日期）
    var targetCell = document.querySelector('.cal-cell[data-date="' + todayStr + '"]');
    if (targetCell) {
        targetCell.classList.add('cal-today');
        var pillGroup = targetCell.querySelector('.cal-pill-group');
        if (!pillGroup) {
            var header = targetCell.querySelector('.cal-cell-header');
            if (header) {
                pillGroup = document.createElement('div');
                pillGroup.className = 'cal-pill-group';
                header.appendChild(pillGroup);
            }
        }
        if (pillGroup && !pillGroup.querySelector('.cal-pill-today')) {
            var pill = document.createElement('span');
            pill.className = 'cal-pill-today';
            pill.textContent = '今日';
            pill.style.animation = 'pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite';
            pillGroup.appendChild(pill);
        }
    }

    // 3. 清单视图高亮：精准定位 .dm-primary 并动态追加今日标签
    var listRow = document.querySelector('.history-row-item[data-date="' + todayStr + '"]');
    if (listRow) {
        listRow.classList.add('history-row-today');
        var primaryElem = listRow.querySelector('.dm-primary');
        if (primaryElem && !primaryElem.querySelector('.chip-today')) {
            var chip = document.createElement('span');
            chip.className = 'status-chip chip-today';
            chip.textContent = '今日';
            primaryElem.appendChild(chip);
        }
    }

    // 4. 顶栏按钮动态同步：今日有简报则跳今日，今日未生成则跳最新并注明
    var btnLatest = document.getElementById('btnTodayBrief');
    if (btnLatest) {
        var btnText = btnLatest.querySelector('.btn-latest-text') || btnLatest.querySelector('span');
        var activeToday = document.querySelector('.cal-cell-active[data-date="' + todayStr + '"]');
        if (activeToday) {
            btnLatest.setAttribute('href', './' + todayStr + '.html');
            if (btnText) btnText.textContent = '查看今日简报 (' + todayStr + ')';
        } else {
            var latestDate = btnLatest.getAttribute('data-latest-date') || '';
            if (btnText) {
                btnText.textContent = latestDate ? ('查看最新简报 (' + latestDate + ')') : '查看最新简报';
            }
        }
    }
})();

// 搜索逻辑增强：当检索命中往年月份时自动展开对应年份，清除搜索时恢复默认折叠状态
(function() {
    var searchInput = document.getElementById('archiveSearch');
    if (searchInput) {
        searchInput.addEventListener('input', function(e) {
            var query = e.target.value.trim().toLowerCase();
            var yearGroups = document.querySelectorAll('.year-archive-group');
            if (!query) {
                yearGroups.forEach(function(grp) {
                    grp.open = grp.classList.contains('current-year');
                });
            } else {
                yearGroups.forEach(function(grp) {
                    var visibleCards = grp.querySelectorAll('.month-calendar-card:not([style*="display: none"])');
                    if (visibleCards.length > 0) {
                        grp.open = true;
                    }
                });
            }
        });
    }
})();
</script>
{admin_modal_html}
{admin_modal_script}
</body>
</html>
"""

admin_modal_html = """
<!-- 多身份统一鉴权弹窗 (普通用户登录 / 免费注册 / 管理员通道) -->
<div id="authModal" class="admin-modal-overlay" style="display: none;" onclick="handleAuthModalBackdrop(event)">
  <div class="admin-modal-card">
    <button type="button" class="admin-modal-close" onclick="closeAuthModal()" aria-label="关闭">&times;</button>
    <div class="admin-modal-header">
      <div class="admin-modal-icon">
        <svg fill="none" height="24" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="24"><path d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path></svg>
      </div>
      <h3 class="admin-modal-title" id="authModalTitle">用户登录与个性化关注</h3>
      <p class="admin-modal-subtitle" id="authModalSubtitle">建立专属重点项目与重点业主跟踪库</p>
    </div>

    <!-- 顶部分栏切换 -->
    <div class="auth-tab-bar">
      <button type="button" class="auth-tab-btn active" id="tabBtnUserLogin" onclick="switchAuthTab('login')">用户登录</button>
      <button type="button" class="auth-tab-btn" id="tabBtnUserRegister" onclick="switchAuthTab('register')">免费注册</button>
      <button type="button" class="auth-tab-btn" id="tabBtnAdminPortal" onclick="switchAuthTab('admin')">管理后台通道</button>
    </div>

    <!-- Tab 1: 普通用户登录 -->
    <form id="formUserLogin" class="admin-modal-form" onsubmit="handleUserLogin(event)">
      <div class="admin-form-group">
        <label for="userLoginUsername" class="admin-form-label">用户账号</label>
        <div class="admin-input-wrapper">
          <input type="text" id="userLoginUsername" class="admin-form-input" placeholder="请输入您的账号" autocomplete="username" required />
        </div>
      </div>
      <div class="admin-form-group">
        <label for="userLoginPassword" class="admin-form-label">登录密码</label>
        <div class="admin-input-wrapper">
          <input type="password" id="userLoginPassword" class="admin-form-input" placeholder="请输入密码" autocomplete="current-password" required />
        </div>
      </div>
      <div id="userLoginTip" class="admin-login-tip" style="display: none;"></div>
      <button type="submit" id="btnUserLoginSubmit" class="admin-btn-submit">
        <svg fill="none" height="16" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="16"><path d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1"></path></svg>
        <span>登录账号</span>
      </button>
    </form>

    <!-- Tab 2: 普通用户注册 -->
    <form id="formUserRegister" class="admin-modal-form" style="display: none;" onsubmit="handleUserRegister(event)">
      <div class="admin-form-group">
        <label for="userRegUsername" class="admin-form-label">注册账号 (3-32位字符)</label>
        <div class="admin-input-wrapper">
          <input type="text" id="userRegUsername" class="admin-form-input" placeholder="英文字母、数字或手机号" autocomplete="username" required />
        </div>
      </div>
      <div class="admin-form-group">
        <label for="userRegDisplayName" class="admin-form-label">姓名 / 称呼 (可选)</label>
        <div class="admin-input-wrapper">
          <input type="text" id="userRegDisplayName" class="admin-form-input" placeholder="例如：张经理 / 华西项目部" />
        </div>
      </div>
      <div class="admin-form-group">
        <label for="userRegPassword" class="admin-form-label">设置密码 (至少6位)</label>
        <div class="admin-input-wrapper">
          <input type="password" id="userRegPassword" class="admin-form-input" placeholder="请设置安全密码" autocomplete="new-password" required />
        </div>
      </div>
      <div id="userRegTip" class="admin-login-tip" style="display: none;"></div>
      <button type="submit" id="btnUserRegSubmit" class="admin-btn-submit" style="background: linear-gradient(135deg, #10b981 0%, #059669 100%);">
        <svg fill="none" height="16" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="16"><path d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z"></path></svg>
        <span>立即注册开通独立关注库</span>
      </button>
    </form>

    <!-- Tab 3: 管理员通道 -->
    <form id="formAdminPortal" class="admin-modal-form" style="display: none;" onsubmit="handleAdminLogin(event)">
      <div class="admin-form-group">
        <label for="adminPasswordInput" class="admin-form-label">管理员密码</label>
        <div class="admin-input-wrapper">
          <input type="password" id="adminPasswordInput" class="admin-form-input" placeholder="请输入管理员访问密码" autocomplete="current-password" required />
          <button type="button" class="btn-toggle-pwd" onclick="togglePasswordVisibility()" title="显示/隐藏密码">
            <svg id="eyeIcon" fill="none" height="16" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="16"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
          </button>
        </div>
      </div>
      <div id="adminLoginTip" class="admin-login-tip" style="display: none;"></div>
      <button type="submit" id="btnAdminSubmit" class="admin-btn-submit">
        <svg fill="none" height="16" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="16"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"></path><polyline points="10 17 15 12 10 7"></polyline><line x1="15" y1="12" x2="3" y2="12"></line></svg>
        <span id="btnAdminSubmitText">安全登录并进入后台</span>
      </button>
      <div class="admin-modal-footer">
        <a href="/admin/" class="admin-direct-link">局域网/免密环境？直接跳转管理后台 &rarr;</a>
      </div>
    </form>
  </div>
</div>

<!-- 用户专属重点跟踪规则与配置中心 (个性化重点项目 / 重点业主 / 预警关键词 / 历史快照) -->
<div id="favoritesModal" class="admin-modal-overlay" style="display: none;" onclick="handleFavModalBackdrop(event)">
  <div class="admin-modal-card" style="max-width: 860px; width: 95%;">
    <button type="button" class="admin-modal-close" onclick="closeFavoritesModal()" aria-label="关闭">&times;</button>
    <div class="admin-modal-header" style="text-align: left; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #f1f5f9; padding-bottom: 12px; margin-bottom: 14px;">
      <div>
        <h3 class="admin-modal-title" style="margin: 0; font-size: 1.15rem; color: #0f172a; display: flex; align-items: center; gap: 8px;">
          <span>🎯</span>
          <span>我的重点跟踪规则配置中心</span>
          <span id="favUserBadge" style="font-size: 0.78rem; font-weight: 600; padding: 2px 8px; border-radius: 9999px; background: #eff6ff; color: #2563eb; border: 1px solid #bfdbfe;"></span>
        </h3>
        <p class="admin-modal-subtitle" style="margin: 4px 0 0; font-size: 0.82rem; color: #64748b;">
          已启用专属跟踪：登录后标讯将按您的独立配置实时匹配与标记，不再沿用系统默认规则
        </p>
      </div>
      <div>
        <button type="button" onclick="handleUserLogout()" class="fav-action-btn" style="color: #ef4444; border: 1px solid #fecaca; background: #fff5f5; font-size: 0.78rem; padding: 5px 12px; border-radius: 6px; cursor: pointer; font-weight: 500;">退出登录</button>
      </div>
    </div>

    <!-- 顶部四项子功能切换 -->
    <div class="auth-tab-bar" style="margin-bottom: 16px; border-radius: 8px;">
      <button type="button" class="auth-tab-btn active" id="favTabBtnProjects" onclick="switchFavTab('projects')">
        <span>1 重点跟踪项目 (<span id="favProjectsCount">0</span>)</span>
      </button>
      <button type="button" class="auth-tab-btn" id="favTabBtnOwners" onclick="switchFavTab('owners')">
        <span>2 重点跟踪业主 (<span id="favOwnersCount">0</span>)</span>
      </button>
      <button type="button" class="auth-tab-btn" id="favTabBtnAlerts" onclick="switchFavTab('alerts')">
        <span>3 通用预警 ＆ 金额门槛</span>
      </button>
      <button type="button" class="auth-tab-btn" id="favTabBtnSnapshots" onclick="switchFavTab('snapshots')">
        <span>4 历史配置快照 (<span id="favSnapshotsCount">0</span>)</span>
      </button>
    </div>

    <!-- Tab 1: 重点跟踪项目清单 (FOCUS_PROJECTS) -->
    <div id="favPanelProjects" class="fav-tab-panel">
      <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 0.82rem; color: #475569; line-height: 1.5;">
        <strong style="color: #0284c7;">【独立强规则】</strong> 只要标讯标题命中指定项目名称，即无条件判定为重点标讯（不受金额门槛限制）。已支持智能模糊匹配与分词匹配。
      </div>
      <div class="fav-quick-add-row" style="display: flex; gap: 8px; margin-bottom: 10px;">
        <input type="text" id="favSingleProjectInput" placeholder="输入单个重点项目名称（例如：平陆运河工程建设）" style="flex: 1; padding: 7px 12px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 0.85rem;" onkeydown="if(event.key==='Enter'){event.preventDefault();addSingleProject();}" />
        <button type="button" onclick="addSingleProject()" class="fav-add-btn" style="padding: 7px 14px; background: #0284c7; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: 600;">+ 添加项目</button>
      </div>
      <div style="margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between;">
        <span style="font-size: 0.82rem; font-weight: 600; color: #334155;">当前项目清单 (支持标签点击 × 快速删除，或切换批量编辑)：</span>
        <button type="button" id="btnToggleBatchProjects" onclick="toggleBatchEdit('projects')" style="font-size: 0.78rem; color: #0284c7; background: none; border: none; cursor: pointer; text-decoration: underline;">切换批量文本编辑</button>
      </div>
      <div id="favProjectsTagContainer" class="fav-tag-list" style="max-height: 200px; overflow-y: auto; padding: 10px; background: #fafafa; border: 1px solid #e5e7eb; border-radius: 6px; display: flex; flex-wrap: wrap; gap: 6px;"></div>
      <div id="favProjectsBatchContainer" style="display: none; margin-top: 6px;">
        <textarea id="favProjectsTextarea" rows="8" placeholder="每行一个重点项目名称，或使用逗号分隔" style="width: 100%; box-sizing: border-box; padding: 8px; font-family: monospace; font-size: 0.83rem; border: 1px solid #cbd5e1; border-radius: 6px;"></textarea>
      </div>
    </div>

    <!-- Tab 2: 重点跟踪业主名单 (FOCUS_OWNERS) -->
    <div id="favPanelOwners" class="fav-tab-panel" style="display: none;">
      <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 0.82rem; color: #475569; line-height: 1.5;">
        <strong style="color: #0284c7;">【独立强规则】</strong> 当招标人、业主单位或标讯标题包含指定业主单位全称或简称时，直接判定为重点标讯（不受金额门槛限制）。
      </div>
      <div class="fav-quick-add-row" style="display: flex; gap: 8px; margin-bottom: 10px;">
        <input type="text" id="favSingleOwnerInput" placeholder="输入单个业主单位全称或简称（例如：南宁轨道交通集团有限公司）" style="flex: 1; padding: 7px 12px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 0.85rem;" onkeydown="if(event.key==='Enter'){event.preventDefault();addSingleOwner();}" />
        <button type="button" onclick="addSingleOwner()" class="fav-add-btn" style="padding: 7px 14px; background: #0284c7; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: 600;">+ 添加业主</button>
      </div>
      <div style="margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between;">
        <span style="font-size: 0.82rem; font-weight: 600; color: #334155;">当前业主名单 (支持点击 × 快速删除，或切换批量编辑)：</span>
        <button type="button" id="btnToggleBatchOwners" onclick="toggleBatchEdit('owners')" style="font-size: 0.78rem; color: #0284c7; background: none; border: none; cursor: pointer; text-decoration: underline;">切换批量文本编辑</button>
      </div>
      <div id="favOwnersTagContainer" class="fav-tag-list" style="max-height: 200px; overflow-y: auto; padding: 10px; background: #fafafa; border: 1px solid #e5e7eb; border-radius: 6px; display: flex; flex-wrap: wrap; gap: 6px;"></div>
      <div id="favOwnersBatchContainer" style="display: none; margin-top: 6px;">
        <textarea id="favOwnersTextarea" rows="8" placeholder="每行一个业主名称，或使用逗号分隔" style="width: 100%; box-sizing: border-box; padding: 8px; font-family: monospace; font-size: 0.83rem; border: 1px solid #cbd5e1; border-radius: 6px;"></textarea>
      </div>
    </div>

    <!-- Tab 3: 常规通用预警关键词 ＆ 激活金额门槛 -->
    <div id="favPanelAlerts" class="fav-tab-panel" style="display: none;">
      <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 0.82rem; color: #475569; line-height: 1.5;">
        <strong style="color: #f59e0b;">【通用预警门槛联动】</strong> 当标讯标题命中通用预警关键词，且标讯金额达到或超过激活门槛时，触发大额预警判定。
      </div>
      <div style="margin-bottom: 14px;">
        <label style="display: block; font-size: 0.84rem; font-weight: 600; color: #334155; margin-bottom: 6px;">激活金额门槛 (单位：万元)：</label>
        <div style="display: flex; align-items: center; gap: 8px;">
          <input type="number" id="favAlertMinAmount" step="10" min="0" placeholder="1000" style="width: 160px; padding: 7px 12px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 0.85rem;" />
          <span style="font-size: 0.82rem; color: #64748b;">万元及以上（设为 0 表示不限制金额，命中关键词即预警）</span>
        </div>
      </div>
      <div>
        <label style="display: block; font-size: 0.84rem; font-weight: 600; color: #334155; margin-bottom: 6px;">通用预警关键词列表 (一行一个或逗号分隔)：</label>
        <textarea id="favAlertKeywordsTextarea" rows="5" placeholder="EPC, 设计施工总承包, 特许经营, 产业园, 智慧, 全过程工程咨询" style="width: 100%; box-sizing: border-box; padding: 8px; font-size: 0.83rem; border: 1px solid #cbd5e1; border-radius: 6px;"></textarea>
      </div>
    </div>

    <!-- Tab 4: 历史配置快照 -->
    <div id="favPanelSnapshots" class="fav-tab-panel" style="display: none;">
      <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 0.82rem; color: #475569; line-height: 1.5;">
        <strong style="color: #6366f1;">【安全快照与回滚机制】</strong> 每次保存都会自动生成专属历史版本快照。如需恢复先前的配置，点击对应版本右侧的「一键回滚」即可。
      </div>
      <div id="favSnapshotsListContainer" style="max-height: 260px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px;">
        <div class="fav-empty-hint">暂无历史配置快照</div>
      </div>
    </div>

    <!-- 底部操作栏与保存 -->
    <div style="margin-top: 16px; padding-top: 14px; border-top: 1px solid #f1f5f9; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;">
      <div style="flex: 1; min-width: 220px;">
        <input type="text" id="favSaveNoteInput" placeholder="修改说明/备注 (可选，例如: 增加五象智慧交通规则)" style="width: 100%; box-sizing: border-box; padding: 7px 10px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 0.82rem;" />
      </div>
      <div style="display: flex; gap: 8px;">
        <button type="button" onclick="closeFavoritesModal()" class="admin-btn-cancel" style="padding: 7px 14px; font-size: 0.83rem; background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; border-radius: 6px; cursor: pointer;">关闭</button>
        <button type="button" id="btnSaveFullConfig" onclick="saveUserFullConfigSubmit()" style="padding: 7px 18px; font-size: 0.83rem; font-weight: 600; background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); color: #fff; border: none; border-radius: 6px; cursor: pointer;">💾 保存专属规则并生成快照</button>
      </div>
    </div>
    <div id="favActionTip" class="admin-login-tip" style="display: none; margin-top: 10px;"></div>
  </div>
</div>
"""

admin_modal_script = """
<script>
var currentUserState = null;
var isAdminState = false;

function openUserPortalModal() {
    if (currentUserState && currentUserState.username) {
        openFavoritesModal();
    } else {
        openAuthModal('login');
    }
}

function openAdminModal() {
    if (isAdminState) {
        window.location.href = '/admin/';
        return;
    }
    openAuthModal('admin');
}

function openAuthModal(defaultTab) {
    var modal = document.getElementById('authModal');
    if (!modal) return;
    modal.style.display = 'flex';
    switchAuthTab(defaultTab || 'login');
}

function closeAuthModal() {
    var modal = document.getElementById('authModal');
    if (modal) modal.style.display = 'none';
}

function handleAuthModalBackdrop(e) {
    if (e.target.id === 'authModal') {
        closeAuthModal();
    }
}

function openFavoritesModal() {
    var modal = document.getElementById('favoritesModal');
    if (!modal) return;
    modal.style.display = 'flex';
    var userBadge = document.getElementById('favUserBadge');
    if (userBadge && currentUserState) {
        userBadge.innerText = (currentUserState.display_name || currentUserState.username) + ' (@' + currentUserState.username + ')';
    }
    loadUserFullConfig();
}

function closeFavoritesModal() {
    var modal = document.getElementById('favoritesModal');
    if (modal) modal.style.display = 'none';
}

function handleFavModalBackdrop(e) {
    if (e.target.id === 'favoritesModal') {
        closeFavoritesModal();
    }
}

function switchAuthTab(tab) {
    var btnLogin = document.getElementById('tabBtnUserLogin');
    var btnReg = document.getElementById('tabBtnUserRegister');
    var btnAdmin = document.getElementById('tabBtnAdminPortal');
    var formLogin = document.getElementById('formUserLogin');
    var formReg = document.getElementById('formUserRegister');
    var formAdmin = document.getElementById('formAdminPortal');
    var title = document.getElementById('authModalTitle');
    var subtitle = document.getElementById('authModalSubtitle');

    [btnLogin, btnReg, btnAdmin].forEach(function(b) { if (b) b.classList.remove('active'); });
    [formLogin, formReg, formAdmin].forEach(function(f) { if (f) f.style.display = 'none'; });

    if (tab === 'register') {
        if (btnReg) btnReg.classList.add('active');
        if (formReg) formReg.style.display = 'block';
        if (title) title.innerText = '免费注册专属账户';
        if (subtitle) subtitle.innerText = '开设独立重点项目与重点业主关注跟踪库';
        var rInput = document.getElementById('userRegUsername');
        if (rInput) setTimeout(function() { rInput.focus(); }, 100);
    } else if (tab === 'admin') {
        if (btnAdmin) btnAdmin.classList.add('active');
        if (formAdmin) formAdmin.style.display = 'block';
        if (title) title.innerText = '管理员安全登录';
        if (subtitle) subtitle.innerText = '直达系统运维调度与用户审计控制后台';
        var aInput = document.getElementById('adminPasswordInput');
        if (aInput) setTimeout(function() { aInput.focus(); }, 100);
    } else {
        if (btnLogin) btnLogin.classList.add('active');
        if (formLogin) formLogin.style.display = 'block';
        if (title) title.innerText = '用户登录';
        if (subtitle) subtitle.innerText = '登录后可查看和管理您关注的重点标段和业主';
        var lInput = document.getElementById('userLoginUsername');
        if (lInput) setTimeout(function() { lInput.focus(); }, 100);
    }
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeAuthModal();
        closeFavoritesModal();
    }
});

function togglePasswordVisibility() {
    var input = document.getElementById('adminPasswordInput');
    if (!input) return;
    input.type = (input.type === 'password') ? 'text' : 'password';
}

async function handleUserLogin(e) {
    e.preventDefault();
    var uInput = document.getElementById('userLoginUsername');
    var pInput = document.getElementById('userLoginPassword');
    var tip = document.getElementById('userLoginTip');
    var btn = document.getElementById('btnUserLoginSubmit');
    if (!uInput || !pInput) return;

    btn.disabled = true;
    if (tip) tip.style.display = 'none';

    try {
        var res = await fetch('/api/user/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: uInput.value.trim(), password: pInput.value.trim() })
        });
        var data = await res.json();
        if (data.ok) {
            currentUserState = data.user;
            updateUserPortalUI(data.user);
            closeAuthModal();
            openFavoritesModal();
        } else {
            if (tip) {
                tip.className = 'admin-login-tip tip-error';
                tip.innerText = data.msg || '登录失败，请检查账号密码';
                tip.style.display = 'block';
            }
        }
    } catch (err) {
        if (tip) {
            tip.className = 'admin-login-tip tip-error';
            tip.innerText = '连接服务器失败: ' + err.message;
            tip.style.display = 'block';
        }
    } finally {
        btn.disabled = false;
    }
}

async function handleUserRegister(e) {
    e.preventDefault();
    var uInput = document.getElementById('userRegUsername');
    var dInput = document.getElementById('userRegDisplayName');
    var pInput = document.getElementById('userRegPassword');
    var tip = document.getElementById('userRegTip');
    var btn = document.getElementById('btnUserRegSubmit');
    if (!uInput || !pInput) return;

    btn.disabled = true;
    if (tip) tip.style.display = 'none';

    try {
        var res = await fetch('/api/user/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: uInput.value.trim(),
                display_name: dInput ? dInput.value.trim() : '',
                password: pInput.value.trim()
            })
        });
        var data = await res.json();
        if (data.ok) {
            currentUserState = data.user;
            updateUserPortalUI(data.user);
            closeAuthModal();
            openFavoritesModal();
        } else {
            if (tip) {
                tip.className = 'admin-login-tip tip-error';
                tip.innerText = data.msg || '注册失败';
                tip.style.display = 'block';
            }
        }
    } catch (err) {
        if (tip) {
            tip.className = 'admin-login-tip tip-error';
            tip.innerText = '请求服务失败: ' + err.message;
            tip.style.display = 'block';
        }
    } finally {
        btn.disabled = false;
    }
}

async function handleUserLogout() {
    try {
        await fetch('/api/user/logout', { method: 'POST' });
    } catch (e) {}
    currentUserState = null;
    updateUserPortalUI(null);
    closeFavoritesModal();
}

function updateUserPortalUI(user) {
    var btn = document.getElementById('btnUserPortal');
    var txt = document.getElementById('userBtnText');
    var mob = document.getElementById('userBtnTextMobile');
    var greeting = document.getElementById('favModalUserGreeting');

    if (user && user.username) {
        var name = user.display_name || user.username;
        if (txt) txt.innerText = '⭐ ' + name;
        if (mob) mob.innerText = '关注';
        if (btn) {
            btn.style.background = '#f0f9ff';
            btn.style.borderColor = '#0284c7';
            btn.style.color = '#0284c7';
        }
        if (greeting) greeting.innerText = '你好，' + name + ' (@' + user.username + ')！以下为您专属关注清单：';
    } else {
        if (txt) txt.innerText = '登录 / 重点关注';
        if (mob) mob.innerText = '关注';
        if (btn) {
            btn.style.background = '#ffffff';
            btn.style.borderColor = '#bae6fd';
            btn.style.color = '#0284c7';
        }
    }
}

var currentUserConfig = {
    focus_projects: [],
    focus_owners: [],
    alert_keywords: [],
    alert_min_amount: 1000.0,
    snapshots: []
};
var favActiveTab = 'projects';

function switchFavTab(tabKey) {
    favActiveTab = tabKey;
    var tabIds = ['projects', 'owners', 'alerts', 'snapshots'];
    tabIds.forEach(function(k) {
        var btn = document.getElementById('favTabBtn' + k.charAt(0).toUpperCase() + k.slice(1));
        var panel = document.getElementById('favPanel' + k.charAt(0).toUpperCase() + k.slice(1));
        if (btn) btn.classList.toggle('active', k === tabKey);
        if (panel) panel.style.display = (k === tabKey) ? 'block' : 'none';
    });
}

function toggleBatchEdit(type) {
    var isPrj = type === 'projects';
    var tagBox = document.getElementById(isPrj ? 'favProjectsTagContainer' : 'favOwnersTagContainer');
    var batchBox = document.getElementById(isPrj ? 'favProjectsBatchContainer' : 'favOwnersBatchContainer');
    var textarea = document.getElementById(isPrj ? 'favProjectsTextarea' : 'favOwnersTextarea');
    var btn = document.getElementById(isPrj ? 'btnToggleBatchProjects' : 'btnToggleBatchOwners');

    if (!batchBox || !tagBox || !textarea) return;
    var isBatchShowing = batchBox.style.display !== 'none';
    if (isBatchShowing) {
        // 从文本域同步回列表
        var text = textarea.value.replace(/\\r\\n/g, '\\n').replace(/，/g, ',');
        var list = [];
        if (text.indexOf('\\n') !== -1) {
            list = text.split('\\n').map(function(s) { return s.trim(); }).filter(Boolean);
        } else {
            list = text.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
        }
        // 去重
        var seen = {};
        var deduped = [];
        list.forEach(function(item) {
            if (!seen[item]) { seen[item] = true; deduped.push(item); }
        });
        if (isPrj) {
            currentUserConfig.focus_projects = deduped;
            renderFavProjectsUI();
        } else {
            currentUserConfig.focus_owners = deduped;
            renderFavOwnersUI();
        }
        batchBox.style.display = 'none';
        tagBox.style.display = 'flex';
        if (btn) btn.innerText = '切换批量文本编辑';
    } else {
        // 展开批量编辑
        var curList = isPrj ? currentUserConfig.focus_projects : currentUserConfig.focus_owners;
        textarea.value = curList.join('\\n');
        batchBox.style.display = 'block';
        tagBox.style.display = 'none';
        if (btn) btn.innerText = '完成文本编辑 (切回标签视图)';
    }
}

function renderFavProjectsUI() {
    var tagBox = document.getElementById('favProjectsTagContainer');
    var countEl = document.getElementById('favProjectsCount');
    var textarea = document.getElementById('favProjectsTextarea');
    var list = currentUserConfig.focus_projects || [];
    if (countEl) countEl.innerText = list.length;
    if (textarea) textarea.value = list.join('\\n');
    if (tagBox) {
        if (list.length === 0) {
            tagBox.innerHTML = '<div class="fav-empty-hint">暂未配置任何重点项目，可通过上方快速添加或批量导入</div>';
        } else {
            tagBox.innerHTML = list.map(function(p, idx) {
                return '<span class="fav-chip fav-chip-project">' +
                    escapeHtmlStr(p) +
                    '<span class="fav-chip-del" onclick="removeSingleProject(' + idx + ')" title="移除此项">&times;</span>' +
                    '</span>';
            }).join('');
        }
    }
}

function renderFavOwnersUI() {
    var tagBox = document.getElementById('favOwnersTagContainer');
    var countEl = document.getElementById('favOwnersCount');
    var textarea = document.getElementById('favOwnersTextarea');
    var list = currentUserConfig.focus_owners || [];
    if (countEl) countEl.innerText = list.length;
    if (textarea) textarea.value = list.join('\\n');
    if (tagBox) {
        if (list.length === 0) {
            tagBox.innerHTML = '<div class="fav-empty-hint">暂未配置任何重点业主单位，可通过上方快速添加或批量导入</div>';
        } else {
            tagBox.innerHTML = list.map(function(o, idx) {
                return '<span class="fav-chip fav-chip-owner">' +
                    escapeHtmlStr(o) +
                    '<span class="fav-chip-del" onclick="removeSingleOwner(' + idx + ')" title="移除此项">&times;</span>' +
                    '</span>';
            }).join('');
        }
    }
}

function renderFavAlertsUI() {
    var amtInput = document.getElementById('favAlertMinAmount');
    var kwArea = document.getElementById('favAlertKeywordsTextarea');
    if (amtInput) amtInput.value = (currentUserConfig.alert_min_amount !== undefined) ? currentUserConfig.alert_min_amount : 1000;
    if (kwArea) {
        var kws = currentUserConfig.alert_keywords || [];
        kwArea.value = kws.join(', ');
    }
}

function renderFavSnapshotsUI() {
    var container = document.getElementById('favSnapshotsListContainer');
    var countEl = document.getElementById('favSnapshotsCount');
    var snaps = currentUserConfig.snapshots || [];
    if (countEl) countEl.innerText = snaps.length;
    if (!container) return;
    if (snaps.length === 0) {
        container.innerHTML = '<div class="fav-empty-hint">暂无历史配置快照。保存当前配置后将自动生成第一份快照。</div>';
        return;
    }
    container.innerHTML = snaps.map(function(s) {
        var triggerBadge = '<span style="padding: 2px 6px; border-radius: 4px; font-size: 0.72rem; font-weight: 600; background: #e0f2fe; color: #0369a1;">手动保存</span>';
        if (s.trigger === 'rollback') {
            triggerBadge = '<span style="padding: 2px 6px; border-radius: 4px; font-size: 0.72rem; font-weight: 600; background: #fef3c7; color: #b45309;">快照回滚</span>';
        } else if (s.trigger === 'auto_save') {
            triggerBadge = '<span style="padding: 2px 6px; border-radius: 4px; font-size: 0.72rem; font-weight: 600; background: #f1f5f9; color: #475569;">自动备份</span>';
        }
        var noteHtml = s.note ? ('<div style="font-size: 0.78rem; color: #475569; margin-top: 4px;">备注: ' + escapeHtmlStr(s.note) + '</div>') : '';
        return '<div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; display: flex; align-items: center; justify-content: space-between; gap: 10px;">' +
            '<div style="flex: 1;">' +
                '<div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">' +
                    triggerBadge +
                    '<span style="font-size: 0.82rem; font-weight: 600; color: #1e293b;">' + escapeHtmlStr(s.created_at) + '</span>' +
                '</div>' +
                '<div style="font-size: 0.78rem; color: #64748b;">' +
                    '项目 ' + s.focus_projects_count + ' 项 · 业主 ' + s.focus_owners_count + ' 家 · 预警词 ' + s.alert_keywords_count + ' 个 · 门槛 ' + s.alert_min_amount + '万' +
                '</div>' +
                noteHtml +
            '</div>' +
            '<div style="display: flex; gap: 8px; align-items: center;">' +
                '<button type="button" onclick="handleRollbackSnapshot(\\'' + s.snapshot_id + '\\')" style="padding: 5px 12px; font-size: 0.78rem; font-weight: 600; background: #f8fafc; color: #0284c7; border: 1px solid #bae6fd; border-radius: 6px; cursor: pointer;">一键回滚</button>' +
                '<button type="button" onclick="handleDeleteSnapshot(\\'' + s.snapshot_id + '\\')" style="padding: 5px 10px; font-size: 0.78rem; font-weight: 600; background: #f8fafc; color: #ef4444; border: 1px solid #fecaca; background: #ffffff; border-radius: 6px; cursor: pointer;">删除</button>' +
            '</div>' +
        '</div>';
    }).join('');
}

function addSingleProject() {
    var input = document.getElementById('favSingleProjectInput');
    if (!input || !input.value.trim()) return;
    var val = input.value.trim();
    if (currentUserConfig.focus_projects.indexOf(val) === -1) {
        currentUserConfig.focus_projects.push(val);
        renderFavProjectsUI();
    }
    input.value = '';
    input.focus();
}

function removeSingleProject(idx) {
    currentUserConfig.focus_projects.splice(idx, 1);
    renderFavProjectsUI();
}

function addSingleOwner() {
    var input = document.getElementById('favSingleOwnerInput');
    if (!input || !input.value.trim()) return;
    var val = input.value.trim();
    if (currentUserConfig.focus_owners.indexOf(val) === -1) {
        currentUserConfig.focus_owners.push(val);
        renderFavOwnersUI();
    }
    input.value = '';
    input.focus();
}

function removeSingleOwner(idx) {
    currentUserConfig.focus_owners.splice(idx, 1);
    renderFavOwnersUI();
}

async function loadUserFullConfig() {
    try {
        var res = await fetch('/api/user/config');
        if (res.status === 401) {
            closeFavoritesModal();
            openAuthModal('login');
            return;
        }
        var data = await res.json();
        if (data.ok && data.config) {
            currentUserConfig.focus_projects = data.config.focus_projects || [];
            currentUserConfig.focus_owners = data.config.focus_owners || [];
            currentUserConfig.alert_keywords = data.config.alert_keywords || [];
            currentUserConfig.alert_min_amount = (data.config.alert_min_amount !== undefined) ? data.config.alert_min_amount : 1000.0;
        }

        var snapRes = await fetch('/api/user/config_snapshots');
        if (snapRes.ok) {
            var snapData = await snapRes.json();
            currentUserConfig.snapshots = snapData.snapshots || [];
        }

        renderFavProjectsUI();
        renderFavOwnersUI();
        renderFavAlertsUI();
        renderFavSnapshotsUI();
    } catch (err) {
        console.error('加载用户独立配置异常:', err);
    }
}

async function saveUserFullConfigSubmit() {
    var btn = document.getElementById('btnSaveFullConfig');
    var tip = document.getElementById('favActionTip');
    var noteInput = document.getElementById('favSaveNoteInput');
    if (btn) btn.disabled = true;
    if (tip) tip.style.display = 'none';

    // 如果处于批量文本编辑态，先完成文本解析
    var pBatch = document.getElementById('favProjectsBatchContainer');
    if (pBatch && pBatch.style.display !== 'none') toggleBatchEdit('projects');
    var oBatch = document.getElementById('favOwnersBatchContainer');
    if (oBatch && oBatch.style.display !== 'none') toggleBatchEdit('owners');

    // 收集预警门槛和关键词
    var amtInput = document.getElementById('favAlertMinAmount');
    var kwArea = document.getElementById('favAlertKeywordsTextarea');
    var minAmt = amtInput ? parseFloat(amtInput.value || '1000') : 1000.0;
    var kwList = [];
    if (kwArea && kwArea.value.trim()) {
        var rawKw = kwArea.value.replace(/\\r\\n/g, '\\n').replace(/，/g, ',');
        if (rawKw.indexOf('\\n') !== -1) {
            kwList = rawKw.split('\\n').map(function(s) { return s.trim(); }).filter(Boolean);
        } else {
            kwList = rawKw.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
        }
    }

    var payload = {
        focus_projects: currentUserConfig.focus_projects,
        focus_owners: currentUserConfig.focus_owners,
        alert_keywords: kwList,
        alert_min_amount: isNaN(minAmt) ? 1000.0 : minAmt,
        note: noteInput ? noteInput.value.trim() : ''
    };

    try {
        var res = await fetch('/api/user/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        var data = await res.json();
        if (data.ok) {
            if (noteInput) noteInput.value = '';
            try {
                localStorage.setItem('gxzb_user_focus_config', JSON.stringify(payload));
            } catch (e) {}
            try {
                if (window.BroadcastChannel) {
                    new BroadcastChannel('gxzb_focus_channel').postMessage(payload);
                }
            } catch (e) {}
            window.dispatchEvent(new CustomEvent('gxzb_focus_config_changed', { detail: payload }));
            if (tip) {
                tip.className = 'admin-login-tip tip-success';
                tip.innerText = '专属重点跟踪规则已成功保存，并已生成最新历史快照！';
                tip.style.display = 'block';
            }
            await loadUserFullConfig();
            setTimeout(function() {
                if (tip) tip.style.display = 'none';
            }, 3000);
        } else {
            if (tip) {
                tip.className = 'admin-login-tip tip-error';
                tip.innerText = data.msg || '保存配置失败';
                tip.style.display = 'block';
            }
        }
    } catch (err) {
        try {
            localStorage.setItem('gxzb_user_focus_config', JSON.stringify(payload));
        } catch (e) {}
        try {
            if (window.BroadcastChannel) {
                new BroadcastChannel('gxzb_focus_channel').postMessage(payload);
            }
        } catch (e) {}
        window.dispatchEvent(new CustomEvent('gxzb_focus_config_changed', { detail: payload }));
        if (tip) {
            tip.className = 'admin-login-tip tip-error';
            tip.innerText = '专属规则已保存到本地并实时生效（离线模式）';
            tip.style.display = 'block';
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function handleRollbackSnapshot(snapshotId) {
    if (!confirm('确定要将您的个性化跟踪规则回滚至此快照版本吗？')) return;
    var tip = document.getElementById('favActionTip');
    try {
        var res = await fetch('/api/user/config_snapshots/rollback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ snapshot_id: snapshotId })
        });
        var data = await res.json();
        if (data.ok) {
            alert('回滚成功！已成功载入该快照规则。');
            await loadUserFullConfig();
        } else {
            alert('回滚失败: ' + (data.msg || '未知错误'));
        }
    } catch (err) {
        alert('回滚请求异常: ' + err.message);
    }
}

async function handleDeleteSnapshot(snapshotId) {
    if (!confirm('确定要删除该条历史配置快照吗？删除后将无法通过此快照回滚。')) return;
    try {
        var res = await fetch('/api/user/config_snapshots/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ snapshot_id: snapshotId })
        });
        var data = await res.json();
        if (data.ok) {
            if (data.snapshots) {
                currentUserConfig.snapshots = data.snapshots;
            } else if (currentUserConfig.snapshots) {
                currentUserConfig.snapshots = currentUserConfig.snapshots.filter(function(s) {
                    return s.snapshot_id !== snapshotId;
                });
            }
            renderFavSnapshotsUI();
        } else {
            alert('删除快照失败: ' + (data.msg || '未知错误'));
        }
    } catch (err) {
        alert('删除快照请求异常: ' + err.message);
    }
}

async function handleAdminLogin(e) {
    e.preventDefault();
    var input = document.getElementById('adminPasswordInput');
    var btn = document.getElementById('btnAdminSubmit');
    var btnText = document.getElementById('btnAdminSubmitText');
    var tip = document.getElementById('adminLoginTip');
    if (!input || !btn) return;
    
    var password = input.value.trim();
    if (!password) return;

    btn.disabled = true;
    if (btnText) btnText.innerText = '正在验证安全密码...';
    if (tip) tip.style.display = 'none';

    try {
        var res = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: password })
        });
        var data = await res.json();
        if (data.ok) {
            if (data.token) {
                localStorage.setItem('gxzb_auth_token', data.token);
            }
            if (tip) {
                tip.className = 'admin-login-tip tip-success';
                tip.innerText = '验证成功，正在直达管理后台...';
                tip.style.display = 'block';
            }
            setTimeout(function() {
                window.location.href = '/admin/';
            }, 300);
        } else {
            if (tip) {
                tip.className = 'admin-login-tip tip-error';
                tip.innerText = data.msg || '密码错误，请重新输入';
                tip.style.display = 'block';
            }
            btn.disabled = false;
            if (btnText) btnText.innerText = '安全登录并进入后台';
            input.focus();
            input.select();
        }
    } catch (err) {
        if (tip) {
            tip.className = 'admin-login-tip tip-error';
            tip.innerText = '请求服务失败，请检查网络或直接访问 /admin/';
            tip.style.display = 'block';
        }
        btn.disabled = false;
        if (btnText) btnText.innerText = '安全登录并进入后台';
    }
}

function escapeHtmlStr(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// 自动检测用户登录态与管理员鉴权状态
(function checkAuthStates() {
    // 1. 普通用户检测
    fetch('/api/user/me')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data && data.authenticated && data.user) {
                currentUserState = data.user;
                updateUserPortalUI(data.user);
                if (window.location.search.indexOf('open_user=1') !== -1) {
                    setTimeout(function() {
                        openFavoritesModal();
                    }, 200);
                }
            }
        })
        .catch(function() {});

    // 2. 管理员状态检测
    fetch('/api/check_auth')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data && data.authenticated) {
                isAdminState = true;
                var btn = document.getElementById('btnAdminLogin');
                var txt = document.getElementById('adminBtnText');
                if (txt) txt.innerText = '管理后台';
                if (btn) {
                    btn.onclick = function() { window.location.href = '/admin/'; };
                    btn.title = '已验证管理员身份，点击直达管理后台';
                    btn.style.borderColor = '#0284c7';
                    btn.style.color = '#0284c7';
                }
            }
        })
        .catch(function() {});
})();
</script>
"""

_FIELDS = {
    "base_style": base_style,
    "extra_style": extra_style,
    "base_script": base_script,
    "admin_modal_html": admin_modal_html,
    "admin_modal_script": admin_modal_script,
    "site": html.escape(arc.get("site", "广西全区招投标数据监控中心")),
    "subtitle": html.escape(arc.get("subtitle", "广西公共资源交易 · 工程建设类公告每日归档")),
    "app_version": getattr(config, "APP_VERSION", "v0.1.0"),
    "day_count": comma(day_count),
    "total_all": comma(total_all),
    "total_focus": comma(arc.get("total_focus", sum(d.get("focus_count", 0) for d in days))),
    "total_overtime": comma(arc.get("total_overtime", sum(d.get("overtime_count", 0) for d in days))),
    "latest_date": latest_date,
    "latest_hhmm": latest_hhmm or "",
    "latest_updated": latest_updated or latest_date,
    "footer_time_info": footer_time_info,
    "cities_all": cities_all,
    "calendar_html": calendar_html,
    "list_html": list_html,
    "latest_btn_text": f"查看今日简报 ({latest_date})" if (latest_date == today_str) else f"查看最新简报 ({latest_date})",
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

# ---------- 自动同步全盘检索索引与页面 ----------
try:
    from scripts.build_search_page import build_search_page
    build_search_page()
    print("全盘检索门户及索引同步生成成功: dist/search.html, dist/search_index.json, dist/search_index.js")
except Exception as _e:
    print(f"全盘检索门户生成跳过或异常: {_e}")
