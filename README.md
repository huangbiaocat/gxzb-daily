# 广西招投标公告日报（gxzb-daily）

广西壮族自治区公共资源交易（自治区本级及 14 个设区市）与崇左市阳光采购平台的**全流程自动化数据采集、首次扫描存证、迟延公开识别、静态网站生成、云端同步与桌面管理控制台**。

> **在线演示站点**：[https://ztb.139771.xyz](https://ztb.139771.xyz)  
> **当前生产版本**：`v0.3.2`  
> **核心设计哲学**：**脱离 AI、零第三方依赖（仅基于 Python 3.9+ 标准库）**，可由 Windows 计划任务 / Linux cron / macOS launchd 长期无人值守稳定调度。

---

## 1. 产物说明

| 产物 | 路径 | 说明 |
| --- | --- | --- |
| 每日明细页 | `<SITE_DIR>/YYYY-MM-DD.html` | 当日公告明细，支持关键词 / 地市 / 业务环节 / 大额标讯筛选、**滞后现身**与**加班突击发布**预警 |
| 归档首页 | `<SITE_DIR>/index.html` | 按天汇总的归档首页（全功能交互月历 + 滞后公开独立统计 + 快速检索清单） |
| 存证数据库 | `<DATA_DIR>/gxzb.sqlite3` | 结构化数据库（notices / notice_fields / projects / runs / **scan_records 首次扫描存证表**） |
| 运行日志 | `<LOG_DIR>/YYYY-MM-DD.jsonl` | 一行一条，**主键 = 官方 infoid**，含 status / updated_at，用于重跑判重与失败重查 |
| infoid 台账 | `<DATA_DIR>/state/index.json` | 跨日去重台账，记录每个 infoid 首次出现日期 |
| 缺失核验报告 | `<REPORT_DIR>/missing-YYYY-MM-DD.{json,md}` | 全量采集与页面展示的差集清单，供人工复核 |
| 采集原始件 | `<DATA_DIR>/raw/YYYY-MM-DD/center-*.json` | 15 个交易中心的接口原始返回，留档便于回溯 |

## 2. 目录结构

```
gxzb-daily/
├── run_daily.py              # 每日流水线编排入口（调度核心：采集->回扫->对账->渲染->归档->推送->同步）
├── config.py                 # 统一配置中心（内置生产安全默认值，.env 优先）
├── requirements.txt          # 无第三方依赖（仅标注 Python 版本要求）
├── .env.example              # 配置模板，复制为 .env 使用
├── manager/                  # 桌面端管理后台与展厅控制台（零依赖标准库 HTTP 架构）
│   ├── server.py             # 控制台服务端（API：服务监控、计划任务管理、一键升级）
│   └── static/index.html     # Web 管理界面（数据看板、大屏模式、实时日志与参数配置）
├── scripts/
│   ├── fetcher.py            # 接口请求统一入口：限速、熔断、指数退避
│   ├── collect.py            # 多源采集（15 个公共资源中心 + 崇左阳光采购平台）
│   ├── backscan_delayed.py   # 历史公告存证回扫（排查过去 30 天滞后补录、隐匿现身项目）
│   ├── fetch_detail.py       # 按 infoid+categorynum 抓公告详情页正文
│   ├── extract.py            # 正文 → 规则实体抽取 → 写 SQLite，产出抽取报告
│   ├── store.py              # SQLite 核心库与 scan_records 存证时间戳读写
│   ├── build_daily_page.py   # 生成每日明细页 + 写运行日志 / infoid 台账
│   ├── build_archive_page.py # 生成归档首页（月历组件与滞后公开卡片）
│   ├── run_yesterday_final.py# 凌晨终版封存与开机补跑逻辑
│   ├── upload_vps.py         # 静态站点自动化云端同步（rsync / scp 自适应回退）
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

# 4) 启动桌面端管理控制台 (默认端口 8765)
python3 manager/server.py

# 5) 常用任务变体
python3 run_daily.py --date 2026-09-24      # 指定日期跑批
python3 run_daily.py --skip-collect         # 只重生成页面（数据已就绪）
python3 run_daily.py --reconcile merge      # 缺失条目自动并入入库后再生成
python3 run_daily.py --strict               # 存在缺失条目时退出码 2，便于监控告警
python3 scripts/backscan_delayed.py --days 30 # 单独排查过去 30 天滞后公开项目
python3 scripts/upload_vps.py               # 单独推送 dist/ 到云端 VPS
```

退出码约定：`0` 正常；`1` 有步骤失败（需人工排查）；`2` 仅在 `--strict` 下出现，表示存在缺失条目。

## 4. 每日任务自动化链路 (v0.3.2)

`run_daily.py` 依次执行完整闭环，任一步失败都会记录到 `<DATA_DIR>/state/run-YYYY-MM-DD.json`：

| 步骤 | 动作 | 说明 |
| --- | --- | --- |
| 1/8 | 采集当日全量公告 | 多源拉取（15 个公共资源中心 + 崇左阳光平台），服务端前缀过滤 + 分页 offset + totalcount 对账 |
| 1.2/8 | 存证历史回扫 | 扫描过去 30 天公告，比对初次捕获时间与官方标称发布时间，更新首次扫描存证库 |
| 2/8 | 入库对账 | 计算「全量 - 已入库」缺失，`RECONCILE=merge` 时按 infoid 只增不改地并入当日入库 |
| 3/8 | 生成每日页 | 渲染 `<SITE_DIR>/YYYY-MM-DD.html`，打标滞后现身与加班发布，运行 8 项构建自检 |
| 4/8 | 刷新归档首页 | 刷新 `archive.json`，重建 `<SITE_DIR>/index.html` 交互月历与迟延指标卡 |
| 5/8 | 缺失核验 | 产出 `missing-YYYY-MM-DD.{json,md}`，区分早前漏采与快照后新增 |
| 6/8 | 外部消息推送 | 可选：通过微信服务号模版消息推送重点/大额/错误告警 |
| 7/8 | 同步上传 VPS | 自动化增量同步（rsync / scp）推送到生产 Web 服务器（如 1Panel/Nginx 站点） |
| 8/8 | 终版封存/归档校准 | 归档态锁定与月度数据对账 |

## 5. 核心创新机制：首次扫描存证与迟延发布检测

为防范招投标过程中常见的**“先开标后挂网”、“临近截标才补录”或“深夜/节假日突击发标”**等现象，系统建立了独立的存证与审计机制：

1. **首次扫描时间戳（First-scan Timestamp）**：
   系统利用 SQLite 表 `scan_records` 记录全网每条公告被爬虫系统**真实初次扫描捕获的绝对时间**，与官方标明的 `pub_time` 严格比对。
2. **迟延天数与滞后现身判定**：
   若 `首次扫描时间 - 官方标称时间 >= 2 天`，系统自动将其定性为「滞后现身」公告，在单日简报与归档日历中赋予鲜明预警徽标，并注明“迟延 N 天浮现”。
3. **非工作时间 / 加班发布识别**：
   算法自动识别周末、法定节假日以及夜间（18:00~次日08:00）突击发布的招标信息，辅助分析合规风险。
4. **30 天历史滚动回扫 (`scripts/backscan_delayed.py`)**：
   常规爬虫往往只看“今日新增”，易被滞后补发的历史日期公告绕过。本系统在白昼定时轮询中定期回扫前 30 天历史时间窗，一旦发现此前未入库的补录项目，立即录入存证并触发归档重算。

## 6. 管理控制台与展厅大屏 (`manager`)

项目内置零依赖的现代化桌面端/局域网管理服务（默认访问 `http://localhost:8765`）：
- **实时监控看板**：展示今日实时入库数、存证库总规模、数据源联通状态与最近扫描倒计时。
- **调度与日志中心**：无缝读取 Windows 计划任务或 cron 运行状态，支持实时日志滚动轮询与异常排查。
- **一键运维与自同步**：支持后台手动触发单步/全流程跑批，提供一键代码拉取自同步与配置热生效。
- **展厅大屏模式**：专为会议室监控大屏或展厅展示优化的全屏深色数据流视图。

## 7. 正文抓取与实体抽取入库（P1）

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

## 8. 唯一识别码口径（重要）

- **一律使用官方接口返回的 `infoid` 作为唯一识别码**：入库判重、日志、台账、跨日去重全部以 infoid 为键。
- **不再自造编号**：历史上的 `GX + 日期 + 序号` 编号机制已废弃，页面不再展示任何编号标签，也不提供编号检索（检索按标题与地市）。
- **判重逻辑**：日志里同一 infoid 重复出现视为"同一条公告重跑"，只刷新 `status` / `updated_at`，不新增条目；跨日由 `state/index.json` 判断是否为首次出现。
- **失败可重查**：日志中 `status != collected` 的条目即为需要重取的公告，可用
  `python3 scripts/build_daily_page.py --date YYYY-MM-DD --refetch` 列出，或直接读取 `<DATA_DIR>/state/refetch-YYYY-MM-DD.json`（仅含 infoid 清单）。
- **链接口径**：不信任数据源 `url` 字段（历史存在被截断、旧路径失效），统一按 `infoid + categorynum` 拼官方详情页地址（`config.DETAIL_URL_TPL`）。

## 9. 配置项清单（.env）

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
| `AUTO_UPLOAD_VPS` | `true` | 是否在流水线执行完成后自动推送静态产物至云端 VPS |
| `VPS_HOST` / `VPS_PORT` / `VPS_USER` / `VPS_PATH` | 内置 | 云端生产服务器连接信息与目标静态网站根目录 |
| `BACKSCAN_ENABLED` | `1` | 是否开启历史公告滚动回扫（1=开启，0=关闭） |
| `BACKSCAN_DAYS` | `30` | 历史回扫窗口天数（默认回溯 30 天） |
| `BACKSCAN_MIN_DELAY` | `2` | 判定为“滞后公开”的最小迟延天数阈值 |
| `WECHAT_APPID` / `WECHAT_APPSECRET` | 空 | 微信服务号模版消息推送凭证（可选） |
| `PUSH_CMD` | 空 | 生成完成后要执行的外部命令（推送 / 同步），留空跳过 |

> 所有键都可用同名环境变量覆盖，便于容器或 CI 注入。

## 10. 无人值守调度与双端协同

生产实践推荐采用 **本地/工控机定时采集 + 云端 VPS 轻量挂载静态站点** 的协同模式：

**Linux crontab**（每 10 分钟或定时运行一次，重复运行由 infoid 与存证判重保证幂等）：

```cron
*/10 8-20 * * * cd /opt/gxzb-daily && /usr/bin/python3 run_daily.py >> dist/logs/cron.log 2>&1
```

**Windows 计划任务（I5 采集机）**：
- `ZtbCollector_Sync`：每 10 分钟静默触发 `run_task.bat`，执行日间采集、入库并自动上传 VPS。
- `ZtbCollector_YesterdayFinal`：每天凌晨 00:10 自动锁定并封存昨日最终版。
- **夜间休眠/关机保护**：早晨开机首次运行时，系统会自动检测昨日终版是否已封存，若尚未封存则自动前置补跑封存。

## 11. 发布与同步

`scripts/upload_vps.py` 内置跨平台自适应同步能力：
- 在安装有 `rsync` 的 Linux / macOS 环境下，采用高效的增量流同步；
- 在纯净 Windows 环境下，自动平滑回退至系统内置 OpenSSH `scp` 批量并发上传，无需额外配置 Cygwin 或第三方工具链。

## 12. 自检与验收

`build_daily_page.py` 生成后会执行以下检查，任一 FAIL 即退出码 1：

设计基准 MD5 未变 / 数据条数一致 / infoid 唯一 / 链接为官方详情页格式 / **页面无自造编号** / 指标卡 6 类齐全 / 筛选控件齐全 / 静态 DOM 配平 / 页面无外部依赖（Tailwind、CDN、`file://`）/ 日志行数与条目数一致 / 台账覆盖全部条目。

`diff_missing.py` 输出「全量采集 / 页面展示 / 缺失 / 页面独有」四项口径，缺失明细含标题、地市、行业、业务环节、发布时间、infoid 与官方链接。

缺失条目再按「页面已覆盖发布时间窗」（窗口 = 页面/入库数据中最早与最晚的 `pub_time`）自动分两类，判据可复现、不依赖人工记忆：

| 分类 | 判据 | 含义 |
| --- | --- | --- |
| `stale_missed` 早前漏采 | `pub_time` ≤ 页面最新条目时间 | 页面快照生成时该公告在官方接口已存在却未入页，属采集遗漏，报告中单独列出并标注 |
| `pending` 快照后新增 | `pub_time` > 页面最新条目时间 | 页面生成后新发布，等待下一次流水线采集入库，非漏采 |

JSON 报告新增字段：`page_window`（时间窗）、`stale_missed_count`、`pending_count`、`stale_missed`（早前漏采子集）；`missing` 数组每条带 `miss_type` 与 `miss_reason`。`run_daily.py --strict` 仍以缺失总数判定退出码。

## 13. 安全与合规

- `.env` 与一切凭证（推送 token、SSH、同步密码）**只放本地环境文件，绝不写进代码、绝不提交仓库**；`.gitignore` 已忽略 `.env`、`data/`、`logs/`、`reports/`、`dist/` 等运行产物。
- 采集仅访问官方公开接口，单次任务每中心请求次数有限、失败重试有上限，避免对源站造成压力。
- 运行数据（含公告正文信息）默认只保留在本地 `data/`，仓库内不携带任何采集数据。

## 14. 变更记录

- **v0.3.2**：
  - 修复 Windows 采集机环境下 `upload_vps.py` 使用 `scp` 回退时的变量路径引用问题。
  - 在 `config.py` 与 `manager/server.py` 中为 `VPS_HOST`、`VPS_PATH` 和 `AUTO_UPLOAD_VPS` 增加强鲁棒性默认值，防范管理后台配置保存清空连接参数。
  - 增强单日简报与归档日历间的数据统计与对账一致性。
- **v0.3.0 ~ v0.3.1**：
  - 引入 30 天历史公告存证回扫机制（`scripts/backscan_delayed.py`），构建 `scan_records` 首次捕获时间库。
  - 支持智能识别“滞后公开 / 迟延挂网”与“非工作时间 / 加班发布”，页面直观标记。
- **v0.2.0**：
  - 引入桌面端/局域网管理控制台（`manager`），支持服务启停、定时任务查看、展厅大屏模式与实时日志。
- **v0.1.8**：
- 废弃自造编号（`GX + 日期 + 序号`）：页面移除编号标签与编号检索，日志、台账、入库文件统一改以官方 `infoid` 为唯一识别码，同时保留重跑判重与失败重查能力。
- 缺失核验增加「早前漏采 / 快照后新增」两类自动判定（基于页面已覆盖发布时间窗，判据可复现），报告中对早前漏采条目单独成区、特别标注；JSON 报告新增 `page_window`、`stale_missed_count`、`pending_count`、`stale_missed` 与每条 `miss_type` / `miss_reason` 字段。
- 引入 `config.py` 配置化与 `run_daily.py` 编排，去除硬编码路径，支持脱离 AI 的定时调度。
