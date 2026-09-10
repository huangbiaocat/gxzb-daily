# 广西招投标公告日报（gxzb-daily）

广西壮族自治区公共资源交易（工程建设类）公告的**每日自动采集 → 入库判重 → 生成静态日报页 → 归档 → 缺失核验**全流程脚本仓库。

设计目标：**脱离 AI 独立运行**。全部脚本只依赖 Python 标准库，可由 cron / launchd / 计划任务无人值守调度；配置与凭证统一走 `.env`（不入库），任何一步失败都会输出非 0 退出码并在日志中留下可重查记录。

---

## 1. 产物说明

| 产物 | 路径 | 说明 |
| --- | --- | --- |
| 每日明细页 | `<SITE_DIR>/YYYY-MM-DD.html` | 当日公告明细，支持关键词 / 工程大类 / 地市 / 业务环节筛选与重点预警 |
| 归档首页 | `<SITE_DIR>/index.html` | 按天汇总的归档首页（月历 + 清单） |
| 运行日志 | `<LOG_DIR>/YYYY-MM-DD.jsonl` | 一行一条，**主键 = 官方 infoid**，含 status / updated_at，用于重跑判重与失败重查 |
| infoid 台账 | `<DATA_DIR>/state/index.json` | 跨日去重台账，记录每个 infoid 首次出现日期 |
| 缺失核验报告 | `<REPORT_DIR>/missing-YYYY-MM-DD.{json,md}` | 全量采集与页面展示的差集清单，供人工复核 |
| 采集原始件 | `<DATA_DIR>/raw/YYYY-MM-DD/center-*.json` | 15 个交易中心的接口原始返回，留档便于回溯 |

## 2. 目录结构

```
gxzb-daily/
├── run_daily.py              # 每日任务编排入口（唯一需要调度的脚本）
├── config.py                 # 全部配置；.env / 环境变量优先
├── requirements.txt          # 无第三方依赖（仅标注 Python 版本要求）
├── .env.example              # 配置模板，复制为 .env 使用
├── scripts/
│   ├── collect.py            # 采集 15 个交易中心公告，按 infoid 去重规范化
│   ├── build_daily_page.py   # 生成每日明细页 + 写运行日志 / infoid 台账
│   ├── build_archive_page.py # 生成归档首页
│   ├── diff_missing.py       # 全量采集 vs 页面展示 差集核验
│   └── logstore.py           # 日志与台账读写（infoid 为唯一识别码）
├── templates/
│   ├── index-preview.html    # 每日页视觉设计基准（样式由此内联提取）
│   └── index-sample.html     # 归档页样板
├── data/                     # 运行数据（不入库）
│   ├── daily/YYYY-MM-DD.json # 当日入库条目（主键 infoid）
│   ├── collect/              # 当日全量采集结果
│   ├── raw/                  # 接口原始返回
│   ├── state/index.json      # 全局 infoid 台账
│   └── archive.json          # 归档首页数据源
└── docs/pipeline.md          # 数据契约与运维细则
```

## 3. 快速开始

```bash
# 1) 环境：Python 3.9+（使用 zoneinfo），无需安装任何第三方包
python3 -V

# 2) 配置
cp .env.example .env
vi .env          # 至少确认 SITE_DIR（页面产物目录）与 SITE_BASE_URL

# 3) 跑当天全流程
python3 run_daily.py

# 4) 常用变体
python3 run_daily.py --date 2026-09-10      # 指定日期
python3 run_daily.py --skip-collect         # 只重生成页面（数据已就绪）
python3 run_daily.py --reconcile merge      # 缺失条目自动并入入库后再生成
python3 run_daily.py --strict               # 存在缺失条目时退出码 2，便于监控告警
```

退出码约定：`0` 正常；`1` 有步骤失败（需人工排查）；`2` 仅在 `--strict` 下出现，表示存在缺失条目。

## 4. 每日任务流程

`run_daily.py` 依次执行 6 步，任一步失败都会记录到 `<DATA_DIR>/state/run-YYYY-MM-DD.json`：

