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
│   ├── fetcher.py            # 接口请求统一入口：限速、熔断、指数退避
│   ├── collect.py            # 采集 15 个交易中心公告（服务端前缀过滤 + offset 分页 + totalcount 对账）
│   ├── fetch_detail.py       # 按 infoid+categorynum 抓公告详情页正文
│   ├── extract.py            # 正文 → 规则实体抽取 → 写 SQLite，产出抽取报告
│   ├── store.py              # SQLite 四表（notices/notice_fields/projects/runs）读写与状态机
│   ├── build_daily_page.py   # 生成每日明细页 + 写运行日志 / infoid 台账
│   ├── build_archive_page.py # 生成归档首页
│   ├── diff_missing.py       # 全量采集 vs 页面展示 差集核验
│   └── logstore.py           # 日志与台账读写（infoid 为唯一识别码）
├── extractors/
│   ├── normalize.py          # 文本预处理 + 金额/日期/机构名标准化
│   └── rules.py              # 字段标签与抽取规则，extract_notice() 主入口
├── tests/test_rules.py       # 抽取规则回归测试（23 条，零依赖）
├── templates/
│   ├── index-preview.html    # 每日页视觉设计基准（样式由此内联提取）
│   └── index-sample.html     # 归档页样板
├── data/                     # 运行数据（不入库）
│   ├── daily/YYYY-MM-DD.json # 当日入库条目（主键 infoid）
│   ├── collect/              # 当日全量采集结果
│   ├── raw/                  # 接口原始返回
│   ├── details/<day>/        # 公告详情页正文
│   ├── gxzb.sqlite3          # 结构化库（notices/notice_fields/projects/runs）
│   ├── state/index.json      # 全局 infoid 台账
│   └── archive.json          # 归档首页数据源
├── docs/
│   ├── pipeline.md           # 数据契约与运维细则
│   └── extraction-rules.md   # 抽取规则手册（P1）
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

## 5. 正文抓取与实体抽取入库（P1）

在原有「采集 → 页面」链路之上，P1 增加「详情页正文 → 规则抽取 → SQLite」三段，全部为标准库实现，可独立于页面流程单独重跑：

```bash
python3 scripts/collect.py --date 2026-09-10        # 1) 采集（前缀过滤 + offset 分页 + totalcount 对账）
python3 scripts/fetch_detail.py --date 2026-09-10   # 2) 抓正文 → data/details/<day>/<infoid>.json
python3 scripts/extract.py --date 2026-09-10        # 3) 抽字段 → gxzb.sqlite3 + extract-report-<day>.{json,md}
python3 tests/test_rules.py                         # 4) 抽取规则回归测试（23 条）
```

### 5.1 采集加固口径

| 项 | 口径 |
| --- | --- |
| 过滤 | 服务端按 categorynum 前缀（默认 001001，likeType=2）过滤，不再全量拉取后本地筛 |
| 分页 | pn 为 offset 语义，按 rn（默认 500）递增翻页，直到累计条数达到该中心 totalcount |
| 对账 | 每个中心记录 totalcount / fetched / pages / kept / unique / duplicate / off_day；汇总 totalcount_sum 与 fetched_sum 必须相等，最后一条不一致即报告 mismatch |
| 限速 | 同 Host 两次请求间隔 ≥3 秒（`API_MIN_INTERVAL`） |
| 熔断 | 遇 403/429 立即熔断 15 分钟（`API_BREAKER_COOLDOWN`），熔断状态落 `data/state/breaker.json` |
| 退避 | 5xx 按 2 的幂次指数退避重试（`API_BACKOFF_BASE`，上限 `API_RETRY` 次） |
| 404 | 直接跳过不重试，避免无效请求 |

### 5.2 抽取与入库

- 正文来源：官方详情页 `projectDetails.html?infoid=...&categorynum=...`，正文容器 class 由 `DETAIL_BODY_CLASS` 指定。
- 字段：项目名称、招标人、招标代理机构、招标方式、预算/概算/控制价/中标价/投标报价、开标时间与地点、计划工期、中标候选人与中标人、联合体成员、联系人与电话、资质要求、建设内容规模、标段列表、region/stage/project_id。
- 规则细节（金额标准化、机构名清洗、标段噪音过滤、联合体拆解等）见 [docs/extraction-rules.md](docs/extraction-rules.md)。
- 状态机：`pending → extracted → pushed`，抽取异常回写 pending 并累计重试次数。
- 抽取报告：`extract-report-<day>.json/.md`，含 success_rate、abnormal_count、必填/选填缺失分布与字段覆盖率。

### 5.3 实跑基线（2026-09-10）

