# -*- coding: utf-8 -*-
"""全盘检索页面构建器 (Global Search HTML Builder)。

功能说明：
1. 生成静态化的 dist/search.html 全盘检索门户；
2. 支持离线与在线毫秒级全盘搜索（基于 search_index.json）；
3. 结果严格按时间倒序排列（最新发布的公告排在最前）；
4. 滞后公开按小时/天自适应显示（统一珊瑚/琥珀红徽章）；
5. 具备多维度筛选（地市、工程大类、业务环节、滞后、加班等）；
6. 支持 URL 参数如 ?q=水库 自动填充并检索。
"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from scripts.build_search_index import generate_search_index


def build_search_page() -> Path:
    """生成 dist/search.html。"""
    # 确保索引是最新的
    generate_search_index()
    
    out_dir = Path(config.SITE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "search.html"
    
    html_content = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>全盘检索 · 广西招投标公告全历史检索中心</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
    :root {
        --primary: #0284c7;
        --primary-light: #f0f9ff;
        --primary-dark: #0369a1;
        --primary-border: #bae6fd;
        --accent: #0ea5e9;
        
        --delayed-bg: #fff7ed;
        --delayed-text: #c2410c;
        --delayed-border: #fed7aa;
        
        --overtime-bg: #f5f3ff;
        --overtime-text: #6d28d9;
        --overtime-border: #ddd6fe;
        
        --bg-body: #f8fafc;
        --card-bg: #ffffff;
        --card-hover: #f1f5f9;
        --border-color: #e2e8f0;
        --border-light: #f1f5f9;
        
        --text-primary: #0f172a;
        --text-secondary: #475569;
        --text-muted: #94a3b8;
        
        --radius-sm: 6px;
        --radius-md: 10px;
        --radius-lg: 14px;
        --radius-full: 9999px;
        --shadow-sm: 0 1px 3px rgba(15, 23, 42, 0.05);
        --shadow-md: 0 4px 12px -2px rgba(15, 23, 42, 0.06);
    }

    html.dark {
        --primary: #38bdf8;
        --primary-light: #082f49;
        --primary-dark: #0284c7;
        --primary-border: #0369a1;
        --accent: #38bdf8;
        
        --delayed-bg: #431407;
        --delayed-text: #fb923c;
        --delayed-border: #9a3412;
        
        --overtime-bg: #2e1065;
        --overtime-text: #c084fc;
        --overtime-border: #581c87;
        
        --bg-body: #0b1120;
        --card-bg: #1e293b;
        --card-hover: #334155;
        --border-color: #334155;
        --border-light: #1e293b;
        
        --text-primary: #f8fafc;
        --text-secondary: #cbd5e1;
        --text-muted: #64748b;
    }

    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
        font-family: 'Plus Jakarta Sans', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        background-color: var(--bg-body);
        color: var(--text-primary);
        line-height: 1.5;
        -webkit-font-smoothing: antialiased;
        min-height: 100vh;
    }

    /* Top Header */
    .site-header {
        background: rgba(255, 255, 255, 0.88);
        backdrop-filter: blur(12px);
        border-bottom: 1px solid var(--border-color);
        position: sticky;
        top: 0;
        z-index: 50;
    }
    html.dark .site-header {
        background: rgba(15, 23, 42, 0.9);
    }
    .header-inner {
        max-width: 1280px;
        margin: 0 auto;
        padding: 12px 24px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
    }
    .brand-group {
        display: flex;
        align-items: center;
        gap: 12px;
        text-decoration: none;
        color: inherit;
    }
    .brand-icon {
        width: 38px;
        height: 38px;
        border-radius: var(--radius-md);
        background: linear-gradient(135deg, #0284c7, #0ea5e9);
        display: flex;
        align-items: center;
        justify-content: center;
        color: #ffffff;
        font-weight: 800;
        font-size: 18px;
        box-shadow: 0 4px 10px rgba(2, 132, 199, 0.3);
    }
    .brand-title {
        font-size: 17px;
        font-weight: 700;
        letter-spacing: -0.01em;
    }
    .brand-subtitle {
        font-size: 12px;
        color: var(--text-muted);
    }
    .nav-actions {
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .nav-btn {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 7px 14px;
        border-radius: var(--radius-sm);
        font-size: 13px;
        font-weight: 600;
        text-decoration: none;
        color: var(--text-secondary);
        background: var(--card-bg);
        border: 1px solid var(--border-color);
        transition: all 0.15s ease;
        cursor: pointer;
    }
    .nav-btn:hover {
        color: var(--primary);
        border-color: var(--primary);
    }
    .nav-btn.primary {
        background: var(--primary);
        color: #ffffff;
        border-color: var(--primary);
    }
    .nav-btn.primary:hover {
        background: var(--primary-dark);
    }

    /* Main Content Container */
    .main-wrap {
        max-width: 1280px;
        margin: 0 auto;
        padding: 24px 20px 60px;
    }

    /* Search Hero Box */
    .search-panel {
        background: var(--card-bg);
        border: 1px solid var(--border-color);
        border-radius: var(--radius-lg);
        padding: 24px;
        box-shadow: var(--shadow-md);
        margin-bottom: 24px;
    }
    .search-input-group {
        display: flex;
        align-items: center;
        gap: 12px;
        background: var(--bg-body);
        border: 2px solid var(--border-color);
        border-radius: var(--radius-md);
        padding: 6px 14px;
        transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }
    .search-input-group:focus-within {
        border-color: var(--primary);
        box-shadow: 0 0 0 3px rgba(2, 132, 199, 0.15);
        background: var(--card-bg);
    }
    .search-input-group svg {
        color: var(--text-muted);
        flex-shrink: 0;
    }
    .search-box-input {
        flex: 1;
        border: none;
        background: transparent;
        font-size: 16px;
        font-weight: 500;
        color: var(--text-primary);
        outline: none;
        padding: 6px 0;
    }
    .search-box-input::placeholder {
        color: var(--text-muted);
    }
    .clear-btn {
        background: none;
        border: none;
        color: var(--text-muted);
        cursor: pointer;
        padding: 4px;
        border-radius: 4px;
        display: none;
    }
    .clear-btn:hover { color: var(--text-primary); }

    /* Filters Row */
    .filter-bar {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-top: 16px;
        padding-top: 16px;
        border-top: 1px solid var(--border-color);
    }
    .filter-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
    }
    .chip {
        padding: 5px 12px;
        border-radius: var(--radius-full);
        font-size: 13px;
        font-weight: 600;
        border: 1px solid var(--border-color);
        background: var(--bg-body);
        color: var(--text-secondary);
        cursor: pointer;
        transition: all 0.15s ease;
        user-select: none;
    }
    .chip:hover {
        border-color: var(--primary);
        color: var(--primary);
    }
    .chip.active {
        background: var(--primary);
        color: #ffffff;
        border-color: var(--primary);
    }
    .chip.delayed.active {
        background: #ea580c;
        color: #ffffff;
        border-color: #ea580c;
    }
    .chip.overtime.active {
        background: #7c3aed;
        color: #ffffff;
        border-color: #7c3aed;
    }
    .chip.project.active {
        background: #0284c7;
        color: #ffffff;
        border-color: #0284c7;
    }
    .chip.owner.active {
        background: #4f46e5;
        color: #ffffff;
        border-color: #4f46e5;
    }

    .filter-selects {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
    }
    .select-item {
        padding: 6px 12px;
        border-radius: var(--radius-sm);
        border: 1px solid var(--border-color);
        background: var(--card-bg);
        color: var(--text-primary);
        font-size: 13px;
        font-weight: 500;
        outline: none;
        cursor: pointer;
    }
    .select-item:focus {
        border-color: var(--primary);
    }

    /* Status / Stats Bar */
    .results-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 16px;
        padding: 0 4px;
    }
    .results-count {
        font-size: 14px;
        color: var(--text-secondary);
    }
    .results-count strong {
        color: var(--primary);
        font-weight: 700;
    }
    .sort-tip {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        font-size: 12px;
        color: var(--text-muted);
        background: var(--card-bg);
        padding: 4px 10px;
        border-radius: var(--radius-sm);
        border: 1px solid var(--border-color);
    }

    /* Notice List */
    .notice-list {
        display: flex;
        flex-direction: column;
        gap: 12px;
    }
    .notice-card {
        background: var(--card-bg);
        border: 1px solid var(--border-color);
        border-radius: var(--radius-md);
        padding: 16px 20px;
        transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }
    .notice-card:hover {
        border-color: var(--primary-border);
        box-shadow: var(--shadow-md);
        transform: translateY(-1px);
    }
    .notice-card.is-delayed {
        border-left: 4px solid #f97316;
    }
    .notice-header-row {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
    }
    .time-badge {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 13px;
        font-weight: 600;
        color: var(--text-secondary);
        background: var(--bg-body);
        padding: 3px 8px;
        border-radius: 4px;
        border: 1px solid var(--border-color);
    }
    .tags-group {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 6px;
    }
    .tag-badge {
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 600;
        border: 1px solid var(--border-color);
        background: var(--bg-body);
        color: var(--text-secondary);
    }
    .tag-badge.area {
        background: #e0f2fe;
        color: #0369a1;
        border-color: #bae6fd;
    }
    html.dark .tag-badge.area {
        background: #082f49;
        color: #7dd3fc;
        border-color: #0369a1;
    }
    .tag-badge.stage {
        background: #f1f5f9;
        color: #334155;
    }
    .delayed-badge {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 700;
        background: var(--delayed-bg);
        color: var(--delayed-text);
        border: 1px solid var(--delayed-border);
    }
    .overtime-badge {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 700;
        background: var(--overtime-bg);
        color: var(--overtime-text);
        border: 1px solid var(--overtime-border);
    }
    .notice-title {
        font-size: 16px;
        font-weight: 600;
        line-height: 1.45;
        color: var(--text-primary);
    }
    .notice-title a {
        color: inherit;
        text-decoration: none;
    }
    .notice-title a:hover {
        color: var(--primary);
        text-decoration: underline;
    }
    mark {
        background: #fef08a;
        color: #854d0e;
        padding: 0 2px;
        border-radius: 2px;
        font-weight: 700;
    }
    html.dark mark {
        background: #854d0e;
        color: #fef08a;
    }
    .notice-footer {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding-top: 6px;
        font-size: 12px;
        color: var(--text-muted);
    }
    .actions-group {
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .action-link {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        color: var(--primary);
        text-decoration: none;
        font-weight: 600;
        padding: 3px 8px;
        border-radius: 4px;
        background: var(--primary-light);
        border: 1px solid var(--primary-border);
        transition: all 0.15s ease;
    }
    .action-link:hover {
        background: var(--primary);
        color: #ffffff;
    }

    /* Pagination */
    .pagination-row {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
        margin-top: 32px;
    }
    .page-btn {
        padding: 8px 16px;
        border-radius: var(--radius-sm);
        border: 1px solid var(--border-color);
        background: var(--card-bg);
        color: var(--text-primary);
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.15s ease;
    }
    .page-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }
    .page-btn:not(:disabled):hover {
        border-color: var(--primary);
        color: var(--primary);
    }
    .page-info {
        font-size: 13px;
        color: var(--text-secondary);
    }

    /* Empty State */
    .empty-card {
        background: var(--card-bg);
        border: 1px dashed var(--border-color);
        border-radius: var(--radius-lg);
        padding: 60px 20px;
        text-align: center;
        color: var(--text-muted);
    }
    .empty-card svg {
        margin-bottom: 12px;
        color: var(--text-muted);
    }
    .empty-card h3 {
        font-size: 16px;
        color: var(--text-secondary);
        margin-bottom: 6px;
    }

    /* Loading Skeleton */
    .loading-wrap {
        padding: 40px;
        text-align: center;
        color: var(--text-muted);
        font-size: 14px;
    }
    .spinner {
        display: inline-block;
        width: 32px;
        height: 32px;
        border: 3px solid var(--border-color);
        border-top-color: var(--primary);
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
        margin-bottom: 12px;
    }
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
</style>
</head>
<body>

<header class="site-header">
    <div class="header-inner">
        <a class="brand-group" href="./index.html">
            <div class="brand-icon">广</div>
            <div>
                <div class="brand-title">广西工程建设招投标每日资讯</div>
                <div class="brand-subtitle">全盘检索中心 · 覆盖全历史招投标公告</div>
            </div>
        </a>
        <div class="nav-actions">
            <a class="nav-btn" href="./index.html">
                <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>
                返回归档
            </a>
            <button class="nav-btn" id="themeToggle" title="切换深色/浅色主题">
                <svg class="sun-icon" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>
                主题
            </button>
        </div>
    </div>
</header>

<main class="main-wrap">
    <!-- Search Filter Panel -->
    <section class="search-panel">
        <div class="search-input-group">
            <svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"></circle><line x1="21" x2="16.65" y1="21" y2="16.65"></line></svg>
            <input class="search-box-input" id="globalSearchInput" autofocus placeholder="全盘检索所有公告：输入项目名称、业主单位、地市、标段等关键词..." type="text"/>
            <button class="clear-btn" id="clearSearchBtn" title="清空搜索">✕</button>
        </div>

        <div class="filter-bar">
            <div class="filter-chips">
                <div class="chip active" data-filter="all">全部公告</div>
                <div class="chip project" data-filter="focus_project">🎯 重点项目</div>
                <div class="chip owner" data-filter="focus_owner">🏢 重点业主</div>
                <div class="chip delayed" data-filter="delayed">⚠️ 滞后公开</div>
                <div class="chip overtime" data-filter="overtime">🌙 加班发布</div>
            </div>
            <div class="filter-selects">
                <select class="select-item" id="regionSelect">
                    <option value="">全部地市 (15个交易中心)</option>
                    <option value="自治区">自治区本级</option>
                    <option value="南宁">南宁市</option>
                    <option value="柳州">柳州市</option>
                    <option value="桂林">桂林市</option>
                    <option value="梧州">梧州市</option>
                    <option value="北海">北海市</option>
                    <option value="防城港">防城港市</option>
                    <option value="钦州">钦州市</option>
                    <option value="贵港">贵港市</option>
                    <option value="玉林">玉林市</option>
                    <option value="百色">百色市</option>
                    <option value="贺州">贺州市</option>
                    <option value="河池">河池市</option>
                    <option value="来宾">来宾市</option>
                    <option value="崇左">崇左市</option>
                </select>
                <select class="select-item" id="stageSelect">
                    <option value="">全部业务环节</option>
                    <option value="招标">招标/资审公告</option>
                    <option value="中标">中标结果/候选人</option>
                    <option value="答疑">答疑/澄清/更正</option>
                </select>
                <select class="select-item" id="industrySelect">
                    <option value="">全部工程类别</option>
                    <option value="水利">水利工程</option>
                    <option value="交通">交通工程</option>
                    <option value="铁路">铁路工程</option>
                    <option value="房建">房建市政工程</option>
                    <option value="其他">其他项目</option>
                </select>
            </div>
        </div>
    </section>

    <!-- Results Header -->
    <div class="results-header">
        <div class="results-count" id="resultsCount">正在加载全盘索引数据...</div>
        <div class="sort-tip">
            <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"></polyline></svg>
            按发布时间倒序排列（最新排最前）
        </div>
    </div>

    <!-- Notices Container -->
    <div class="notice-list" id="noticeContainer">
        <div class="loading-wrap">
            <div class="spinner"></div>
            <div>正在秒级载入全量招投标数据库...</div>
        </div>
    </div>

    <!-- Pagination -->
    <div class="pagination-row" id="paginationWrap" style="display: none;">
        <button class="page-btn" id="prevPageBtn" disabled>上一页</button>
        <span class="page-info" id="pageInfoText">第 1 / 1 页</span>
        <button class="page-btn" id="nextPageBtn" disabled>下一页</button>
    </div>
</main>

<script>
    // State
    let allData = [];
    let filteredData = [];
    let currentPage = 1;
    const pageSize = 50;
    let activeTag = 'all';

    const searchInput = document.getElementById('globalSearchInput');
    const clearBtn = document.getElementById('clearSearchBtn');
    const noticeContainer = document.getElementById('noticeContainer');
    const resultsCount = document.getElementById('resultsCount');
    const regionSelect = document.getElementById('regionSelect');
    const stageSelect = document.getElementById('stageSelect');
    const industrySelect = document.getElementById('industrySelect');
    const paginationWrap = document.getElementById('paginationWrap');
    const prevPageBtn = document.getElementById('prevPageBtn');
    const nextPageBtn = document.getElementById('nextPageBtn');
    const pageInfoText = document.getElementById('pageInfoText');
    const chips = document.querySelectorAll('.chip');

    // Theme
    const themeToggle = document.getElementById('themeToggle');
    const isDark = localStorage.getItem('theme') === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches);
    if (isDark) document.documentElement.classList.add('dark');
    themeToggle.addEventListener('click', () => {
        document.documentElement.classList.toggle('dark');
        localStorage.setItem('theme', document.documentElement.classList.contains('dark') ? 'dark' : 'light');
    });

    // Helper: Escape HTML
    function esc(s) {
        if (!s) return '';
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // Highlight matched text
    function highlightText(text, keyword) {
        if (!keyword || !text) return esc(text);
        const escapedKw = keyword.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
        const regex = new RegExp(`(${escapedKw})`, 'gi');
        const parts = String(text).split(regex);
        return parts.map(part => {
            if (part.toLowerCase() === keyword.toLowerCase()) {
                return `<mark>${esc(part)}</mark>`;
            }
            return esc(part);
        }).join('');
    }

    // Filter & Search
    function applyFilters() {
        const q = (searchInput.value || '').trim().toLowerCase();
        const reg = regionSelect.value;
        const stg = stageSelect.value;
        const ind = industrySelect.value;

        clearBtn.style.display = q ? 'block' : 'none';

        filteredData = allData.filter(item => {
            // Keyword search across title, areaname, stage, industry
            if (q) {
                const t = (item.title || '').toLowerCase();
                const a = (item.areaname || '').toLowerCase();
                const s = (item.stage || '').toLowerCase();
                const i = (item.industry || '').toLowerCase();
                if (!t.includes(q) && !a.includes(q) && !s.includes(q) && !i.includes(q)) {
                    return false;
                }
            }

            // Chips
            if (activeTag === 'delayed' && !item.is_delayed) return false;
            if (activeTag === 'overtime' && !item.is_overtime) return false;
            if (activeTag === 'focus_project' && (!item.focus_tags || item.focus_tags.indexOf('重点项目') === -1)) return false;
            if (activeTag === 'focus_owner' && (!item.focus_tags || item.focus_tags.indexOf('重点业主') === -1)) return false;
            if (activeTag === 'focus' && !item.is_focus) return false;

            // Region
            if (reg && !(item.areaname || '').includes(reg)) return false;

            // Stage
            if (stg && !(item.stage || '').includes(stg)) return false;

            // Industry
            if (ind && !(item.industry || '').includes(ind)) return false;

            return true;
        });

        // Ensure strict descending order by publish time (newest first)
        filteredData.sort((a, b) => {
            const ta = a.pub_time || a.date || '';
            const tb = b.pub_time || b.date || '';
            return tb.localeCompare(ta);
        });

        currentPage = 1;
        renderResults();
    }

    // Render Results List
    function renderResults() {
        const total = filteredData.length;
        const q = (searchInput.value || '').trim();
        
        if (q) {
            resultsCount.innerHTML = `共检索到 <strong>${total}</strong> 条包含「${esc(q)}」的讯息（按时间倒序排列）`;
        } else {
            resultsCount.innerHTML = `全盘历史共收录 <strong>${allData.length}</strong> 条公告，当前筛选显示 <strong>${total}</strong> 条`;
        }

        if (total === 0) {
            noticeContainer.innerHTML = `
                <div class="empty-card">
                    <svg width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"></circle><line x1="21" x2="16.65" y1="21" y2="16.65"></line></svg>
                    <h3>未检索到匹配的招投标公告</h3>
                    <p>请尝试更换关键词（如项目名、单位简称、地市），或清除部分筛选条件</p>
                </div>
            `;
            paginationWrap.style.display = 'none';
            return;
        }

        const totalPages = Math.ceil(total / pageSize);
        const startIdx = (currentPage - 1) * pageSize;
        const endIdx = Math.min(startIdx + pageSize, total);
        const pageItems = filteredData.slice(startIdx, endIdx);

        let html = '';
        pageItems.forEach(d => {
            const pTime = d.pub_time || `${d.date} 00:00:00`;
            const titleHtml = highlightText(d.title, q);
            
            // Delay badge formatting (hours / days adaptive with same uniform style)
            let delayHtml = '';
            if (d.is_delayed) {
                let dLabel = d.delay_label;
                if (!dLabel) {
                    if (d.delay_days >= 1) dLabel = `滞后 ${d.delay_days}天`;
                    else if (d.delay_hours >= 1) dLabel = `滞后 ${d.delay_hours}小时`;
                    else dLabel = '滞后公开';
                }
                const tip = d.delayed_reason || `【存证判定】官方标称发布于 ${d.pub_time || ''}，确证${dLabel}。`;
                delayHtml = `<span class="delayed-badge" title="${esc(tip)}"><svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>${esc(dLabel)}</span>`;
            }

            // Overtime badge
            let overtimeHtml = '';
            if (d.is_overtime) {
                const otTip = d.overtime_reason || '非工作时间/加班发布';
                overtimeHtml = `<span class="overtime-badge" title="${esc(otTip)}"><svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>加班发布</span>`;
            }

            // Focus badges (separate project & owner)
            let focusProjHtml = '';
            if (d.focus_tags && d.focus_tags.indexOf('重点项目') !== -1) {
                focusProjHtml = `<span class="tag-badge project" style="background:#f0f9ff;color:#0369a1;border:1px solid #bae6fd;font-weight:700;">🎯 重点项目</span>`;
            }
            let focusOwnerHtml = '';
            if (d.focus_tags && d.focus_tags.indexOf('重点业主') !== -1) {
                focusOwnerHtml = `<span class="tag-badge owner" style="background:#eef2ff;color:#4338ca;border:1px solid #c7d2fe;font-weight:700;">🏢 重点业主</span>`;
            }

            const dayLink = `./${d.date}.html#${d.infoid || ''}`;
            const originalLink = d.link || '#';

            html += `
                <article class="notice-card ${d.is_delayed ? 'is-delayed' : ''}">
                    <div class="notice-header-row">
                        <span class="time-badge">
                            <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                            ${esc(pTime)}
                        </span>
                        <div class="tags-group">
                            ${delayHtml}
                            ${overtimeHtml}
                            ${focusProjHtml}
                            ${focusOwnerHtml}
                            ${d.areaname ? `<span class="tag-badge area">${esc(d.areaname)}</span>` : ''}
                            ${d.stage ? `<span class="tag-badge stage">${esc(d.stage)}</span>` : ''}
                            ${d.industry ? `<span class="tag-badge">${esc(d.industry)}</span>` : ''}
                        </div>
                    </div>
                    <h2 class="notice-title">
                        <a href="${esc(originalLink)}" target="_blank" rel="noopener noreferrer">${titleHtml}</a>
                    </h2>
                    <div class="notice-footer">
                        <span>归档日期：${esc(d.date)} · 数据来源：${esc(d.source || '广西公共资源交易平台')}</span>
                        <div class="actions-group">
                            <a class="action-link" href="${esc(dayLink)}" title="前往对应单日日报浏览上下文">
                                查看当日日报
                                <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"></polyline></svg>
                            </a>
                            <a class="action-link" href="${esc(originalLink)}" target="_blank" rel="noopener noreferrer" title="前往官方源网站查看">
                                官方源文 ↗
                            </a>
                        </div>
                    </div>
                </article>
            `;
        });

        noticeContainer.innerHTML = html;

        // Pagination Controls
        if (totalPages > 1) {
            paginationWrap.style.display = 'flex';
            pageInfoText.textContent = `第 ${currentPage} / ${totalPages} 页 (共 ${total} 条)`;
            prevPageBtn.disabled = currentPage <= 1;
            nextPageBtn.disabled = currentPage >= totalPages;
        } else {
            paginationWrap.style.display = 'none';
        }
    }

    // Event Listeners
    let debounceTimer = null;
    searchInput.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(applyFilters, 150);
    });

    clearBtn.addEventListener('click', () => {
        searchInput.value = '';
        searchInput.focus();
        applyFilters();
    });

    chips.forEach(chip => {
        chip.addEventListener('click', () => {
            chips.forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            activeTag = chip.getAttribute('data-filter');
            applyFilters();
        });
    });

    regionSelect.addEventListener('change', applyFilters);
    stageSelect.addEventListener('change', applyFilters);
    industrySelect.addEventListener('change', applyFilters);

    prevPageBtn.addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            renderResults();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    });

    nextPageBtn.addEventListener('click', () => {
        const totalPages = Math.ceil(filteredData.length / pageSize);
        if (currentPage < totalPages) {
            currentPage++;
            renderResults();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    });

    // Load data from search_index.json
    async function initData() {
        try {
            const res = await fetch('./search_index.json');
            if (!res.ok) throw new Error('HTTP ' + res.status);
            allData = await res.json();
            
            // Parse URL parameters (e.g. ?q=水库)
            const params = new URLSearchParams(window.location.search);
            const urlQ = params.get('q');
            if (urlQ) {
                searchInput.value = urlQ;
            }
            const urlTag = params.get('tag');
            if (urlTag) {
                const targetChip = Array.from(chips).find(c => c.getAttribute('data-filter') === urlTag);
                if (targetChip) {
                    chips.forEach(c => c.classList.remove('active'));
                    targetChip.classList.add('active');
                    activeTag = urlTag;
                }
            }

            applyFilters();
        } catch (err) {
            noticeContainer.innerHTML = `
                <div class="empty-card">
                    <h3>载入全盘索引失败</h3>
                    <p>${esc(err.message)}。请检查 search_index.json 是否已生成并在同一路径下。</p>
                </div>
            `;
        }
    }

    initData();
</script>
</body>
</html>
"""
    out_file.write_text(html_content, encoding="utf-8")
    return out_file


if __name__ == "__main__":
    p = build_search_page()
    print(f"全盘检索门户页面已生成: {p}")
