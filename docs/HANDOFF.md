---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 3ae3ef24d3ae03b7c9ad9ad4585475c6_4d7cb22fadbc11f18f50525400aeaaa3
    ReservedCode1: ZBT9XAU1UIdr8lWAdBWQTmVhKK+LHTdMn+sOtZysmDo1CJgGslY9pBWArlWqFCSzugmQI83SvTlrgQxbSYSjqt/67LNsx/6IxB7WYpsb/iaIBWgc/E8Fa0sams2ViZz8ChcIrLR/d8uigqIThR3+CZDzm5SWSpGnunzjEps0a1QJJwoXr1XrmTC0dOo=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 3ae3ef24d3ae03b7c9ad9ad4585475c6_4d7cb22fadbc11f18f50525400aeaaa3
    ReservedCode2: ZBT9XAU1UIdr8lWAdBWQTmVhKK+LHTdMn+sOtZysmDo1CJgGslY9pBWArlWqFCSzugmQI83SvTlrgQxbSYSjqt/67LNsx/6IxB7WYpsb/iaIBWgc/E8Fa0sams2ViZz8ChcIrLR/d8uigqIThR3+CZDzm5SWSpGnunzjEps0a1QJJwoXr1XrmTC0dOo=
---

# gxzb-daily 交接文档：换一台电脑从零恢复并跑起来

> 适用场景：**换机交接**。你拿到的是一个 zip 包，需要在一台全新电脑上把它恢复成"能每天自动产出日报页"的状态。
> 本文只写已核实的事实与可直接照做的操作；未实现的部分一律标注「未实现」。
> 文档版本：2026-09-11。

---

## 0. 五分钟上手

```bash
# 1) 解压（包名随交接日期变化）
unzip gxzb-daily-handoff-YYYY-MM-DD.zip -d /你的工作目录/

# 2) 建配置（zip 内不含 .env，必须自己建）
cd /你的工作目录/gxzb-daily
cp .env.example .env
vi .env            # 只需改 SITE_DIR，其余保持默认即可先跑通

# 3) 环境自检
python3 -V                          # 需 3.9+
python3 tests/test_rules.py         # 应输出 31 条用例全部 OK

# 4) 试跑（用包里自带的 2026-09-10 数据，不联网也能验证页面生成）
python3 run_daily.py --date 2026-09-10 --skip-collect

# 5) 正式跑当天全流程（联网）
python3 run_daily.py
```

跑完打开 `SITE_DIR` 下的 `2026-09-10.html` 与 `index.html` 即可看到页面。

---

## 1. 交接包内容清单

zip 解压后得到一个顶层目录 `gxzb-daily/`，结构与作用如下：

| 路径 | 作用 | 是否必须 |
| --- | --- | --- |
| `run_daily.py` | 每日任务编排入口（唯一需要挂调度的脚本） | 必须 |
| `config.py` | 全部配置的读取与默认值（`.env` / 环境变量优先） | 必须 |
| `requirements.txt` | 依赖声明（无第三方运行依赖，仅标注 Python 版本） | 必须 |
| `.env.example` | 配置模板；**包内不含 `.env`**，需自己复制生成 | 必须 |
| `.gitignore` | 忽略规则（`.env`、`data/`、`logs/`、`reports/`、`dist/` 等） | 必须 |
| `scripts/` | 全部可执行脚本（采集、抓正文、抽取、入库、生成页面、核验） | 必须 |
| `extractors/` | 正文实体抽取的规则模块（正则 + 词表，纯标准库） | 必须 |
| `tests/` | 抽取规则回归测试（31 条用例，零依赖） | 必须 |
| `templates/` | 页面视觉模板（每日页基准 + 归档页样板，样式构建时内联提取） | 必须 |
| `docs/` | 文档：本文、数据契约（`pipeline.md`）、抽取规则手册（`extraction-rules.md`） | 必须 |
| `README.md` | 项目总览与命令速查 | 必须 |
| `data/` | 运行数据与样例数据（含 SQLite、正文缓存、采集结果、归档数据、台账） | 可保留可删，见 §6 |
| `__pycache__/`、`.pyc` | Python 字节码缓存 | **已剔除** |
| `.git/` | 版本历史 | **已剔除**（避免历史提交中的旧内容外泄） |
| `.env` | 本机配置 | **已剔除**（含真实凭证，绝对不入包） |
| `.DS_Store`、临时文件 | 系统与编辑器垃圾 | **已剔除** |

> 包内 `data/` 是**样例运行数据**（2026-09-10 为完整链路样例：采集 111 → 入库 61 → 正文 111 → 抽取入库；2026-09-11 只有采集结果），用于"不联网先跑通、先看页面"，不是运行必需。全新环境也可以清空 `data/` 后从当天重新采集。

---

## 2. 环境准备

### 2.1 Python 版本

- 需要 **Python 3.9 或以上**（脚本用到 `zoneinfo`、`pathlib`、带类型提示的标准库 API）。
- 验证：`python3 -V`。

### 2.2 第三方依赖

- **运行零第三方依赖**：采集、抓正文、抽取、入库、生成页面、核验全部只用 Python 标准库，**不需要 `pip install`**。
- `requirements.txt` 只为依赖管理工具与 CI 保留统一入口；其中 `playwright` 为可选（仅在需要人工核对渲染截图时安装）。
- 唯一可能需要补装的情况：**Windows 上 `zoneinfo` 找不到 IANA 时区库**时，`ZoneInfo("Asia/Shanghai")` 会失败。`config.py` 对此做了兜底（捕获异常后回退到本机时区），因此不会崩，但"当天"的判定会跟随本机时区。若要求严格按北京时间取日期，装一次即可：`python -m pip install tzdata`（可选，非必须）。

