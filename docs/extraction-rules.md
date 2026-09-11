# 抽取规则说明（extraction-rules）

P1 阶段新增的「公告正文 → 结构化字段」规则手册。全部规则由 `extractors/` 下的纯 Python 正则与词表实现，**不调用任何 AI、不引入第三方库**，可随流水线无人值守运行。

本文与代码一一对应，改规则时请同步改本文；回归用例见 `tests/test_rules.py`（23 条，全部针对真实误报）。

---

## 1. 模块与数据流

```
接口列表(collect.py) → notices 原始记录（infoid 为主键）
        ↓
fetch_detail.py   按 infoid+categorynum 抓官方详情页，抽正文 → data/details/<day>/<infoid>.json
        ↓
extract.py        rules.extract_notice() 抽字段 → 写 notice_fields / 重建 projects → runs 登记
        ↓
store.py          SQLite 四表：notices / notice_fields / projects / runs
```

| 文件 | 职责 |
| --- | --- |
| `extractors/normalize.py` | 文本预处理、金额/日期/比率标准化、机构名清洗与判定、通用取值清洗 |
| `extractors/rules.py` | 字段标签词表、机构名/联合体/标段/资质/规模/工期/联系人抽取、project_id 生成、extract_notice() 主入口 |
| `scripts/fetch_detail.py` | 详情页抓取（同 Host 间隔 ≥3s、403/429 熔断 15min、5xx 指数退避、404 不重试） |
| `scripts/extract.py` | 遍历当日公告 → 抽取 → 入库 → 产出 extract-report-<day>.json/.md |
| `scripts/store.py` | SQLite 建表、写入、状态机、项目归并、运行记录 |
| `tests/test_rules.py` | 规则回归测试（`python3 tests/test_rules.py`） |

约定：`extractors` 只做「规则 + 纯函数」，不做 IO；网络在 `scripts/fetch_detail.py`、库在 `scripts/store.py`。

---

## 2. 文本预处理 normalize_text

1. 去零宽字符（U+200B / U+200C / U+200D / U+FEFF / U+2060）。
2. 换行与空白统一：CRLF 转 LF，全角空格转半角。
3. 连续空格压缩为单个空格，行首行尾去空白。
4. 其余全角字符不动，避免破坏 `（ ）`、`：` 等标签语境。

页面正文里的表格会以「标签 + 值」的行文形式出现，规则一律基于该行文取值。

---

## 3. 金额规则 parse_money

统一输出到**元**，返回 `{value, kind, raw, unit_inferred}`。

### 3.1 判定优先级（自上而下，命中即返回）

| 序 | 情形 | 处理 |
| --- | --- | --- |
| 1 | 空值词：空串、`/`、`-`、`—`、无、不报价、未报价、空、略 | value 为空，kind 为 empty |
| 2 | 费率型：含百分号，或含 下浮率/下浮/费率/折扣率/折扣/优惠率/让利率/浮动率 | **不换算**，value 即百分比数值（5.0 表示 5%），kind 为 rate |
| 3 | 人民币符号后的阿拉伯数字 | 取该数字，精确到元，优先级最高（应对「中标金额（¥2379618.00元）」） |
| 4 | 汉字大写金额串（数字字 + 万/亿 + 元或圆） | 汉字转整数，**不再乘单位**（「壹佰玖拾肆万零陆佰陆拾柒元肆角整」→ 1940667） |
| 5 | 阿拉伯数字 + 单位 | 亿乘 1e8、万乘 1e4、千元乘 1e3、元乘 1 |
| 6 | 无单位纯数字 | 仅当**整段基本就是一个数字**（正则 `^[^0-9]{0,8}\d+(\.\d+)?[^0-9]{0,4}$` 且长度 ≤25）时按 default_unit（默认元）采信，置 unit_inferred=True |
| 7 | 无元/万/亿后缀的汉字数字 | 仅长度 ≥3 时采信，避免把「一」「二」当金额 |
| 8 | 其他 | value 为空，kind 为 unknown |

### 3.2 kind 语义（易误读，务必对齐）

- kind 是**数量级/类型标记**，不是原始单位：yi（≥1 亿元）、wan（≥1 万元）、qian、yuan（<1 万元）、rate、empty、unknown。
- value 恒为**元的数值**（rate 除外，为百分比数值）。