| 步骤 | 动作 | 说明 |
| --- | --- | --- |
| 1/6 | 采集 | 拉取 15 个交易中心公告，按 infoid 去重，落 `raw/` 与 `collect/`；全部中心失败则终止 |
| 2/6 | 入库对账 | 计算「全量 - 已入库」的缺失条目；`RECONCILE=merge` 时按 infoid 只增不改地并入当日入库 |
| 3/6 | 生成每日页 | 渲染 `<SITE_DIR>/YYYY-MM-DD.html`，写运行日志与 infoid 台账，并跑 8 项自检 |
| 4/6 | 归档首页 | 用当日入库数据刷新 `archive.json`，重建 `<SITE_DIR>/index.html` |
| 5/6 | 缺失核验 | 产出 `missing-YYYY-MM-DD.{json,md}`，供人工确认后再合并 |
| 6/6 | 外部推送 | 可选，执行 `.env` 中的 `PUSH_CMD`（如推送脚本 / rsync 同步） |

## 5. 唯一识别码口径（重要）

- **一律使用官方接口返回的 `infoid` 作为唯一识别码**：入库判重、日志、台账、跨日去重全部以 infoid 为键。
- **不再自造编号**：历史上的 `GX + 日期 + 序号` 编号机制已废弃，页面不再展示任何编号标签，也不提供编号检索（检索按标题与地市）。
- **判重逻辑**：日志里同一 infoid 重复出现视为"同一条公告重跑"，只刷新 `status` / `updated_at`，不新增条目；跨日由 `state/index.json` 判断是否为首次出现。
- **失败可重查**：日志中 `status != collected` 的条目即为需要重取的公告，可用
  `python3 scripts/build_daily_page.py --date YYYY-MM-DD --refetch` 列出，或直接读取 `<DATA_DIR>/state/refetch-YYYY-MM-DD.json`（仅含 infoid 清单）。
- **链接口径**：不信任数据源 `url` 字段（历史存在被截断、旧路径失效），统一按 `infoid + categorynum` 拼官方详情页地址（`config.DETAIL_URL_TPL`）。

## 6. 配置项（.env）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `SITE_DIR` | `./dist` | 页面产物根目录（也是同步到服务器的目录） |
| `SITE_BASE_URL` | `https://ztb.139771.xyz` | 站点前缀，用于生成前后一日导航链接 |
| `DATA_DIR` | `./data` | 运行数据目录（采集 / 入库 / 台账） |
| `LOG_DIR` | `<SITE_DIR>/logs` | 运行日志目录 |
| `REPORT_DIR` | `<SITE_DIR>/reports` | 核验报告目录 |
| `TEMPLATE_PREVIEW` | `./templates/index-preview.html` | 每日页视觉设计基准 |
| `PREVIEW_MD5` | 内置 | 设计基准 MD5；改动模板会触发构建自检失败，防止视觉漂移 |
| `COLLECT_CENTERS` | `001`…`015` | 采集的交易中心编号 |
| `COLLECT_CATEGORY_PREFIX` | `001001` | 采集类别（工程建设类） |
| `API_TIMEOUT` / `API_RETRY` / `API_PAGE_SIZE` | `30` / `2` / `500` | 接口超时、重试次数、分页大小 |
| `FOCUS_KEYWORDS` | 公路,医院,学校,… | 重点预警关键词（标题命中即标记） |
| `TIMEZONE` | `Asia/Shanghai` | 取值日期所用时区 |
| `RECONCILE` | `report` | `report` 只出报告；`merge` 自动合并缺失条目后再生成 |
| `PUSH_CMD` | 空 | 生成完成后要执行的外部命令（推送 / 同步），留空跳过 |

> 所有键都可用同名环境变量覆盖，便于容器或 CI 注入。

## 7. 无人值守调度

**crontab**（每天 08:30 与 17:30 各跑一次，重复运行由 infoid 判重保证幂等）：