### 2.3 Windows / macOS 差异

| 事项 | macOS / Linux | Windows |
| --- | --- | --- |
| Python 命令 | `python3` | 通常 `python` 或 `py -3`（以 `python3 -V` 能否运行为准） |
| 路径分隔符 | `/` | `/` 与 `\` 均可；`config.py` 内部统一用 `pathlib`，无需手工改路径 |
| 相对路径基准 | 一律相对**仓库根目录**解析，所以换机后只要不改 `.env` 里的绝对路径就不会失效 | 同左 |
| 时区库 | 系统自带 IANA tzdata | 可能需要 `pip install tzdata`（见 §2.2），否则回退本机时区 |
| 控制台中文输出 | 默认 UTF-8 | PowerShell 5.1 若中文乱码，先执行 `chcp 65001` 或将输出重定向到文件查看 |
| 定时调度 | `crontab` 或 `launchd`（`~/Library/LaunchAgents/*.plist`） | 「任务计划程序」：程序填 `python`，参数填 `run_daily.py`，起始于仓库目录 |
| 打开生成的页面 | `open <SITE_DIR>/index.html` | `start <SITE_DIR>\index.html` |

> 编码：所有脚本与数据文件均为 **UTF-8**，仓库内不要用 GBK/ANSI 另存。

---

## 3. 从零跑起来的完整步骤

### 第 1 步：解压

```bash
unzip gxzb-daily-handoff-YYYY-MM-DD.zip -d <目标目录>
cd <目标目录>/gxzb-daily
```

解压后先确认三件套在：`run_daily.py`、`config.py`、`.env.example`。

### 第 2 步：路径与权限确认

- 目标目录建议放在**用户目录下**（如 `~/work/gxzb-daily`），不要放系统目录（`/System`、`/Library`、`C:\Windows`、`Program Files`）。
- 确认对目录有读写权限（脚本要在其中创建 `data/`、`logs/`、`reports/`、`dist/` 等运行目录）。

### 第 3 步：建立 `.env`

```bash
cp .env.example .env
```

然后**只需先改一个字段**：`SITE_DIR`（页面产物输出目录，也是将来同步到服务器的目录）。

- 想输出到仓库内的 `dist/`：留默认 `./dist` 即可。
- 想直接输出到某个 Web 根目录：写绝对路径。
- 需要自行填写/确认的字段清单见 §5。

### 第 4 步：环境自检

```bash
python3 -V                       # 3.9+
python3 tests/test_rules.py      # 31 条用例应全部 OK（零依赖、不联网）
```

### 第 5 步：试跑（用包内样例数据，不联网）

包内自带 `data/collect/2026-09-10.json`（按 `infoid` 去重后 111 条全量）、`data/daily/2026-09-10.json`（61 条已入库，即当日页面的数据源）、`data/details/2026-09-10/`（111 份正文缓存）与 SQLite 库，因此可以**不联网**先把整个页面链路跑通：

```bash
python3 run_daily.py --date 2026-09-10 --skip-collect
```

预期结果：

- 控制台依次打印 `1/6 采集：已跳过（--skip-collect）` → `2/6 入库对账（缺失 = 全量 - 已入库）` → `3/6 生成每日明细页` → `4/6 刷新归档首页` → `5/6 核验页面缺失条目` → `6/6 外部推送：未配置或已跳过`；
- `SITE_DIR` 下出现 `2026-09-10.html` 与 `index.html`；
- 第 5 步会报缺失 50 条（早前漏采 2 / 快照后新增 48），这是**样例数据的预期现象**而非故障，原因见 §8.3；
- `data/state/run-2026-09-10.json` 的 `failed_steps` 为空数组；
- 退出码为 `0`（`echo $?` 或 `echo %ERRORLEVEL%` 查看）。

若这一步就失败，先看 §10 的故障处理，不要急着联网。

### 第 6 步：正式跑当天全流程（联网）

```bash
python3 run_daily.py
```

该命令会真实访问官方接口，按 **每秒不大于一次、同 Host 间隔 ≥3 秒**的限速采集 15 个交易中心的当日公告，然后判重入库、生成当日页与归档首页、输出缺失核验报告。

- 首次跑完整天数据（15 个中心）预计需要十几分钟量级，取决于当日公告量（每个中心至少 3 秒间隔，逐页递增）。
- 中途中断可重新执行，重复运行由 `infoid` 判重保证幂等。

### 第 7 步：挂调度（正式启用无人值守）

按产品的采集节奏要求（见 §7），需要**每 10 分钟采集一轮**并做「重点关注」即时推送，模型上分两条调度：

- 采集轮（10 分钟一次）：`python3 run_daily.py --skip-archive`（或按接入推送后的实际编排调整）；
- 每日汇总（17:30 一次）：`python3 run_daily.py`。

> 注意：当前 `README.md` §8 里的 crontab 示例仍是「08:30 与 17:30 各跑一次」，与上述 10 分钟一轮的口径不一致，接入调度时需同步改写。推送脚本尚未实现（见 §8），目前的调度只能覆盖"采集 → 页面 → 归档 → 核验"。

---

## 4. 目录结构与各脚本职责

```
gxzb-daily/
├── run_daily.py              # 每日任务编排入口
├── config.py                 # 全部配置；.env / 环境变量优先
├── requirements.txt          # 依赖声明（无第三方运行依赖）
├── .env.example              # 配置模板
├── scripts/
│   ├── fetcher.py            # 统一取数层：限速、熔断、指数退避、404 不重试
│   ├── collect.py            # 采集 15 个交易中心（前缀过滤 + offset 分页 + totalcount 对账）
│   ├── fetch_detail.py       # 抓公告详情页正文（按 infoid+categorynum）
│   ├── extract.py            # 正文 → 规则实体抽取 → 写 SQLite，产出抽取报告
│   ├── store.py              # SQLite 四表读写与状态机
│   ├── build_daily_page.py   # 生成每日明细页 + 写运行日志 / infoid 台账 + 自检
│   ├── build_archive_page.py # 生成归档首页
│   ├── diff_missing.py       # 全量采集 vs 页面展示 差集核验
│   └── logstore.py           # 日志与台账读写（infoid 为唯一识别码）
├── extractors/
│   ├── normalize.py          # 文本预处理 + 金额/日期/机构名标准化（纯函数）
│   └── rules.py              # 字段标签与抽取规则，主入口 extract_notice()
├── tests/test_rules.py       # 抽取规则回归测试（31 条，零依赖）
├── templates/
│   ├── index-preview.html    # 每日页视觉设计基准（样式由此内联提取）
│   └── index-sample.html     # 归档页样板
├── data/                     # 运行数据（详见 §6）
└── docs/
    ├── HANDOFF.md            # 本文
    ├── pipeline.md           # 数据契约与运维细则
    └── extraction-rules.md   # 抽取规则手册 + 实跑校准记录
```

### 脚本职责与输入输出

| 脚本 | 输入 | 输出 | 备注 |
| --- | --- | --- | --- |
| `run_daily.py` | `.env` + 日期 | 全流程产物 + `data/state/run-<day>.json` | 编排 6 步；任一步失败写入 `failed_steps` |
| `collect.py` | 官方接口 | `data/raw/<day>/center-*.json`、`data/collect/<day>.json`、`reports/collect-reconcile-<day>.json` | 按 `infoid` 去重 |
| `fetch_detail.py` | `data/daily`（或 `collect`）+ 接口 | `data/details/<day>/<infoid>.json`、`_progress.json` | 逐条落盘，可断点续跑 |
| `extract.py` | `data/collect` × `data/details` | `data/gxzb.sqlite3`、`reports/extract-report-<day>.{json,md}` | 失败回写 `pending` 并累计重试 |
| `store.py` | 抽取结果 | SQLite：`notices / notice_fields / projects / runs` | 主键 `infoid`；只写不删 |
| `build_daily_page.py` | `data/daily/<day>.json` | `<SITE_DIR>/<day>.html`、`<SITE_DIR>/logs/<day>.jsonl`、`data/state/index.json` | 生成后跑多项自检，任一 FAIL 退出码 1 |
| `build_archive_page.py` | `data/archive.json` | `<SITE_DIR>/index.html` | 样式来自 `templates/index-sample.html` |
| `diff_missing.py` | 采集文件 + 页面 | `<SITE_DIR>/reports/missing-<day>.{json,md}` | 缺失分「早前漏采」「快照后新增」「未知」 |
| `logstore.py` | — | 日志 / 台账读写 | 主键 `infoid`；同日重跑只刷新 `status` / `updated_at` |
| `fetcher.py` | — | HTTP 响应 | 全站唯一出网入口，限速/熔断/退避在此统一实现 |

### 常用命令

```bash
# 全流程
python3 run_daily.py                          # 跑当天
python3 run_daily.py --date 2026-09-10        # 指定日期
python3 run_daily.py --skip-collect           # 数据已就绪，只重生成页面
python3 run_daily.py --skip-archive           # 跳过归档首页
python3 run_daily.py --reconcile merge        # 缺失条目自动并入入库后再生成页面
python3 run_daily.py --no-push                # 跳过第 6 步外部推送
python3 run_daily.py --strict                 # 存在缺失条目时退出码 2，便于监控告警

# 分段单独重跑（互不依赖，重复运行由 infoid 判重保证幂等）
python3 scripts/collect.py --date 2026-09-10            # 采集
python3 scripts/fetch_detail.py --date 2026-09-10       # 抓正文（--limit N / --infoid X / --force）
python3 scripts/extract.py --date 2026-09-10            # 抽取入库
python3 scripts/build_daily_page.py --date 2026-09-10   # 重生成每日页（--refetch 列出待重取）
python3 scripts/diff_missing.py --date 2026-09-10 --strict
python3 tests/test_rules.py                             # 抽取规则回归测试
```

### 退出码

| 脚本 | 退出码含义 |
| --- | --- |
| `run_daily.py` | `0` 正常；`1` 有步骤失败（需人工排查）；`2` 仅在 `--strict` 下出现，表示存在缺失条目 |
| `collect.py` | `0` 全部中心成功且对账一致；`3` 部分中心失败 / 对账不一致（结果可能不全）；`2` 全部失败 |
| `fetch_detail.py` | `0` 正常；`1` 全部失败；`2` 触发熔断中断（冷却期内不应再请求）；`4` 没有待抓条目 |
| `extract.py` | `0` 正常；`1` 全部抽取失败；`4` 没有任何可抽取条目（正文缓存为空） |
| `build_daily_page.py` | `0` 成功；`1` 自检未通过（页面已写盘，需人工检查） |
| `diff_missing.py` | `--strict` 下存在缺失时返回非 0 |

---

## 5. 配置项说明（`.env`）

`.env` **不在交接包内**，必须由 `.env.example` 复制生成。下表只列**字段名与含义**，值一律按新机环境自行填写，不要在文档或仓库里记录任何值。

> 说明：所有键都可以用**同名系统环境变量**覆盖，且环境变量优先级高于 `.env`；所有键都有代码内置默认值，不填也能跑。

### 5.1 建议必填 / 必确认

| 字段名 | 含义 | 何时必须改 |
| --- | --- | --- |
| `SITE_DIR` | 页面产物根目录（每日页、归档页、logs、reports 都写在这里，也是将来同步到服务器的目录） | **换机后必改**：`./dist` 表示仓库内 `dist/`；原机 `.env` 里的绝对路径不会随包带过来 |
| `SITE_BASE_URL` | 站点外链前缀，用于生成"前一日/后一日"导航链接 | 部署到真实域名后改为实际访问地址 |
| `RECONCILE` | 页面入库口径：`report` 只出缺失报告（默认，人工确认后合并）；`merge` 自动并入后生成页面 | 无人值守建议设 `merge` |
| `PUSH_CMD` | 第 6 步要执行的外部命令（推送/同步脚本），留空则跳过 | 推送脚本实现后再填 |

### 5.2 路径类（可选，留空用默认）

| 字段名 | 含义 |
| --- | --- |
| `DATA_DIR` | 运行数据目录（采集原始 / 当日入库 / 台账），默认仓库内 `./data` |
| `LOG_DIR` | 运行日志目录，默认 `<SITE_DIR>/logs` |
| `REPORT_DIR` | 核验与抽取报告目录，默认 `<SITE_DIR>/reports` |
| `DB_PATH` | SQLite 库文件路径，默认 `<DATA_DIR>/gxzb.sqlite3` |
| `TEMPLATE_PREVIEW` | 每日页视觉基准模板路径 |
| `TEMPLATE_ARCHIVE_SAMPLE` | 归档页样板模板路径 |
| `TEMPLATE_PREVIEW_MD5` | 视觉基准指纹；模板被改动会触发构建自检失败，防止样式漂移 |

### 5.3 采集类（可选，留空用默认）

| 字段名 | 含义 |
| --- | --- |
| `COLLECT_CENTERS` | 采集的交易中心编号列表（共 15 个） |
| `COLLECT_CATEGORY_PREFIX` | 采集类别前缀（工程建设类） |
| `API_URL` / `API_REFERER` | 官方列表接口地址与请求 Referer |
| `API_TIMEOUT` / `API_RETRY` / `API_PAGE_SIZE` | 接口超时秒数 / 重试次数 / 每页条数 |
| `API_MIN_INTERVAL` | 同一 Host 两次请求的最小间隔（秒），反爬保护 |
| `API_BREAKER_COOLDOWN` | 遇 403/429 熔断后的冷却时长（秒） |
| `API_BACKOFF_BASE` | 5xx 重试的指数退避基数 |
| `API_MAX_PAGES` | 单中心翻页上限保护 |
| `API_DAY_WINDOW` | 是否启用服务端"当日时间窗"过滤 |
| `CATEGORY_LIKE_TYPE` | `categorynum` 前缀匹配口径（必须与前端口径一致） |
| `DETAIL_URL_HOST` / `DETAIL_URL_TPL` | 详情页域名与地址模板 |
| `DETAIL_BODY_CLASS` / `DETAIL_TITLE_CLASS` | 详情页正文 / 标题容器的 class |
| `TIMEZONE` | 取"当天"所用时区 |
| `FOCUS_KEYWORDS` | 页面标题关键词预警词表（注意：与产品口径的"重点关注"是两套判定，见 §7） |
| `KEEP_DAYS` | 大于 0 时只保留最近 N 天采集原始响应，0 表示全部保留 |

### 5.4 凭证类（**只在 `.env` 中填写，绝不写入代码/文档/仓库**）

`.env.example` 中已预留以下键（默认被注释掉，需要时取消注释并填值）：

| 字段名 | 含义 |
| --- | --- |
| `WECHAT_PUSH_TOKEN` | 微信侧推送凭据（推送实现后使用） |
| `SSH_HOST` / `SSH_USER` | 部署同步用的服务器地址与登录用户名 |

另有一个由 `config.py` 直接读取、但**未写进 `.env.example`** 的键：

| 字段名 | 含义 |
| --- | --- |
| `GXZB_ENV` | 指定另一个 `.env` 文件的路径（多环境切换用，非凭证）；不设时默认读仓库根的 `.env` |

推送脚本落地时若需要新增键（如公众号 AppID/AppSecret、企业微信机器人地址、SSH 密钥路径等），**统一追加到 `.env` 与 `.env.example`（后者只留空键名）**，不得写进任何代码文件。

---

## 6. 数据说明

包内 `data/` 是样例运行数据；运行期产物分布在 `data/` 与 `SITE_DIR` 两处。

### 6.1 数据关系

```
官方接口 --collect.py--> data/raw/<day>/center-*.json（原始响应留档）
                        data/collect/<day>.json      （当日全量、按 infoid 去重）
                                   │
                     reconcile（缺失 = 全量 − 已入库）
                                   ▼
                        data/daily/<day>.json        （当日入库条目，页面数据源）
                                   │
   build_daily_page.py ───────────┼──> <SITE_DIR>/<day>.html（页面内嵌数据即由 daily JSON 渲染）
                                   ├──> <SITE_DIR>/logs/<day>.jsonl（运行日志，主键 infoid）
                                   └──> data/state/index.json（跨日去重台账）
   build_archive_page.py ─────────┼──> data/archive.json（归档首页数据源）
                                   └──> <SITE_DIR>/index.html
   diff_missing.py ───────────────────> <SITE_DIR>/reports/missing-<day>.{json,md}

   正文链路（可独立重跑）：
   data/daily 或 collect --fetch_detail.py--> data/details/<day>/<infoid>.json
                        --extract.py------> data/gxzb.sqlite3 + <SITE_DIR>/reports/extract-report-<day>.{json,md}
```

### 6.2 逐项说明与可否删除

| 数据 | 路径 | 内容与作用 | 能否删除 |
| --- | --- | --- | --- |
| SQLite 库 | `data/gxzb.sqlite3` | 结构化库：`notices`（公告主记录）、`notice_fields`（字段长表）、`projects`（项目归并）、`runs`（每次运行记录）；抽取结果的主存储 | **删了不致命但要重抓**：需重跑 `fetch_detail.py` + `extract.py`（要联网、耗时）才能恢复 |
| 正文缓存 | `data/details/<day>/<infoid>.json` | 每条约公告的清洗后正文 + 抓取状态；`_progress.json` 供续跑 | 同上，删后需重新抓取 |
| 页面数据源 | `data/daily/YYYY-MM-DD.json` | 当日入库条目（主键 `infoid`），页面渲染的直接来源 | 删后当日页无法重生成，需重跑采集 |
| 全量采集结果 | `data/collect/YYYY-MM-DD.json` | 当日全量去重结果，是"缺失对账"的基准 | 可删，重跑采集即重建 |
| 接口原始响应 | `data/raw/<day>/center-*.json` | 15 个交易中心的原始返回，用于回溯 | 可删（可用 `KEEP_DAYS` 控制保留天数） |
| 归档数据 | `data/archive.json` | 归档首页的数据源（按天汇总） | 可删，但首页会只剩当天；重跑各天可重建 |
| 跨日台账 | `data/state/index.json` | 记录每个 `infoid` 首次/最后出现日期，用于**跨日去重** | **不建议删**：删后历史公告可能被当新公告重新入库 |
| 运行摘要 | `data/state/run-<day>.json` | 当日各步骤成败、缺失数等 | 可删，重跑即重建 |
| 熔断状态 | `data/state/breaker.json` | 403/429 熔断后的冷却记录 | 可删（删后立即重试源站，不推荐在熔断期内删） |
| 页面备份 | `data/backup/backup_old_<day>.html` | 页面写盘前的旧产物备份，误生成可回滚 | 可删 |
| 抽取中间目录 | `data/extract/` | 预留目录，当前抽取结果直接进 SQLite 与 `reports/` | 可删（空目录） |
| 运行日志 | `<SITE_DIR>/logs/<day>.jsonl` | 一行一条，主键 `infoid`，含 `status` / `updated_at`，用于判重与失败重查 | 可删（重跑页面生成会重建），但会丢失历史 `status` 记录 |
| 核验报告 | `<SITE_DIR>/reports/*.json / *.md` | 缺失核验、采集对账、抽取报告 | 可删，重跑即重建 |

> 名词对照：文档与旧记录中出现的 "daily_data.json" 指的就是**当日入库数据文件**，在本项目中的实际路径是 `data/daily/YYYY-MM-DD.json`（页面 HTML 内嵌的 `RAW_DATA` 由它渲染），仓库中不存在同名独立文件。

---

## 7. 已确认的产品参数（已定口径，代码尚未实现）

> 以下为已与需求方确认的产品口径，**当前代码尚未实现**（详见 §8），接手后照此实现，不要自行发挥。

| # | 参数 | 口径 |
| --- | --- | --- |
| 1 | 每日汇总推送 | **17:30** 推送当天页面汇总（一条消息含当日页面入口与关键统计） |
| 2 | 重点关注即时推送 | 命中即推，不等到 17:30 |
| 3 | 「重点关注」判定条件 | 满足**任一**：①公告含**控制价**；②公告为**中标结果 / 中标候选人**公告；③公告金额 **≥ 3000 万元** |
| 4 | 推送去重 | 同一 `infoid` **当天只推一次**（需持久化"已推送"台账，建议复用 `store.py` 状态机与 `runs` 表） |
| 5 | 采集频率 | **10 分钟一轮**（用于即时推送的时效性；每日汇总仍在 17:30） |

与现状的差异（避免混淆）：

- 代码里现有的 `FOCUS_KEYWORDS`（默认一批工程类关键词）只是**页面上的标题预警标记**（`is_focus` / `focus_reason`），与第 3 条的「重点关注」判定条件是**两套不同口径**。
- `README.md` 与 `run_daily.py` 文档字符串中的调度示例仍是「每天 08:30 与 17:30 各跑一次」，与「10 分钟一轮采集 + 17:30 汇总」不一致，接入调度时需统一。

---

## 8. 当前进度与未完成待办

### 8.1 已完成

| # | 内容 | commit | 主要文件 |
| --- | --- | --- | --- |
| 1 | 每日流水线骨架：采集 → 入库对账 → 生成每日页 → 归档首页 → 缺失核验 → 可选外部推送；配置化、去除硬编码路径；废弃自造编号，全链路改用官方 `infoid` 为唯一识别码 | `cf1d061` | `run_daily.py`、`config.py`、`scripts/{collect,build_daily_page,build_archive_page,diff_missing,logstore}.py`、`templates/*.html`、`.env.example`、`README.md`、`docs/pipeline.md` |
| 2 | 缺失核验升级：按**页面已覆盖发布时间窗**自动区分「早前漏采」与「快照后新增」，真实遗漏单独成区标注 | `6a42b72` | `scripts/diff_missing.py` 等 |
| 3 | 采集加固 + 正文抓取 + 规则实体抽取 + SQLite 入库 | `2dd4e1e` | `scripts/{fetcher,collect,fetch_detail,extract,store}.py`、`extractors/*`、`tests/test_rules.py`、`docs/extraction-rules.md` |

实跑基线（2026-09-10 批次）：采集 15 中心，`totalcount_sum = 219 = fetched_sum`（差 0），按 `infoid` 去重后 111 条；正文抓取 111/111 成功；抽取成功 111 条、异常 0，成功率 100%，归出 102 个项目。

### 8.2 未完成

| # | 事项 | 现状证据 |
| --- | --- | --- |
| 1 | **流水线合并**：把 `fetch_detail.py` + `extract.py` 并入 `run_daily.py` 每日步骤 | `run_daily.py` 当前仍是 `1/6 … 6/6` 六步（采集 / 对账 / 每日页 / 归档 / 核验 / 推送），**没有**正文抓取与抽取步骤，也没有可一键回退的开关。此前只完成现状核查，代码未改动 |
| 2 | **推送实现**（公众号 / 企业微信） | 仓库内无任何推送脚本；`config.PUSH_CMD` 默认为空；`store.py` 状态机已预留 `pushed` 状态但无人写入 |
| 3 | **VPS 部署**（OpenResty 静态托管 + rsync 同步） | 未开始 |
| 4 | **调度挂载** | 未挂载；`README.md` 只有示例配置 |

### 8.3 其他遗留

| 事项 | 说明 |
| --- | --- |
| GitHub 远程地址 | 交接包**不含 `.git`**，新机恢复后如需版本管理，请在用户提供空仓库地址后重新初始化并推送 |
| 页面导航相对路径 | 当前导航链接依赖 `SITE_BASE_URL`，改相对路径后站点可放任意子目录 |
| 首页与每日页风格统一 | 两者视觉体系分别来自两个模板，待统一 |
| 缺失条目合并 | 2026-09-10 页面 61 条、全量 111 条，核验缺失 50 条（早前漏采 2 / 快照后新增 48），因 `RECONCILE=report` 未合并；确认真实遗漏后可用 `--reconcile merge` 并入 |
| 文档口径对齐 | `docs/extraction-rules.md` 正文与末尾小结两处仍写「23 条」用例（第 16、243 行），实际已增至 31 条 |

---

## 9. 技术基准（接口实测结论）

> 以下是最容易踩坑的部分，均已固化在 `config.py` 与 `scripts/collect.py` 中；改代码前先读本节。

### 9.1 端点与请求口径

| 项 | 值 |
| --- | --- |
| 列表接口 | `http://ggzy.jgswj.gxzf.gov.cn/inteligentsearchgxes/rest/esinteligentsearch/getFullTextDataNew` |
| Referer | `http://ggzy.jgswj.gxzf.gov.cn/nnggzy/jyxx/001001/tradeInfo.html` |
| 采集类别前缀 | `001001`（工程建设类） |
| 超时 / 重试 / 每页 | 30 秒 / 2 次 / 500 条 |
| 请求限速 | 同 Host 两次请求间隔 ≥ 3 秒 |
| 熔断 | 遇 403/429 熔断 15 分钟（状态落 `data/state/breaker.json`，跨进程生效） |
| 退避 | 5xx 按 2 的幂次指数退避；404 直接跳过不重试 |

### 9.2 分页、过滤、时间窗与对账

| 机制 | 口径 |
| --- | --- |
| 前缀过滤 | 必须在**服务端**完成：`condition = {"fieldName":"categorynum","equal":"<前缀>","isLike":true,"likeType":2}`。**把 `condition` 写成纯字符串会直接返回 500** |
| 分页 | `pn` 是 **0-based offset 语义**（不是页码），翻页必须 `pn += rn` 递增，直到累计条数达到该中心的 `totalcount`。**旧版固定 `pn=0 / rn=500`，当某中心当日条数超过 500 时会静默漏采——这是历史漏采的根因** |
| 每页上限 | `rn` 实测可探到 9999，默认 500 |
| 时间窗 | `time` 数组限定 `infodatepx` 为当日 `00:00:00 ~ 23:59:59`，配合 `totalcount` 得到"当日应有多少条"的权威口径 |
| 对账 | 逐中心比较 `fetched == totalcount`，汇总后两侧必须相等；不一致即视为漏采告警，结果落盘 `reports/collect-reconcile-<day>.json` |
| 分页保护 | 单中心翻页上限 200 页，防接口异常死循环 |

### 9.3 15 个交易中心编码

| 编码 | 中心 | 编码 | 中心 | 编码 | 中心 |
| --- | --- | --- | --- | --- | --- |
| 001 | 自治区本级 | 006 | 北海市 | 011 | 百色市 |
| 002 | 南宁市 | 007 | 防城港市 | 012 | 贺州市 |
| 003 | 柳州市 | 008 | 钦州市 | 013 | 河池市 |
| 004 | 桂林市 | 009 | 贵港市 | 014 | 来宾市 |
| 005 | 梧州市 | 010 | 玉林市 | 015 | 崇左市 |

### 9.4 类别编码（categorynum 结构）

`categorynum` = **`001001`（工程建设大类）+ 行业码（3 位）+ 环节码（3 位）**

| 行业码 | 含义 | 环节码 | 含义 | 环节内部键 |
| --- | --- | --- | --- | --- |
| 001 | 房建市政工程 | 001 | 招标计划 | `plan` |
| 002 | 水利工程 | 002 | 招标公告 | `notice` |
| 003 | 交通工程 | 003 | 澄清/答疑 | `clarify` |
| 004 | 铁路工程 | 004 | 控制价公示 | `control_price` |
| 005 | 其他项目 | 005 | 中标公示 | `candidate` |
|  |  | 006 | 中标公告 | `result` |

> 采集时只按前缀 `001001` 过滤，即"工程建设类的全部行业 × 全部环节"；不做行业/环节级细分。

### 9.5 详情页地址与正文容器

| 项 | 值 |
| --- | --- |
| 官方详情页模板 | `http://ggzy.jgswj.gxzf.gov.cn/gxggzy/projectDetails.html?infoid={infoid}&categorynum={categorynum}` |
| 抓正文优先地址 | 接口返回的 `linkurl`（静态 html，正文服务端渲染）；缺失时回退上述模板（前端路由页，通常无正文，会记为 fallback 且正文可能为空） |
| 正文容器 | `div.ewb-details-info`（按 div 配平截取后转纯文本） |
| 标题容器 | `.ewb-details-title` |
| 页面链接口径 | **不信任数据源 `url` 字段**（历史存在被截断、旧路径失效），页面链接一律由 `infoid + categorynum` 重新拼接 |

### 9.6 实跑基线（2026-09-10）

| 指标 | 结果 |
| --- | --- |
| 采集对账 | 15 中心，`totalcount_sum = 219`，`fetched_sum = 219`，差 0；去重后 111 条 |
| 正文抓取 | 111/111 取得正文，空正文 0 |
| 抽取 | 成功 111、异常 0，成功率 100%，`project_id` 归出 102 个项目 |
| 字段覆盖（条 / 111） | `contact_phone` 100、`tenderee` 94、`agency` 79、`contact_name` 57、`open_time` 51、`bid_method` 49、`period` 44、`winner` 36、`scale` 32、`contract_price` 31、`open_place` 31、`lots` 20、`candidates` 18、`qualification` 16、`members` 14、`bid_price` 11、`control_price` 8、`budget` 5、`estimate` 3 |
| 必填缺失 | 139 处：`bid_method` 62、`open_time` 60、`tenderee` 17（流标公示、控制价公告等环节本身不含这些字段，属正常缺失） |

---

## 10. 安全须知

### 10.1 必须重置 / 重建的凭证清单

> 以下凭据在开发过程中曾以明文形式出现过，**必须在系统正式启用前逐项重置／重建**，新值只写进新机的 `.env`。

| # | 事项 | 操作 |
| --- | --- | --- |
| 1 | 微信公众号 AppSecret | 到公众平台后台**重置 AppSecret**，新值只写进 `.env` |
| 2 | VPS root 密码 | **重置**为强密码，禁用密码登录，改为 SSH 密钥登录 |
| 3 | 企业微信 Webhook | **重建**群机器人 Webhook，新地址只写进 `.env` |
| 4 | 公众号 IP 白名单 | 按新部署环境的出口 IP（采集机 / VPS）重新配置，否则接口调用被拒 |
| 5 | 推送模板 ID | 推送形态确定后申请模板并登记模板 ID（写 `.env`） |

### 10.2 凭证落位铁律

- 凭证**只进 `.env`**（已被 `.gitignore` 忽略），代码、文档、README、commit message 一律不得出现明文值。
- **交接包不含 `.env`、不含 `.git`**，目的是避免本机配置与历史提交中的旧内容外泄；新机必须自己从 `.env.example` 生成 `.env` 并重新填入新凭据。
- 当需要把包再转交他人时：保持"不含 `.env`、不含 `.git`"，并在打包前重新执行一次明文凭证扫描（关键词如 secret / token / password / webhook / private key 等）。

### 10.3 合规与影响面

- 采集仅访问官方公开接口；受 `API_MIN_INTERVAL`（同 Host ≥3 秒）与重试上限约束，避免对源站造成压力。
- 运行数据（含公告正文）只留在本地 `data/` 与 `SITE_DIR`，不随仓库或交接包外发到公网。
- 涉及删除/覆盖的操作前先备份：页面重生成时会自动把旧产物备份到 `data/backup/`。

---

## 11. 踩坑记录

### 11.1 漏采根因：固定 pn 窗口

旧版采集固定 `pn=0 / rn=500`，某交易中心当日条数超过 500 时，超出部分被**静默丢弃且不报错**。修正：`pn` 按 offset 语义递增（`pn += rn`）直到取满该中心 `totalcount`，并新增逐中心对账落盘，把"靠人发现"变成"对账自动发现"。

### 11.2 抽取规则实跑修正的 4 处缺陷（已沉淀为回归用例）

| # | 缺陷 | 触发原文 | 修正 |
| --- | --- | --- | --- |
| 1 | 计算式残句被当成金额 | 「…发包人公布的最高投标限价的1.5%计算。」抽出 `rate=1.5%` | 噪音词表增加 计算/计取/折算/下浮；首字符为连词（的按以和及对由）直接拒绝 |
| 2 | 有效控制价被误杀 | 「为（人民币）：陆佰叁拾贰万壹仟贰佰肆拾柒元贰角叁分（¥6321247.23）；」超 40 字上限 | 上限 40 → 60，并允许句末标点后再判定 |
| 3 | 项目名残留年份片段 | 标题「2026年星岛湖镇…」被剥成「年星岛湖镇…」 | 新增 `strip_leading_numbering`：先剥 `YYYY年`，再剥「（1）」式序号 |
| 4 | 资质字段吞后续内容 | 「…（包括资格要求和加分业绩、诚信综合评价分）」被当作字段标签，值里混入开标时间/地点 | 新增 `label_context_ok` 上下文判定；可信位置优先、可疑位置兜底；值以并列连词开头则丢弃 |

修正后（2026-09-10 实测）：`control_price` 8 → 14（找回被长度上限误杀的有效控制价）、`budget` 5 → 6、`contract_price` 31 → 30 与 `bid_price` 11 → 10（剔除评标办法残句假值）、`qualification` 16 → 14（剔除括号内枚举项残片）；`project_name / tenderee / agency / open_time` 等主体字段命中不变、无误伤。

### 11.3 接口踩坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| HTTP 500 | 把 `condition` 写成纯字符串 | 必须用 `{"fieldName":..., "equal":..., "isLike":true, "likeType":2}` 结构 |
| 数据少了但接口没报错 | 固定 `pn` 窗口 | 改用 offset 递增分页 + `totalcount` 对账 |
| 链接打不开 | 数据源 `url` 字段被截断 / 旧路径失效 | 一律用 `infoid + categorynum` 重拼详情页地址 |
| 抓不到正文 | 用了前端路由页而非静态页 | 优先取接口返回的 `linkurl`，不要直接拼模板地址 |
| 被限流 / 403 / 429 | 请求过密 | 同 Host ≥3 秒间隔；触发熔断后等冷却（默认 15 分钟），不要立即重试 |

### 11.4 不可擅动的约束（红线）

1. **页面模块不可擅动**：`build_daily_page.py`、`build_archive_page.py`、`templates/*.html` 是已验收的视觉与数据形态，改动会触发设计基准 MD5 自检失败。确需改版：先改模板 → 同步更新 `TEMPLATE_PREVIEW_MD5` → 重跑生成脚本并跑通自检。
2. **唯一识别码只认官方 `infoid`**：自造的 `GX + 日期 + 序号` 编号已废弃；页面不展示编号、不提供编号检索；判重、日志、台账、入库全部以 `infoid` 为键。
3. **抽取只记录不推断**：不按标段拆行、不跨公告合并、不做 Excel/CSV 导出。
4. **只写不删**：重跑不删除历史数据；上游条目消失时只标记，不 `DELETE`。
5. **空值不落行**：字段抽取为空时不写噪音行（"宁缺勿错"，不用模板文字填充）。
6. **凭证零入库**：任何密钥、Token、密码只进 `.env`。

### 11.5 常见故障处理

| 现象 | 处理 |
| --- | --- |
| `collect` 退出码 3，个别中心失败 | 保留已采集结果继续生成；稍后重跑，判重不会产生重复条目 |
| `collect` 退出码 2 | 15 个中心全部失败（网络/接口维护），检查网络后重跑 |
| 自检 FAIL「设计基准未变」 | 模板被改；确认是有意改版则更新 `TEMPLATE_PREVIEW_MD5`，否则回滚模板 |
| 页面条目少于全量采集 | 看 `reports/missing-<day>.md` 的「早前漏采」分区确认真实遗漏，再用 `--reconcile merge` 并入后重生成 |
| `fetch_detail` 退出码 2 | 触发熔断；等冷却（默认 15 分钟）后重跑 |
| 同一公告重复出现 | 判重被绕过（手改了数据文件），以 `infoid` 去重后重写当日入库文件并重跑生成脚本 |
| 页面中文乱码（Windows） | 控制台执行 `chcp 65001`；文件本身均为 UTF-8，不影响产物 |

---

## 附录：接手第一天 Checklist

1. 解压，确认 `run_daily.py` / `config.py` / `.env.example` 在位。
2. `python3 -V` 确认 3.9+。
3. `cp .env.example .env`，改 `SITE_DIR`。
4. `python3 tests/test_rules.py` —— 31 条全绿。
5. `python3 run_daily.py --date 2026-09-10 --skip-collect` —— 不联网跑通页面链路。
6. 打开 `SITE_DIR` 下的 `2026-09-10.html` 与 `index.html` 目视确认。
7. 看 `SITE_DIR/reports/missing-2026-09-10.md`，理解两类缺失的含义。
8. 按 §10.1 逐项重置/重建凭证，配置公众号 IP 白名单。
9. 联网跑一次 `python3 run_daily.py`，确认当天全流程正常。
10. 按 §8.2 的 P0 顺序推进：流水线合并 → 推送实现 → VPS 部署 → 调度挂载。
*（内容由AI生成，仅供参考）*