### 3.3 展示格式 format_money

≥1e8 输出「x.xx亿元」，≥1e4 输出「x.xx万元」，否则「x.xx元」，整尾 `.00` 去零。入库写入 notice_fields.value_text 用该格式，value_num 存元数值、value_unit 存「元」、value_type 为 money。

### 3.4 误报防护（实测踩坑）

- 条款编号 `2.2.2`、「按本表2.2.2内容计算评标基准值(A)。」曾被抽成 2.20 元假控制价，现由第 6 条正则拦下。
- 金额标签后若带 `【`、`】`、或、详见、每平方米、`元/` 等噪音（MONEY_NOISE_TOKENS），find_money 直接判定该单元格无效，宁缺勿错。

---

## 4. 日期规则 parse_date

- 识别 `YYYY-MM-DD`、`YYYY/M/D`、`YYYY.M.D`、`YYYY年M月D日`（分隔符可混用），可选时间后缀 `HH:MM`（T 或空格分隔）。
- 输出 `YYYY-MM-DD HH:MM`；**只有日期时补 00:00**；月/日越界（13 月、32 日）拒绝返回空；时 >23、分 >59 归零。
- pub_time 优先取官方接口字段（ctx 中的 pub_time），能解析则解析，否则原样保留。

---

## 5. 机构名规则（重点）

### 5.1 规范化流水线 clean_org_name

按顺序执行：

1. 去零宽字符。
2. 去掉尾部「（以下简称甲方）」「（简称…）」。
3. 去掉行首身份前缀：中标人、中标单位、第一中标候选人、成交供应商、投标人、招标人 等（含冒号）。
4. 去掉行首系词 为/系/是/由（「招标人为XX公司」取值残留）。
5. strip_alias_paren 剥离别名括号。
6. strip_org_prefix 剔除标段/包组前缀（一标段、2分标、A包 等）。
7. truncate_at_suffix 在**法定后缀白名单最后一次出现处**截断。
8. 按 ORG_ATTR_KEYWORDS（地址/所在地/法定代表人/统一社会信用代码/注册地/联系电话…）做**属性词断言截断**，截断点须 ≥1，避免误伤。
9. 收尾去标点。

### 5.2 法定后缀白名单 ORG_SUFFIXES

公司类（集团有限责任公司/有限责任公司/股份有限公司/有限公司/公司）、设计研究类（勘察设计研究院/设计院/研究院/研究所/科学院）、监理咨询造价类（监理有限公司/工程咨询有限公司/招标代理有限公司/事务所）、施工建设类（建设工程有限公司/建工集团/建设集团有限公司）、企事业单位与政府机构（农村商业银行/供电局/水务集团/大学/学院/医院/管理局/水利厅…）。

截断取「最靠右的结尾」，保证长后缀不被短后缀抢先截成半截名。

### 5.3 别名括号剥离 strip_alias_paren

括号内容命中 原/原名/曾用名/前身/更名/简称/以下简称/下称/又名/亦名 则**整段括号删除**；命中机构特征词（公司/集团/院/中心/银行/局/联合体/事务所）则**保留**（可能是联合体成员）；其他 ≤12 字保留、>12 字删除。

### 5.4 是否算机构名 looks_like_org

- 长度 4~60；
- 命中 ORG_FEATURE_WORDS（公司/集团/院/中心/银行/局/厅/厂/社/所/站/校/大学/医院/政府/管理局…）；
- **长度 ≥3 的特征词**只需出现在文本中；**短特征词（所/站/会/社/厂/校/院/局/厅）必须落在词尾**才算数。

> 「短词须词尾」是修掉「各投标人可就招标项目的**所有**标段进行投标」被整句当成机构名的关键。

### 5.5 字段级校验 valid_org_field

在 looks_like_org 之外追加：长度 4~40、不含句读（逗号/句号/分号/问号/感叹号）、不含 ORG_INVALID_TOKENS（根据/按照/的规定/有关规定/备案/有关/应当/负责…）、空格数 <2。满足「含特征词」或「结尾命中法定后缀」二者之一即通过。招标人、招标代理、联合体成员均须过此校验才入库。

### 5.6 抽取来源 extract_orgs / split_org_cell

