---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 3ae3ef24d3ae03b7c9ad9ad4585475c6_eb2c1978adf811f18f50525400aeaaa3
    ReservedCode1: sBIMvSv6jZRm/oaDUuzAQ+NnX4YR2VvbzbvUjcn+/vfTPlW6IuG6E8hU1a+0ZpRt6asX8df+XVgZ4leRI/lMgmbmKL2neZuBHvOv6Yq5/Ik/u8CU9GrqegFaV1WFqyir4V5ysGkqeNQZ2lK6P0BajW0AGt6q0kE1rHCBAoVicBdMv5MkevQzLKfHrNE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 3ae3ef24d3ae03b7c9ad9ad4585475c6_eb2c1978adf811f18f50525400aeaaa3
    ReservedCode2: sBIMvSv6jZRm/oaDUuzAQ+NnX4YR2VvbzbvUjcn+/vfTPlW6IuG6E8hU1a+0ZpRt6asX8df+XVgZ4leRI/lMgmbmKL2neZuBHvOv6Yq5/Ik/u8CU9GrqegFaV1WFqyir4V5ysGkqeNQZ2lK6P0BajW0AGt6q0kE1rHCBAoVicBdMv5MkevQzLKfHrNE=
---

# AI-HANDOFF —— gxzb-daily 交接文档（面向下一个接手的 AI）

> 读完本文件即可接续工作，无需翻阅任何历史对话。
> 最后更新：2026-09-11。仓库版本：git 提交 `2dd4e1e` 之后（另有未提交改动，见 §8.3）。
> 路径约定：本文以 `<PROJ>` 代指项目根目录，实际为
> `<你的工作目录>/output/gxzb-daily`（迁移后按实际解压位置替换）。

---

## 1. 项目目标与业务背景

**业务**：广西工程建设招投标交易平台（`http://ggzy.jgswj.gxzf.gov.cn`）每日发布大量招标/中标类公告。
**目标**：把这套人工查站的动作自动化，做成**无人值守的每日流水线**：

```
采集（官方接口拉当日公告） → 抽取（详情页正文结构化字段） → 建页（每日 HTML 汇总 + 归档） → 推送（每日汇总 / 重点关注）
```

**产出物**：面向业务人员的「今日公告看板」——按行业与地市分组、标注重点关注项、可点进详情，并推送到用户手边。

**当前阶段**：前三环（采集 / 抽取 / 建页）已在本地跑通并留有实跑数据；**第四环推送尚未落地**，见 §6.2。

---

## 2. 系统架构与各环节落地程度

| 环节 | 落地程度 | 入口脚本 | 主要产物 |
|---|---|---|---|
| 配置加载 | ✅ 完成 | `config.py`（根目录） | 环境变量 + 项目内配置 |
| 采集 | ✅ 完成，已实跑 | `scripts/run_daily.py`（调采集器） | `data/collect/<day>.json`、`data/logs/<day>.jsonl` |
| 抽取 | ✅ 完成，已实跑 | `extractors/*`（被 `run_daily` 调度） | `data/details/<day>/<infoid>.json`、SQLite `notice_fields` |
| 建页 / 归档 | ✅ 完成，已实跑 | `scripts/build_archive.py`（由 `run_daily` 调用） | `data/daily/<day>.json`、`data/site/<day>.html`、`data/archive.json` |
| 推送 | ❌ **未落地** | 待建 `scripts/push_*.py` | — |
| 调度（无人值守） | ❌ 未落地 | 待建 cron / launchd | — |
| 报告 | ⚠️ 部分 | `scripts/report_missing.py`（或同类） | `reports/missing-<day>.md` |

**一键入口**：`python3 scripts/run_daily.py`（详见 §5）。

---

## 3. 技术基准（实测定论，勿再重新试错）

### 3.1 列表接口：分页与参数

- **接口**：`POST /gxggzy/services/rest/notice/getNoticeList`（表单参数式，非 JSON body）。
- **`pn` 是 offset（偏移量），不是页码**。取第 2 页时 `pn=13`（当 `rn=13`），即 `pn = page_index * rn`。
  传 `pn=2` 会与第 1 页数据大量重叠——**这是最容易踩的坑**。实测证据：`pn=0` 与 `pn=13` 返回的 `infoid` 集合无交集。