| 指标 | 结果 |
| --- | --- |
| 采集对账 | 15 个中心，totalcount_sum = 219，fetched_sum = 219，差 0，aligned = true；跨中心按 infoid 去重后 111 条 |
| 正文抓取 | 111/111 条取得正文，正文为空 0 条 |
| 抽取 | 成功 111 条、异常 0 条，成功率 100%，project_id 归出 102 个项目 |
| 字段覆盖 | contact_phone 100、tenderee 94、agency 79、contact_name 57、open_time 51、bid_method 49、period 44、winner 36、scale 32、contract_price 31、open_place 31、lots 20、candidates 18、qualification 16、members 14、bid_price 11、control_price 8、budget 5、estimate 3（单位：条 / 111） |
| 必填缺失 | 139 处，分布 bid_method 62、open_time 60、tenderee 17（多为流标公示、控制价公告等本身不含这些字段的环节） |

## 6. 唯一识别码口径（重要）

- **一律使用官方接口返回的 `infoid` 作为唯一识别码**：入库判重、日志、台账、跨日去重全部以 infoid 为键。
- **不再自造编号**：历史上的 `GX + 日期 + 序号` 编号机制已废弃，页面不再展示任何编号标签，也不提供编号检索（检索按标题与地市）。
- **判重逻辑**：日志里同一 infoid 重复出现视为"同一条公告重跑"，只刷新 `status` / `updated_at`，不新增条目；跨日由 `state/index.json` 判断是否为首次出现。
- **失败可重查**：日志中 `status != collected` 的条目即为需要重取的公告，可用
  `python3 scripts/build_daily_page.py --date YYYY-MM-DD --refetch` 列出，或直接读取 `<DATA_DIR>/state/refetch-YYYY-MM-DD.json`（仅含 infoid 清单）。
- **链接口径**：不信任数据源 `url` 字段（历史存在被截断、旧路径失效），统一按 `infoid + categorynum` 拼官方详情页地址（`config.DETAIL_URL_TPL`）。

## 7. 配置项（.env）

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

## 8. 无人值守调度

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

## 9. 发布与同步

`SITE_DIR` 即待发布目录，可按现有流程同步到 Web 服务器，例如：

```bash
rsync -az --delete --include='*.html' --exclude='*' "$SITE_DIR/" user@host:/var/www/tender/
```

若已有推送逻辑，把它写成独立脚本并配置到 `PUSH_CMD`，即纳入每日任务闭环。

## 10. 自检与验收

`build_daily_page.py` 生成后会执行以下检查，任一 FAIL 即退出码 1：

设计基准 MD5 未变 / 数据条数一致 / infoid 唯一 / 链接为官方详情页格式 / **页面无自造编号** / 指标卡 6 类齐全 / 筛选控件齐全 / 静态 DOM 配平 / 页面无外部依赖（Tailwind、CDN、`file://`）/ 日志行数与条目数一致 / 台账覆盖全部条目。

`diff_missing.py` 输出「全量采集 / 页面展示 / 缺失 / 页面独有」四项口径，缺失明细含标题、地市、行业、业务环节、发布时间、infoid 与官方链接。

缺失条目再按「页面已覆盖发布时间窗」（窗口 = 页面/入库数据中最早与最晚的 `pub_time`）自动分两类，判据可复现、不依赖人工记忆：

| 分类 | 判据 | 含义 |
| --- | --- | --- |
| `stale_missed` 早前漏采 | `pub_time` ≤ 页面最新条目时间 | 页面快照生成时该公告在官方接口已存在却未入页，属采集遗漏，报告中单独列出并标注 |
| `pending` 快照后新增 | `pub_time` > 页面最新条目时间 | 页面生成后新发布，等待下一次流水线采集入库，非漏采 |

JSON 报告新增字段：`page_window`（时间窗）、`stale_missed_count`、`pending_count`、`stale_missed`（早前漏采子集）；`missing` 数组每条带 `miss_type` 与 `miss_reason`。`run_daily.py --strict` 仍以缺失总数判定退出码。

## 11. 安全与合规

- `.env` 与一切凭证（推送 token、SSH、同步密码）**只放本地环境文件，绝不写进代码、绝不提交仓库**；`.gitignore` 已忽略 `.env`、`data/`、`logs/`、`reports/`、`dist/` 等运行产物。
- 采集仅访问官方公开接口，单次任务每中心请求次数有限、失败重试有上限，避免对源站造成压力。
- 运行数据（含公告正文信息）默认只保留在本地 `data/`，仓库内不携带任何采集数据。

## 12. 变更记录

- 废弃自造编号（`GX + 日期 + 序号`）：页面移除编号标签与编号检索，日志、台账、入库文件统一改以官方 `infoid` 为唯一识别码，同时保留重跑判重与失败重查能力。
- 缺失核验增加「早前漏采 / 快照后新增」两类自动判定（基于页面已覆盖发布时间窗，判据可复现），报告中对早前漏采条目单独成区、特别标注；JSON 报告新增 `page_window`、`stale_missed_count`、`pending_count`、`stale_missed` 与每条 `miss_type` / `miss_reason` 字段。
- 引入 `config.py` 配置化与 `run_daily.py` 编排，去除硬编码路径，支持脱离 AI 的定时调度。