- extract_orgs：全文按机构名正则（2~60 字 + 法定后缀）扫描 → _trim_junk_start 砍掉「综合楼工程中标候选人」这类前缀 → clean_org_name → 黑名单（招标代理机构/项目名称/工程名称 等模板词）与无效词过滤 → 去重。
- split_org_cell：把「公司、公司」「公司 公司」「公司与公司」切成成员列表，供联合体使用。

---

## 6. 联合体成员 extract_members

1. 取「联合体成员名单/联合体成员/联合体各方/联合体牵头人/联合体/成员单位」之后的段落，在 第二中标/第三中标/分号/中标候选人/中标人/公示期 处**硬截断**，避免把下一候选人的名单误收。
2. 另从标题与正文中的「联合体：xxx」括号短语补抽。
3. 成员名清洗：剥掉 成员单位、成员、牵头人、主办方、各方 等前缀。
4. 逐名过 valid_org_field，长度 ≥4，去重，最多 10 个。

---

## 7. 标段列表 extract_lots（只记录列表，**不做拆行**）

1. **扫描来源**：标题与正文，按标段正则（前缀 0~30 字 + 标段/标包/分标/合同包/标项 + 可选序号）。
   - 关键负向断言 `(?<!招)标项`：避免「招**标项**目」被当成标段。
2. **噪音过滤 LOT_NAME_NOISE**：命中 本项目/本标段/各标段/多标段/个标段/标段划分/标段数量/招标/投标/中标/应当/根据/规定/允许/编号/名称/否则/同一人/拟任/不得/参与/顿号 即丢弃。
3. **段落补充**：取「标段名称/标段划分」之后的段落，按 顿号/逗号/分号/换行 切分，要求同时满足：
   - 长度 3~40；
   - **必须命中标段类词**（标段/标包/分标/合同包/标项），用于剔除说明性长句（「各投标人可就……所有标段……」本身不含标段名）；
   - 不命中噪音词。
4. **_tidy_lot 收尾清洗**：去空白与换行，去掉紧随「标段」后的残号（「…标段 + 换行 + 4」→「…标段」）。
5. 去重保序，最多 20 条。

---

## 8. 中标候选 / 中标人

- extract_candidates：取「中标候选人」段落中的机构名列表。
- extract_winner：优先取「中标人/中标单位/成交供应商」标签值（过 valid_org_field）；取不到时**回退**为候选名单首位，并在 evidence.winner 标注「候选名单首位(回退)」以便区分证据强度。

---

## 9. 其他字段

| 字段 | 规则要点 |
| --- | --- |
| bid_method | 方式枚举词命中（公开招标/邀请招标/竞争性磋商/竞争性谈判/单一来源…） |
| open_time | 取「开标时间/投标截止时间…」标签值并 parse_date；取不到时回退「公告发布时间」类标签 |
| open_place | 取「开标地点/开标地址/递交地点…」标签值，clean_value 截断 |
| period | parse_duration：取首个含数字片段，识别 日历天/天/日/个工作日/个月/月/年，月按 30 天、年按 365 天折算入 days（仅排序参考）；**无数字则 raw 置空视为缺失**，避免把模板残句当工期 |
| qualification | 「资质要求」标签后最多 4 行 / 120 字；砍掉 `【`、备注、中标价、项目负责人、投标人须知 等后续字段尾巴；若正文含「具备/具有/须具有/应当具有」，从该处起头，保证值是「具备……资质」的完整表述 |
| scale | 「建设内容/规模」标签后最多 5 行 / 180 字，去空行，≤300 字 |
| contact_name | 「联系人」类标签值，剥离括号备注与「电话/手机/联系方式」尾巴 |
| contact_phone | 「联系电话」类标签值中匹配 手机号（1[3-9] 开头 11 位）或 固话（区号-号码-分机）；单元格无则全文兜底 |
| lots | 见第 7 节 |
| region / stage / stage_key / pub_time | 取自接口上下文（交易中心地区、业务环节），不做正文抽取 |

---

## 10. 项目聚合键 build_project_id

同一项目的多阶段公告（招标计划 → 招标公告 → 澄清 → 控制价 → 中标公示 → 中标公告）需归到同一 project_id。键 = 归一化后的「项目名称 + 招标人 + 地区」拼接串的 SHA1 前 16 位：