- **`rn` 上限 = 13**。传大于 13 的值会被服务端截断/异常，稳定批量取数只能按 13 条/轮推进。
- **`categorynum` 是前缀过滤，格式为 `likeType=2` 的前缀匹配**：传 `001001` 可命中 `001001001/002/003...` 全部子类；
  传完整 12 位码则只命中该叶子类。**不要**传带通配符的字符串。
  叶子分类码结构（12 位）：`001001` + 二级(3位) + 三级(3位) + 四级(3位)，例：`001001003002`。
- **时间窗**：接口按**发布日期**过滤，只接受「日」粒度窗口（起止日期），不支持小时级；当日采集必须按 `当天 00:00:00 ~ 23:59:59` 拉，跨天需换窗口重拉。
- **`totalcount` 对账**：接口返回的 `totalcount` 是**该过滤条件下的总条数**，每轮采集结束必须用
  `已入库条数 vs totalcount` 做对账，差值即遗漏量，写入 `state/run-<day>.json` 的 `missing_count`。
  实测：某日 `totalcount=111`，实际入库 61，缺失 50（其中 2 条为早期漏采、48 条为快照后新增）。

### 3.2 15 个中心（采集范围）

广西壮族自治区公共资源交易中心 + 14 个地市中心，共 **15 个中心编码**，
逐个用 `categorynum` 前缀 + 各中心筛选条件拉取（编码清单见 `config.py` / `extractors` 内常量，勿硬编码到业务逻辑）。

### 3.3 详情页

- **地址格式**：
  `http://ggzy.jgswj.gxzf.gov.cn/gxggzy/projectDetails.html?infoid=<infoid>&categorynum=<categorynum>`
- **正文容器**：`parse_mode` 字段记录解析方式，实测稳定值为 `container`
  （正文在固定容器 div 内，非 iframe；`http_len` 典型 24k 左右）。
- **附件**：`attachments` 数组，多数公告为空 `[]`。
- **抓取节奏**：需限速 + 失败退避 + 熔断保护（见 `extractors/` 内限速与重试逻辑），
  对同一域名保持串行，避免被风控。

### 3.4 字段抽取

- 规则式抽取（非 LLM），核心在 `extractors/rules.py`：
  - `FIELD_LABELS`：字段 → 中文标签别名表（15 个业务字段）。
  - **必填字段** `REQUIRED_FIELDS = [project_name, tenderee, bid_method, open_time]`
  - **可选字段** `OPTIONAL_FIELDS = [agency, budget, estimate, control_price, contract_price, bid_price, open_place, period, candidates, winner, members, contact_name, contact_phone, qualification, scale, lots]`
  - 金额字段 `MONEY_FIELDS = [budget, estimate, control_price, contract_price, bid_price]`，
    命中 `MONEY_NOISE_TOKENS`（`【`、`】`、`或…`、`每平方米`、`元/`、`详见` 等模板残句标记）即判为无效值丢弃。
  - 数值出现「同页多候选」时按段落终止词 `SEGMENT_STOPS` 截断，避免吞掉后续字段。
- 抽取结果两处落盘：SQLite `notice_fields`（便于查询）+ `data/details/<day>/<infoid>.json`（原文留档）。
- 抽取命中率基线见 `runs` 表的 payload 与 `docs/extraction-rules.md`；已知会因「评标办法残句」「括号内枚举残片」产生假值，
  已在 2026-09 做过一轮降噪（`budget`/`contract_price`/`bid_price`/`qualification` 各减少 1~2 条假值），**主体字段未误伤**。

---

## 4. 代码结构与职责

