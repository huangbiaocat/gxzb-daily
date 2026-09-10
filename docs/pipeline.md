# 数据契约与运维细则

本文件描述各脚本产出的数据结构、接口口径与常见故障处理步骤，供后续维护（无 AI 参与）时对照使用。

## 1. 数据流

```
官方接口(15 交易中心) --collect.py--> data/raw/YYYY-MM-DD/center-*.json
                                      data/collect/YYYY-MM-DD.json   （全量去重，按 infoid）
                                                |
                              reconcile（缺失 = 全量 - 已入库）
                                                v
                                      data/daily/YYYY-MM-DD.json     （当日入库，主键 infoid）
                                                |
                        build_daily_page.py ---+---> <SITE_DIR>/YYYY-MM-DD.html
                                                |     <LOG_DIR>/YYYY-MM-DD.jsonl
                                                |     data/state/index.json
                                                v
                       build_archive_page.py -----> data/archive.json + <SITE_DIR>/index.html
                       diff_missing.py -----------> <REPORT_DIR>/missing-YYYY-MM-DD.{json,md}
```

## 2. 数据结构

### 2.1 `data/daily/YYYY-MM-DD.json`（入库条目，数组）

| 字段 | 说明 |
| --- | --- |
| `infoid` | **官方唯一识别码**（接口返回 uuid），判重与溯源主键；兼容旧数据的 `id` 字段，读入时会归一化到 `infoid` |
| `title` | 公告标题 |
| `categorynum` | 官方类别编码（`001001` 起为工程建设类），用于拼详情页链接 |
| `industry` | 工程大类（如 交通工程、房建市政工程） |
| `stage` / `stage_key` | 业务环节中文名 / 内部键（plan、notice、clarify、control、candidate、result） |
| `badge_class` | 环节徽章样式（渲染用） |
| `areacode` / `areaname` | 地市（行政区划）编码与名称 |
| `pub_time` | 发布时间 `YYYY-MM-DD HH:MM:SS` |
| `link` | 官方详情页地址，由 `infoid + categorynum` 生成 |
| `is_focus` / `focus_reason` | 是否命中重点预警关键词及命中原因 |
| `owner` | 招标人（源数据为空时保留空串） |

### 2.2 `<LOG_DIR>/YYYY-MM-DD.jsonl`（运行日志，一行一条）

```json
{"infoid": "...", "title": "...", "areaname": "玉林市", "industry": "交通工程",
 "stage": "招标公告", "pub_time": "2026-09-10 15:53:30", "link": "http://...",
 "status": "collected", "updated_at": "2026-09-10 21:51:18"}
```

- 键：`infoid`。同一天重跑时同一条公告只刷新 `status` / `updated_at`，不重复入库。
- `status` 取值：`collected`（已正常入库）、`failed`（生成阶段异常）、`pending`（待重取）。
- 待重取清单：`status != collected` 的条目，用 `--refetch` 导出，或读 `data/state/refetch-YYYY-MM-DD.json`。

### 2.3 `data/state/index.json`（全局台账）

```json
{"count": 61, "updated_at": "2026-09-10 21:51:18",
 "items": {"<infoid>": {"first_seen": "2026-09-10", "last_seen": "2026-09-10", "title": "..."}}}
```

用于跨日判重：同一公告次日再次出现时不会被当成新公告重复入库（`collect.py` 的 `fresh` 计数即"台账中未曾出现过的条数"）。

### 2.4 `<REPORT_DIR>/missing-YYYY-MM-DD.json`

```json
{"date": "2026-09-10", "collect_unique": 110, "page_total": 61,
 "missing_count": 49, "extra_count": 0, "missing": [ {...完整条目...} ], "extra": []}
```

## 3. 接口口径

- 采集端点：官方公共资源交易公告列表接口，按交易中心编号（`COLLECT_CENTERS`）逐一分页拉取。
- 采集类别：`COLLECT_CATEGORY_PREFIX` 默认 `001001`（工程建设类）。
- 请求策略：超时 `API_TIMEOUT` 秒、失败重试 `API_RETRY` 次、每页 `API_PAGE_SIZE` 条；单次任务请求量小且有上限，不构成对源站压力。
- 数据缺陷：源数据 `url` 字段历史上存在被截断、旧路径失效问题，故链接一律由 `infoid + categorynum` 重新拼接，不采用源 `url`。

## 4. 常见故障处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 采集步骤退出码 3，日志显示部分中心失败 | 个别中心接口超时/维护 | 保留已采集结果继续生成；稍后重跑一次，日志判重不会产生重复条目 |
| 采集步骤退出码 2，任务终止 | 15 个中心全部失败（网络/接口维护） | 检查网络与接口地址，重跑 `run_daily.py` |
| 生成页自检 FAIL：「设计基准未变」 | `templates/index-preview.html` 被修改 | 若确为有意改版，更新 `config.PREVIEW_MD5` 后重跑；否则回滚模板 |
| 自检 FAIL：「无自造编号」 | 有人往模板里加回了编号渲染 | 检查模板是否残留 `code-tag` / `data-code` 类元素 |
| 页面条目少于全量采集 | 入库阶段未合并 | 查看 `missing-YYYY-MM-DD.md`；先看「早前漏采」分区（发布时间落在页面时间窗内，属真实遗漏），确认无误后用 `--reconcile merge` 并入后重生成 |
| 同一公告重复出现 | 判重被绕过（手改了数据文件） | 以 `infoid` 去重后重写当日入库文件，重跑生成脚本 |

## 5. 缺失分类口径（早前漏采 vs 快照后新增）

`diff_missing.py` 在「缺失 = 全量采集 - 页面」基础上，用**页面已覆盖发布时间窗**（`page_window` = 页面/入库数据中最早与最晚的 `pub_time`）自动再分类：

- `stale_missed` 早前漏采：`pub_time ≤ page_window.end` → 页面快照生成时该公告已在官方接口存在却未入页，报告中单独成区并标注；
- `pending` 快照后新增：`pub_time > page_window.end` → 页面生成后新发布，等下一次流水线采集入库，不算遗漏；
- 页面窗口为空或无 `pub_time` 时归 `unknown`，交人工确认。

判据只依赖产物文件本身，可复现、可回归，不需要人工记忆；JSON 报告含 `page_window` / `stale_missed_count` / `pending_count` / `stale_missed`，每条缺失带 `miss_type` / `miss_reason`。

## 6. 重跑与幂等

- 任意步骤都可单独重跑，重复运行由 infoid 判重保证幂等：
  - 重新采集：`python3 scripts/collect.py --date YYYY-MM-DD`
  - 重新生成页面：`python3 scripts/build_daily_page.py --date YYYY-MM-DD`
  - 重新核验：`python3 scripts/diff_missing.py --date YYYY-MM-DD`
- 页面写盘前会把旧产物备份到 `data/backup/backup_old_YYYY-MM-DD.html`，误生成可回滚。
- 生成脚本会把当日入库文件里的 `infoid` 归一化后原样使用，不做任何编号分配，因此**不会漂移**。

## 7. 改版与扩展

- **调整视觉**：更新 `templates/index-preview.html`（每日页基准）或 `templates/index-sample.html`（归档页），同步更新 `config.PREVIEW_MD5`，重跑生成脚本；样式在构建时内联提取，页面无外部 CDN 依赖。
- **新增交易中心或类别**：改 `.env` 的 `COLLECT_CENTERS` / `COLLECT_CATEGORY_PREFIX` 即可，无需改脚本。
- **接入推送**：把推送逻辑写成独立脚本，将命令填到 `PUSH_CMD`，即纳入每日任务第 6 步；凭证只放 `.env`。