- 归一化仅去除空白与括号、分隔标点（不影响文字本身）；
- 任一字段为空时按空串参与拼接，键仍稳定；
- 该键写入 notices.project_id，并由 store.rebuild_projects 聚出 projects 表（同一 project_id 取最长项目名、首个招标人、首个地区、全部环节去重序列、通知数、首末发布时间与最新环节）。

---

## 11. 字段清单与缺失口径

| 类别 | 字段 |
| --- | --- |
| 必填（REQUIRED_FIELDS） | project_name、tenderee、bid_method、open_time |
| 选填（OPTIONAL_FIELDS） | agency、budget、estimate、control_price、contract_price、bid_price、open_place、period、candidates、winner、members、contact_name、contact_phone、qualification、scale、lots |
| 上下文（不判缺失） | region、stage、stage_key、pub_time |

- 金额类（MONEY_FIELDS）：budget、estimate、control_price、contract_price、bid_price，值为 parse_money 的 dict。
- 缺失统计：`missing_required` / `missing_optional` 分别计数，报告里给出「必填缺失累计、分布」与「选填缺失累计」。

---

## 12. 入库与状态机（scripts/store.py）

| 表 | 主键 | 说明 |
| --- | --- | --- |
| notices | infoid | 公告主记录：标题、发布时间、地区、环节、project_id、正文长度、抽取状态、失败信息、重试次数 |
| notice_fields | (infoid, field) | 扁平化字段：label、value_text（人读）、value_num、value_unit、value_json（列表/金额原样）、value_type（text/money/date/list/number） |
| projects | project_id | 项目归并视图，由 notices 重算，支持多阶段聚合 |
| runs | run_id | 每次采集/正文抓取/抽取/推送的运行记录（day、kind、total、ok、failed、skipped、aborted、payload） |

状态机：`pending → extracted → pushed`；抽取异常时回写 `status=pending` 并 `inc_retry`，便于下次重试。

写入约定（P1 加固点）：

- **空值不落行**：serialize_field 对空串、空列表、空字典返回空串，notice_fields 不写 value_text 为空的噪音行；本次实跑 111 条公告的字段表中空值行为 0。
- **runs.ok 回退**：record_run 在 stats 未提供 ok 时回退取 extracted（抽取阶段用 extracted 计成功数），避免出现「total=111 却 ok=0」的假记录。
- **projects 幂等收尾**：rebuild_projects 在指定 day 时，会删除「最后一条发布时间落在该日、但已不在本次归并结果中」的陈旧 project 行，保证同日重跑结果收敛。

---

## 13. 质量保障

### 13.1 抽取报告

`python3 scripts/extract.py --date YYYY-MM-DD` 产出 `extract-report-<day>.json` 与 `.md`（`REPORT_DIR` 下），核心指标：

- total / with_detail / no_detail / body_empty；
- extracted / failed / skipped / attemptable / success_rate / abnormal_count；
- missing_required_total / missing_optional_total 与必填缺失分布；
- 每字段命中条数与覆盖率（field_hits）；
- projects_rebuilt / project_id_count。

### 13.2 回归测试（tests/test_rules.py，23 条，零依赖）

`python3 tests/test_rules.py` 直接运行（`unittest`，无需 pytest）。用例均对应当前清洗过程中修掉或防回归的真实误报：

| 用例组 | 覆盖点 |
| --- | --- |
| TestMoney | 元/万元/亿元/千元/汉字大写/人民币符号/费率保留百分比；条款编号 2.2.2 不得当金额；无单位数字须带 unit_inferred 标记 |
| TestDate | 中文日期、ISO 日期、空值 |
| TestOrg | 公司/政府/学校判真；「所有标段」整句判假；后缀截断与别名括号剥离 |
| TestLots | 说明性长句与条款词不进入 lots；真实标段名保留；_tidy_lot 去掉换行残号 |
| TestMembers | 顿号分隔拆解；「成员单位」前缀剥离；句子残片剔除 |
| TestQualification | 值从「具备」起头、不带「备注」尾巴 |

### 13.3 字段质量红线

- 宁缺勿错：机构、金额、日期三类字段一律「过校验才写，不过则留空」，不用模板文字填充。
- 可追溯：每条结果带 evidence（命中的标签或段落来源），便于人工复核。
- 不做推断合并：只记录原文出现的标段列表，不按标段拆行、不跨公告合并、不生成 Excel/CSV 导出（P1 约束）。