```
<PROJ>/
├── config.py                  # 配置加载（环境变量优先），含中心编码、路径常量
├── scripts/
│   ├── run_daily.py           # ★ 每日主流水线：采集 → 抽取 → 建页/归档 → 报告（推送待接）
│   ├── build_archive.py       # 生成 data/site/<day>.html 与 data/archive.json
│   ├── report_missing.py      # 生成 reports/missing-<day>.md（缺失/待采清单）
│   ├── collect.py             # 列表接口采集（分页 pn/rn、时间窗、totalcount 对账）
│   ├── fetch_detail.py        # 详情页抓取（限速/退避/熔断）
│   ├── store_sqlite.py        # 写入 SQLite（notices / notice_fields / projects / runs）
│   ├── logstore.py            # 运行日志与 infoid 台账（每日 jsonl + state/index.json）
│   └── archive.py             # 归档辅助
├── extractors/
│   ├── rules.py               # 字段标签表、必填/可选字段、金额噪音、标段正则、段落终止词
│   ├── parse.py               # 正文 → 字段值
│   ├── normalize.py           # 文本清洗、机构名规整、去重保序
│   └── ...
├── templates/                 # 页面模板（每日看板 HTML）
├── tests/                     # 测试
├── docs/
│   ├── AI-HANDOFF.md          # ★ 本文件（面向 AI）
│   ├── HANDOFF.md             # 面向人的换机恢复手册
│   ├── extraction-rules.md    # 抽取规则说明
│   └── pipeline.md            # 数据契约 / 流水线设计
├── data/                      # 数据（见 §5）
├── reports/                   # 对账与缺失报告（Markdown）
└── .env                       # 凭证（仅本地，禁止入仓、禁止进 AI 交接包）
```

**运行命令**：

```bash
cd <PROJ>
python3 scripts/run_daily.py            # 全流程（默认跑「今天」）
python3 scripts/run_daily.py --date 2026-09-10   # 补跑指定日期
```

**退出码约定**：`0` = 全流程成功；非 `0` = 有步骤失败。
失败步骤名会写入 `data/state/run-<day>.json` 的 `failed_steps` 数组；
**`failed_steps` 非空即视为当日流水线未完成**，需按该数组定位重跑对应步骤。

---

## 5. 数据存放位置与字段说明

### 5.1 SQLite（`data/gxzb.db` 及同类，4 张表）

| 表 | 行数基线（2026-09-10） | 作用 |
|---|---|---|
| `notices` | 141 | 公告主表（列表级信息） |
| `notice_fields` | 1616 | 抽取出的结构化字段（一公告多字段行） |
| `projects` | 130 | 项目维度聚合 |
| `runs` | 18 | 每次运行的统计与 payload（含字段命中统计） |

> 表结构以实际建表语句为准（可 `sqlite3 <db> .schema` 查看）；`runs` 表是排查历史运行的第一现场。

### 5.2 文件数据

| 路径 | 内容 | 关键字段 | 可否删除 |
|---|---|---|---|
| `data/collect/<day>.json` | 列表接口原始采集结果 | `infoid, title, categorynum, industry, stage, stage_key, badge_class, areacode, areaname, pub_time, link, detail_url, is_focus, focus_reason, owner` | ⚠️ 建议保留（对账/复现） |
| `data/details/<day>/<infoid>.json` | 详情页正文留档（一条一文件） | `infoid, url, title, pub_time, body, body_len, attachments, parse_mode, source_row, fetched_at, http_len, status` | ⚠️ 建议保留（重跑抽取的唯一原料） |
| `data/daily/<day>.json` | 当日建页用数据（**已裁剪**） | `infoid, title, categorynum, industry, stage, stage_key, badge_class, areacode, areaname, pub_time, link, is_focus, focus_reason, owner` | 可重建（由 collect+details 生成） |
| `data/site/<day>.html` | 当日看板页面 | — | 可重建 |
| `data/archive.json` | 归档索引 | 顶层 `site, subtitle, generated, day_count, total_all, days`；`days[]` 每项 `date, file, total, cities, cat_count, groups{行业:条数}, updated` | ❌ 勿手改（由 build_archive 生成） |
| `data/logs/<day>.jsonl` | 每日运行日志，一行一条 JSON，**主键 infoid** | `infoid, title, areaname, industry, stage, pub_time, link, status, updated_at`（`status` 默认 `collected`） | ❌ 勿删（失败重查依赖它） |
| `data/state/index.json` | 全局 infoid 台账 | 顶层 `count, items, updated_at, version`；`items: infoid -> {date, status, title, first_seen, last_seen}` | ❌ 勿删（去重/去重推的依据） |
| `data/state/run-<day>.json` | 当日运行收口 | `date, finished_at, failed_steps[], missing_count, stale_missed_count, pending_count, collect_total, page_total, report_md` | ⚠️ 保留（对账） |
| `reports/missing-<day>.md` | 缺失/待采清单报告 | — | 可重建 |