```cron
30 8,17 * * * cd /opt/gxzb-daily && /usr/bin/python3 run_daily.py >> logs/cron.log 2>&1
```

**macOS launchd**：`~/Library/LaunchAgents/com.gxzb.daily.plist`，`ProgramArguments` 指向 `python3 /path/to/run_daily.py`，`StartCalendarInterval` 指定时间。

**Windows 计划任务**：程序 `python.exe`，参数 `run_daily.py`，起始于仓库目录。

**systemd timer**：

```ini
# /etc/systemd/system/gxzb-daily.service
[Service]
Type=oneshot
WorkingDirectory=/opt/gxzb-daily
ExecStart=/usr/bin/python3 run_daily.py
# /etc/systemd/system/gxzb-daily.timer
[Timer]
OnCalendar=*-*-* 08:30:00
Persistent=true
```

## 8. 发布与同步

`SITE_DIR` 即待发布目录，可按现有流程同步到 Web 服务器，例如：

```bash
rsync -az --delete --include='*.html' --exclude='*' "$SITE_DIR/" user@host:/var/www/tender/
```

若已有推送逻辑，把它写成独立脚本并配置到 `PUSH_CMD`，即纳入每日任务闭环。

## 9. 自检与验收

`build_daily_page.py` 生成后会执行以下检查，任一 FAIL 即退出码 1：

设计基准 MD5 未变 / 数据条数一致 / infoid 唯一 / 链接为官方详情页格式 / **页面无自造编号** / 指标卡 6 类齐全 / 筛选控件齐全 / 静态 DOM 配平 / 页面无外部依赖（Tailwind、CDN、`file://`）/ 日志行数与条目数一致 / 台账覆盖全部条目。

`diff_missing.py` 输出「全量采集 / 页面展示 / 缺失 / 页面独有」四项口径，缺失明细含标题、地市、行业、业务环节、发布时间、infoid 与官方链接。

缺失条目再按「页面已覆盖发布时间窗」（窗口 = 页面/入库数据中最早与最晚的 `pub_time`）自动分两类，判据可复现、不依赖人工记忆：

| 分类 | 判据 | 含义 |
| --- | --- | --- |
| `stale_missed` 早前漏采 | `pub_time` ≤ 页面最新条目时间 | 页面快照生成时该公告在官方接口已存在却未入页，属采集遗漏，报告中单独列出并标注 |
| `pending` 快照后新增 | `pub_time` > 页面最新条目时间 | 页面生成后新发布，等待下一次流水线采集入库，非漏采 |

JSON 报告新增字段：`page_window`（时间窗）、`stale_missed_count`、`pending_count`、`stale_missed`（早前漏采子集）；`missing` 数组每条带 `miss_type` 与 `miss_reason`。`run_daily.py --strict` 仍以缺失总数判定退出码。

## 10. 安全与合规

- `.env` 与一切凭证（推送 token、SSH、同步密码）**只放本地环境文件，绝不写进代码、绝不提交仓库**；`.gitignore` 已忽略 `.env`、`data/`、`logs/`、`reports/`、`dist/` 等运行产物。
- 采集仅访问官方公开接口，单次任务每中心请求次数有限、失败重试有上限，避免对源站造成压力。
- 运行数据（含公告正文信息）默认只保留在本地 `data/`，仓库内不携带任何采集数据。

## 11. 变更记录

- 废弃自造编号（`GX + 日期 + 序号`）：页面移除编号标签与编号检索，日志、台账、入库文件统一改以官方 `infoid` 为唯一识别码，同时保留重跑判重与失败重查能力。
- 缺失核验增加「早前漏采 / 快照后新增」两类自动判定（基于页面已覆盖发布时间窗，判据可复现），报告中对早前漏采条目单独成区、特别标注；JSON 报告新增 `page_window`、`stale_missed_count`、`pending_count`、`stale_missed` 与每条 `miss_type` / `miss_reason` 字段。
- 引入 `config.py` 配置化与 `run_daily.py` 编排，去除硬编码路径，支持脱离 AI 的定时调度。