---

## 14. 已知限制与后续可做项

| 限制 | 说明 | 建议 |
| --- | --- | --- |
| 必填缺失集中在 bid_method / open_time | 部分公告（如流标公示、控制价公告）确实不含招标方式与开标时间，属正常缺失；tenderee 缺失 17 条多为纯公示类公告 | 报告中按环节分层看缺失，不必强行补齐 |
| 标段区间表达 | 「本项目划分 1 个标段」这类只给数量的表述不产出标段名 | 如需标段数量，可另设 lot_count 字段 |
| 机构名简称 | 正文只出现简称（如「中建八局」）时无法归一到全称 | 可维护一份别名映射表 |
| project_id 口径 | 项目名文本微调会导致 project_id 变化 | 若接入官方项目编号（如 investProjectCode）应优先采用 |

---

## 15. 变更记录（P1）

- 新增 fetch_detail / extractors（normalize、rules）/ extract / store，实现「正文抓取 → 规则抽取 → SQLite 入库」闭环。
- 采集链路加固：categorynum 服务端前缀过滤 + pn 按 offset 递增分页 + 与 totalcount 对账；同 Host 请求间隔 ≥3s、403/429 熔断 15 分钟、5xx 指数退避、404 不重试。
- 金额规则收紧：无单位纯数字需「整段基本为一个数字」，消除条款编号假金额；汉字大写金额不再二次乘单位。
- 机构判定收紧：短特征词须词尾命中，消除「所有标段」整句误判；联合体成员接入 valid_org_field 与前缀清洗。
- 标段抽取引入噪音词表与 _tidy_lot，去掉「招标项」等误命中与残号。
- 资质值截断后续字段尾巴并从「具备」起头。
- 入库空值行清理、runs.ok 回退、projects 幂等收尾三处加固。
- 新增 tests/test_rules.py 回归用例。

## 16. P1 实跑校准（2026-09-10 / 2026-09-11 批次）

对两日真实抓取结果抽样复核后，修正 4 处规则缺陷，并沉淀为回归用例（tests/test_rules.py
`TestP1Regressions`，累计 31 条用例全绿）：

| # | 缺陷 | 触发原文 | 修正 |
| --- | --- | --- | --- |
| 1 | 计算式残句被当成金额 | 「…发包人公布的最高投标限价的1.5%计算。」抽出 rate=1.5% | `MONEY_NOISE_TOKENS` 增加 计算/计取/折算/下浮，首字符连词（的按以和及对由）直接拒绝 |
| 2 | 有效控制价被误杀 | 「为（人民币）：陆佰叁拾贰万壹仟贰佰肆拾柒元贰角叁分（¥6321247.23）；」超 40 字上限 | 上限 40 → 60，并允许句末标点（`rstrip("；;。，,、")`）后再判定 |
| 3 | 项目名残留年份片段 | 标题「2026年星岛湖镇…」→「年星岛湖镇…」 | 新增 `strip_leading_numbering`：先剥「YYYY年」，再剥「（1）」式序号 |
| 4 | 资质字段吞后续内容 | 「…（包括资格要求和加分业绩、诚信综合评价分）」被当作字段标签，值含开标时间/地点 | 新增 `label_context_ok` 上下文判定（同行前置枚举符、括号未闭合不算字段行），`_ordered_label_matches` 可信位置优先、可疑位置兜底；值以并列连词开头则丢弃 |

同时补充：评标办法脚注（`[A1]` 式脚注标记）、废标/招标失败公告句式不再产出金额；`extract_scale`
在遇到「开标时间/开标地点/投标截止时间/预中标人/联系人/联系电话」等后续字段名时立即截断。

校准后两日实测变化（2026-09-10）：`control_price` 8 → 14（+6，找回被长度上限误杀的有效控制价）、
`budget` 5 → 6、`contract_price` 31 → 30 与 `bid_price` 11 → 10（各 −1，剔除评标办法残句假值）、
`qualification` 16 → 14（−2，剔除括号内枚举项残片）；`project_name/tenderee/agency/open_time` 等
主体字段命中不变，未出现误伤。