**语义要点**：
- `status`：`collected` = 已采集入库（还有失败态取值，代码中以 `STATUS_` 常量定义，`status != collected` 的条目可按 `infoid` 重取）。
- `is_focus` / `focus_reason`：关注标记与理由（如 `["命中关键词: 公路"]`），是推送环节的直接依据。
- 采集 → 入库存在差值是**正常现象**：`collect_total(111) → page_total(61) → missing_count(50)`，
  其中 `stale_missed_count`（早期漏采未补）与 `pending_count`（快照后新增、次日可采）需分开看。

---

## 6. 当前进度

### 6.1 已完成

1. 列表接口逆向完成：`pn`=offset、`rn`≤13、`categorynum` 前缀过滤（`likeType=2`）、时间窗、`totalcount` 对账。
2. 15 个中心采集范围确定并跑通。
3. 详情页抓取跑通（容器解析，含附件与正文长度校验）。
4. 规则式字段抽取落地，15 字段 + 必填/可选划分 + 金额降噪，假值过一轮清洗。
5. SQLite 四表落库、每日 jsonl 日志 + 全局 infoid 台账（`state/index.json`）。
6. 每日建页 HTML + `archive.json` 归档索引生成。
7. `reports/missing-<day>.md` 缺失对账报告。
8. 实跑基线（2026-09-10）：`collect_total=111`，`page_total=61`，`missing_count=50`（`stale=2` + `pending=48`），`failed_steps=[]`；归档统计 `days[0]`：61 条 / 14 地市 / 5 类行业（房建市政 42、水利 10、交通 4、其他 4、铁路 1）。
9. 本轮新增本文件（AI 交接）与双 zip 打包脚本。

### 6.2 未完成（按优先级）

| 优先级 | 待办 | 说明 |
|---|---|---|
| **P0** | **推送环节落地** | 按 §7 产品参数实现：每日 17:30 汇总推 + 重点关注实时推 + 同 infoid 当天只推一次。推送通道（企业微信/其它）需与用户确认，token/Webhook 只进 `.env`。 |
| **P0** | **流水线合并未落地** | 目前各步骤靠 `run_daily.py` 串联，**日期级的采集窗口 + 抽取 + 建页 + 推送尚未合并为一次原子运行**；跨天边界、部分失败重入、`pending_count` 顺延次日补采这三件事需要显式实现并测试。 |
| **P1** | 调度（无人值守） | 采集 10 分钟一轮的定时器（cron / launchd），含失败告警。 |
| **P1** | 缺失补采闭环 | `pending_count` / `stale_missed_count` 自动顺延次日补采，并回写 `runs`。 |
| **P2** | 抽取规则继续降噪 | 针对「评标办法残句」「括号枚举残片」迭代 `MONEY_NOISE_TOKENS` 与 `SEGMENT_STOPS`。 |
| **P2** | 详情页增量更新 | 公告正文常有更正/补充，需支持同 `infoid` 二次抓取覆盖。 |

---

## 7. 已确认的产品参数（**不可自行更改，改动需用户确认**）

1. **每日 17:30 汇总推**：当日全部公告汇总推送一次。
2. **重点关注立即推**：命中以下任一条件时，**不等 17:30，立即推送**：
   - 含**控制价**类公告；
   - **中标结果**与**中标候选人**公示；
   - 金额 **≥ 3000 万**。
3. **同一 `infoid` 当天只推一次**（去重依据 `state/index.json` + 当日推送记录）。
4. **采集 10 分钟一轮**。

---

## 8. 红线与约束

### 8.1 数据与文件

- **不得擅动**：`data/archive.json`、`data/logs/*.jsonl`、`data/state/index.json`（均为算法产物/台账，手工改会破坏去重与对账）。
- **不得删除**：`data/details/**`（重跑抽取的唯一原料）。
- **只动不删**：任何批量文件操作保持可逆，先预览后执行。

### 8.2 凭证

- 所有密钥/Webhook/账号密码**只能写在 `.env`**。
- **严禁**在代码、文档、日志、组件、仓库任何位置出现**明文凭证**；日志与报告输出前必须做凭证脱敏。
- `.env` **永不入仓**（`.gitignore` 已覆盖），**永不进入 AI 交接包**。

### 8.3 版本控制现状（接手时先处理）

- 当前分支有**未提交改动**：`docs/extraction-rules.md`（+12 行，为文档头 AIGC front-matter 与文末「内容由AI生成，仅供参考」声明）。
- 有**未追踪文件**：`docs/HANDOFF.md`（以及本轮新增的 `docs/AI-HANDOFF.md`）。
- 接手后建议先 `git status` 确认，再决定提交或 stash，**不要**无脑 `git checkout .`。

---

## 9. 已知坑与常见故障处理

| # | 坑 / 症状 | 处理 |
|---|---|---|
| 1 | 翻页后数据与首页大量重复 | `pn` 是 **offset**，必须 `pn = page * rn`（`rn=13` 时第 2 页 `pn=13`）。 |
| 2 | 一次拉取条数始终不超过 13 | `rn` 上限就是 13，属服务端行为，不是 bug；靠多轮 `pn` 递进。 |
| 3 | 过滤后条数远少于预期 | `categorynum` 是**前缀过滤**，别传完整 12 位去捞整个大类；也确认 `likeType=2` 参数已带。 |
| 4 | 采集条数与 `totalcount` 不一致 | 正常。差值写入 `missing_count`，区分 `stale_missed_count`（旧漏）与 `pending_count`（新出），后者次日补采。 |
| 5 | 抽取出的金额是残句/枚举 | 命中 `MONEY_NOISE_TOKENS` 的值必须在清洗阶段丢弃；新增噪音样本请补进该常量。 |
| 6 | 详情页抓取被风控 | 保持串行 + 限速 + 指数退避 + 熔断；不要并发轰炸同一域名。 |
| 7 | 建页内容与实际不符 | `build_archive.py` 依赖 `data/daily/<day>.json`，先确认该文件已由当日 `run_daily` 生成，再重建。 |
| 8 | 当日流水线「看似成功」实际有步骤失败 | 只看 `data/state/run-<day>.json` 的 `failed_steps`；**非空即未完成**，按数组重跑对应步骤。 |
| 9 | 迁移到新机器后路径全变 | 项目内路径均基于 `config.py` 的根目录推导，**不要**硬编码绝对路径；`.env` 里若有绝对路径需同步更新。 |

---

## 10. 给下一个 AI 的启动清单

1. `cd <PROJ>`，读 `docs/pipeline.md`（数据契约）与 `docs/extraction-rules.md`（规则细节）。
2. `git status` + `git log --oneline -5`，处理 §8.3 的未提交改动。
3. 确认 `.env` 存在且字段齐全（**只读，不打印内容**）：`ls -l .env` 即可，切勿 `cat`。
4. 小样本验证链路：`python3 scripts/run_daily.py --date <近一日>`，看退出码与 `data/state/run-<day>.json:failed_steps`。
5. `sqlite3 <db> "select count(*) from notices;"` 等核对 SQLite 行数基线（§5.1）。
6. 接 **P0 推送**：先与用户确认推送通道与凭证获取方式，再动 `scripts/`，凭证只进 `.env`。
7. 推进 **P0 流水线合并**：把「日期窗口采集 + 抽取 + 建页 + 推送 + 补采顺延」合并为一次原子运行，并补测试。

---

*本文档由 AI 生成，仅用于跨机/跨 AI 交接；内容以代码与实测数据为准，如与代码冲突，以代码为准。*
*（内容由AI生成，仅供参考）*
