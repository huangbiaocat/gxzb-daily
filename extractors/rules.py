# -*- coding: utf-8 -*-
"""抽取规则（正则/词表）与字段抽取器。

本模块只做「规则 + 纯函数」，不做 IO；正文来源见 scripts/fetch_detail.py，
入库见 scripts/store.py，规则说明见 docs/extraction-rules.md。

对外主要入口:
    extract_notice(text, ctx)   单条公告 → 结构化字段 + 命中详情 + 缺失清单
    extract_orgs(text)          文本 → 机构名列表（已规范化）
    build_project_id(...)       项目聚合键（用于 projects 表归集同一项目的多阶段公告）
"""
import hashlib
import re

try:
    from . import normalize
except ImportError:               # 直接以脚本方式运行（extractors 目录在 sys.path 上）
    import normalize

# ==================================================================== 一、机构名
# 法定后缀白名单：命中即在此处截断（取"最靠右的结尾"），保证不带出后续正文
ORG_SUFFIXES = [
    # —— 公司类（长后缀优先，避免把"集团有限公司"截成"有限公司"已足够，但保留以增强可读性）
    "集团有限责任公司", "集团股份有限公司", "集团股份有限公司分公司",
    "有限责任公司", "股份有限公司", "有限公司", "股份公司", "公司",
    # —— 设计 / 研究 / 勘察
    "勘察设计研究院有限公司", "勘察设计研究院", "设计研究院有限公司", "设计研究院",
    "设计研究总院", "研究院有限公司", "设计院", "研究院", "研究所", "科学院",
    # —— 监理 / 咨询 / 造价 / 管理
    "工程监理有限公司", "监理咨询有限公司", "监理有限公司", "工程咨询有限公司",
    "造价咨询有限公司", "咨询有限公司", "项目管理有限公司", "管理有限公司",
    "招标代理有限公司", "工程造价咨询有限公司", "招标咨询有限公司",
    "会计师事务所", "律师事务所", "税务师事务所", "事务所",
    # —— 施工 / 建设 / 工程
    "建筑工程有限公司", "建设工程有限公司", "工程建设有限公司", "建设有限公司",
    "建工集团有限公司", "建工集团", "建设集团有限公司", "建设集团",
    "工程有限公司", "建筑有限公司", "建设发展有限公司", "建设投资有限公司",
    "开发有限公司", "实业有限公司", "投资有限公司", "置业有限公司",
    # —— 企事业单位 / 政府机构
    "农村信用合作联社", "农村商业银行", "供电局", "供电公司", "水务集团",
    "集团有限公司", "有限公司分公司",
    "管理委员会", "管理服务中心", "管理中心", "交易中心", "采购中心", "服务中心",
    "保障中心", "检测中心", "造价中心", "中心", "管理处", "管理局", "事业局", "厅", "局",
    "大学", "学院", "学校", "小学", "中学", "医院", "卫生院", "银行",
    "委员会", "政府", "办公室",
]
# 机构特征词（用于 looks_like_org 粗判）
ORG_FEATURE_WORDS = ["公司", "集团", "院", "中心", "银行", "局", "厅", "厂", "社", "所", "站",
                     "会", "校", "大学", "学院", "医院", "政府", "办公室", "委员会", "管理处", "事务所"]
# 属性关键词：出现在机构名中间时，在其之前截断（防止"XX公司在南宁市"这类尾巴）
ORG_ATTR_KEYWORDS = ["地址", "所在地", "法定代表人", "统一社会信用代码", "注册地", "联系电话",
                     "邮政编码", "开户银行", "账号", "资质", "等级", "成立于"]
# 括号内为别名/简称语义词 → 整段括号删除
ORG_ALIAS_KEYWORDS = ["原", "原名", "曾用名", "前身", "更名", "简称", "以下简称", "下称", "又名", "亦名"]
# 括号内出现以下词则保留括号（可能是联合体成员等有效信息）
ORG_KEEP_PAREN_KEYWORDS = ["公司", "集团", "院", "中心", "银行", "局", "联合体", "事务所"]
# 标段/包组前缀（机构名前方出现时剔除）
SECTION_PREFIX_PATTERNS = [
    r"^\s*[（(]?\s*第?\s*[一二三四五六七八九十\d]{1,3}\s*(?:标段|标包|标|包|分标|合同包|标项)\s*[）)]?\s*[:：、]?\s*",
    r"^\s*[（(]?\s*[A-Za-z]\s*(?:标段|标包|包|分标)\s*[）)]?\s*[:：、]?\s*",
    r"^\s*[（(]\s*联合体\s*[）)]\s*[:：、]?\s*",
]
# 机构名前方噪音词（候选串首部出现则截掉）
ORG_START_JUNK = ["第一中标候选人", "第二中标候选人", "第三中标候选人", "中标候选人", "中标人",
                  "成交供应商", "成交人", "招标人", "招标代理机构", "采购人", "投标人",
                  "供应商", "评审委员会", "评标委员会", "公示", "公告", "名称", "单位", "结果"]
# 机构候选串中出现即否决的黑名单词
ORG_BLACKLIST_TOKENS = ["招标代理机构", "招标人名称", "项目名称", "工程名称", "采购人名称",
                        "投标人名称", "中标候选人公示", "评标委员会", "评审委员会",
                        "本项目", "工程概况", "附件", "说明", "详见", "无", "详见公告"]

_ORG_SUFFIX_ALT = "|".join(re.escape(s) for s in sorted(ORG_SUFFIXES, key=len, reverse=True))
# 机构名候选：2~40 个中英文/数字/· 字符 + 以白名单后缀结尾（不使用括号、空格，避免吞并后续正文）
ORG_NAME_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9·]{2,60}?(?:%s)" % _ORG_SUFFIX_ALT)

# 「裸后缀片段」：懒匹配在后缀白名单的靠前位置截断时留下的残片（如"…设计研究总院" + "有限公司"），
# 这类片段本身不构成机构名，需并入上一个候选，避免产出 "有限公司" 这种垃圾条目。
ORG_BARE_FRAGMENTS = {"公司", "有限公司", "股份有限公司", "有限责任公司", "集团有限公司",
                      "集团股份有限公司", "分公司", "有限公司分公司", "事务所", "中心", "院",
                      "研究院", "设计院", "设计研究院", "研究总院", "设计研究总院", "局", "厅",
                      "银行", "委员会", "办公室"}
# 机构类字段的「句子标记」：命中即判定不是机构名（如 "将根据有关规定…报行政主管部门备案"）
ORG_INVALID_TOKENS = ["根据", "按照", "的规定", "有关规定", "备案", "有关", "应当", "负责",
                      "详见", "如下", "本工程", "本招标", "本项目", "评价结果", "行政主管部门",
                      "见附件", "另行", "以上", "以下"]


def _merge_org_fragments(names):
    """把裸后缀片段并回前一个候选；前项已含该后缀（如"…有限公司"+"有限公司"）则直接丢弃，避免叠字。"""
    out = []
    for name in names:
        if name in ORG_BARE_FRAGMENTS:
            if out and not out[-1].endswith(name):
                out[-1] = out[-1] + name
            continue
        out.append(name)
    return out


def valid_org_field(text):
    """机构类字段值校验：过滤句子残片（含句子标记/句读/超长），避免把正文尾巴当机构名入库。

    认可两种情况：含机构特征词（公司/院/局/中心/大学…）或结尾命中后缀白名单（如 "广西大学"、"自治区水利厅"）。
    """
    t = normalize.clean_value(text or "", max_len=80)
    if len(t) < 4 or len(t) > 40:
        return False
    if re.search(r"[，,。;；！？!?]", t):
        return False
    if any(tok in t for tok in ORG_INVALID_TOKENS):
        return False
    if t.count(" ") >= 2:
        return False
    return bool(normalize.looks_like_org(t)) or any(t.endswith(s) for s in ORG_SUFFIXES)


def _trim_junk_start(name):
    """截掉候选串首部的噪音词（如'综合楼工程中标候选人广西XX公司'→'广西XX公司'）。"""
    best = 0
    for tok in ORG_START_JUNK:
        idx = name.rfind(tok)
        if idx >= 0:
            best = max(best, idx + len(tok))
    tail = name[best:]
    return tail if len(tail) >= 4 else name


def extract_orgs(text, max_items=15):
    """从任意文本中抽取机构名（已走 normalize.clean_org_name 规范化）。"""
    text = normalize.normalize_text(text)
    if not text:
        return []
    out = []
    for m in ORG_NAME_RE.finditer(text):
        cand = _trim_junk_start(m.group(0))
        name = normalize.clean_org_name(cand)
        if len(name) < 4 or len(name) > 50:
            continue
        if any(bad in name for bad in ORG_BLACKLIST_TOKENS):
            continue
        if not normalize.looks_like_org(name):
            continue
        if any(tok in name for tok in ORG_INVALID_TOKENS):
            continue
        out.append(name)
    return normalize.dedup_keep_order(_merge_org_fragments(out))[:max_items]


def split_org_cell(cell, max_items=20):
    """把"公司、公司" / "公司 公司" 这类联合体单元格拆成成员列表。"""
    cell = normalize.normalize_text(cell)
    if not cell:
        return []
    parts = re.split(r"[、,，;；/]|(?:与)|(?:和(?=[\u4e00-\u9fa5]{2,}公司))|\s{2,}|\n", cell)
    names = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        names += extract_orgs(part, max_items=5) or (
            [normalize.clean_org_name(part)] if normalize.looks_like_org(part) else [])
    return normalize.dedup_keep_order(_merge_org_fragments(names))[:max_items]


# ==================================================================== 二、字段标签
FIELD_LABELS = {
    "project_name": ["项目名称", "工程名称", "采购项目名称", "招标项目名称", "标段名称", "项目名"],
    "tenderee": ["招标人名称", "招标人（采购人）", "采购人名称", "招标人", "采购人", "建设单位",
                 "项目业主", "业主单位", "发包人", "招标单位", "采购单位", "采购机关", "招标方"],
    "agency": ["招标代理机构名称", "招标代理机构", "采购代理机构", "招标代理单位", "代理机构",
               "招标代理人", "代理单位", "受托单位", "采购代理"],
    "bid_method": ["招标方式", "采购方式", "招标组织形式", "交易方式", "招标类型", "招标办法"],
    "budget": ["预算金额", "采购预算", "项目预算", "预算总金额", "预算价", "预算"],
    "estimate": ["投资估算", "概算金额", "项目概算", "工程概算", "概算"],
    "control_price": ["招标控制价", "最高投标限价", "最高限价", "控制价", "限价"],
    "contract_price": ["合同估算价", "中标金额", "中标价", "成交金额", "成交价", "中标总价",
                       "中标价格", "合同金额", "合同价"],
    "bid_price": ["投标报价", "投标总价", "报价", "投标价格"],
    "open_time": ["开标时间", "开标（投标截止）时间", "投标截止时间", "递交投标文件截止时间",
                  "响应文件递交截止时间", "递交响应文件截止时间", "报价截止时间", "开启时间",
                  "谈判时间", "磋商时间", "投标文件递交截止时间", "递交截止时间", "截止时间",
                  "开标日期", "评审时间"],
    "open_place": ["开标地点", "开标地址", "投标地点", "递交地点", "开启地点", "谈判地点",
                   "磋商地点", "开标室", "开标场所", "评审地点"],
    "period": ["计划工期", "工期要求", "合同履行期限", "服务期限", "服务期", "交货期",
               "履约期限", "工期", "建设工期", "计划服务期", "供货期"],
    "contact_name": ["项目联系人", "联系人", "采购人联系人", "招标联系人", "联系人姓名"],
    "contact_phone": ["联系电话", "联系方式", "联系号码", "电话", "咨询电话", "联系人电话"],
    "qualification": ["资质要求", "资格要求", "投标人资格要求", "供应商资格要求", "资质条件",
                      "资格条件", "投标人资质", "资质等级", "特定资格要求"],
    "scale": ["建设规模", "建设内容及规模", "建设内容", "项目规模", "工程规模", "招标范围",
              "招标内容", "采购需求", "项目概况", "工程概况", "招标范围及内容", "建设内容与规模"],
    "lots": ["标段名称", "标段划分", "标段", "分标", "标包", "合同包", "标项", "包号"],
}

# 值截断用的"下一个字段标签"正则（避免把后续字段吞进当前值）
_ALL_LABELS = sorted({lab for labs in FIELD_LABELS.values() for lab in labs}, key=len, reverse=True)
NEXT_FIELD_SPLIT = r"\s*(?:%s)\s*[:：]" % "|".join(re.escape(l) for l in _ALL_LABELS)

# 招标方式取值枚举（按优先级）
METHOD_KEYWORDS = ["公开招标", "邀请招标", "竞争性磋商", "竞争性谈判", "单一来源采购", "单一来源",
                   "询价", "公开选取", "比选", "竞价", "直接发包", "框架协议", "定点采购"]

# 标段名：只认「标段/标包/分标/合同包/标项」类词，避免 "总承包" 里的 "包" 误命中；
# `(?<!招)标项` 挡住 "招标项目/招标项" 这类被 "标项" 结尾误命中
LOT_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9（）()·\-]{0,30}?(?:标段|标包|分标|合同包|(?<!招)标项)\s*[（(]?[一二三四五六七八九十\d]{0,3}[）)]?")
# 标段名噪音词：命中的是说明性长句/键值行（"各投标人可就…所有标段…"、"标段(包)编号:E45…"）而非标段名
LOT_NAME_NOISE = ("本项目", "本标段", "各标段", "多标段", "个标段", "标段划分", "标段数量", "招标", "投标", "中标",
                  "应当", "根据", "规定", "允许", "编号", "名称", "否则", "同一人", "拟任", "不得", "参与", "、" )

# 候选段落终止词：取到这些词之前为止
SEGMENT_STOPS = ["中标金额", "中标价", "投标报价", "报价", "公示期", "公示期限", "异议", "质疑",
                 "投诉", "联系方式", "联系电话", "联系人", "招标人", "采购人", "地址", "特此公示",
                 "项目负责人", "项目经理", "评标情况", "评审情况", "附件", "备注"]

DATE_LABELS_FALLBACK = ["公告发布时间", "发布时间", "发布日期", "公示开始时间", "公示期"]

REQUIRED_FIELDS = ["project_name", "tenderee", "bid_method", "open_time"]
OPTIONAL_FIELDS = ["agency", "budget", "estimate", "control_price", "contract_price", "bid_price",
                   "open_place", "period", "candidates", "winner", "members", "contact_name",
                   "contact_phone", "qualification", "scale", "lots"]
MONEY_FIELDS = ["budget", "estimate", "control_price", "contract_price", "bid_price"]
# 金额单元格里的模板/残句标记：命中即判定不是有效报价（如"【或每平方米 元;或投标费率 %;或其他报价方式: 】…"）
MONEY_NOISE_TOKENS = ["【", "】", "或", "；", ";", "每平方米", "元/", "详见", "根据", "按照",
                      "投标人须知", "填列", "格式",
                      # 计算式残句：如"…最高投标限价的1.5%计算。"（曾把 1.5% 误当成控制价）
                      "计算", "计取", "折算", "下浮",
                      # 非金额句式：评标办法脚注（Word 脚注标记 [A1]）、废标/失败公告
                      "[", "]", "否决投标", "招标失败", "评标委员会"]
# 金额单元格首字符若为这些连词/助词，说明截取的是句子尾巴而非金额
MONEY_LEADING_JUNK = "的按以和及对由"

# 标题中的阶段词后缀（剥离后得到项目名）
TITLE_STAGE_SUFFIX = re.compile(
    r"(?:"
    r"中标候选人公示|中标结果公示|中标候选人公告|中标结果公告|中标公示|中标公告|中标通知|"
    r"评标结果公示|评标结果公告|评标公示|定标公告|流标公示|流标公告|废标公告|废标公示|"
    r"终止公告|中止公告|变更公告|更正公告|澄清公告|答疑纪要|答疑澄清|补遗书|补遗公告|"
    r"延期公告|推迟公告|开标记录|开标公告|招标控制价公告|最高限价公告|最高投标限价公告|"
    r"招标公告|投标邀请书|资格预审公告|资格预审结果公告|资格预审|"
    r"竞争性磋商公告|竞争性谈判公告|询价公告|单一来源采购公告|采购公告|结果公告|成交公告|"
    r"合同公告|履约公告|验收公告|复议公告|信息公开|公示|公告|通知"
    r")\s*$")


# ==================================================================== 三、取值原语
def _line_tail(text, end):
    """取标签之后同一行的内容（去掉冒号等分隔符）；同行无内容时回退到随后的第一个非空行。"""
    tail = text[end:]
    head = tail.split("\n", 1)[0].lstrip(" \t：:-—=、")
    if head.strip():
        return head
    rest = tail.split("\n", 1)[1] if "\n" in tail else ""
    for line in rest.split("\n")[:3]:
        if line.strip():
            return line.strip()
    return ""


def find_value(text, labels, max_len=200, cleaner=None):
    """按标签匹配取值（长标签优先），返回 (命中标签, 值)；未命中返回 ("", "")。"""
    cleaner = cleaner or (lambda s: normalize.clean_value(s, max_len=max_len))
    for lab in sorted(labels or [], key=len, reverse=True):
        for m in re.finditer(re.escape(lab), text):
            raw = _line_tail(text, m.end())
            val = cleaner(raw)
            if val:
                return lab, val
    return "", ""


def find_money(text, labels):
    """按标签取金额字段，返回 (标签, parse_money 结果) 或 ("", None)。"""
    for lab in sorted(labels or [], key=len, reverse=True):
        for m in re.finditer(re.escape(lab), text):
            raw = _line_tail(text, m.end())
            if not raw or not _money_cell_ok(raw):
                continue
            parsed = normalize.parse_money(raw)
            if parsed["value"] is not None or parsed["kind"] in ("empty", "rate"):
                return lab, parsed
    return "", None


def _money_cell_ok(raw):
    """金额单元格可信度：拒绝"【或每平方米 元;或投标费率 %】"这类模板残句与超长尾巴。

    上限放宽到 60 字：大写金额 + 括号阿拉伯数字的完整表达（如
    "为（人民币）：陆佰叁拾贰万壹仟贰佰肆拾柒元贰角叁分（¥6321247.23）；"）约 40+ 字，
    旧上限 40 会把这类有效控制价误杀；模板残句仍由 MONEY_NOISE_TOKENS 拦截。
    """
    t = normalize.normalize_text(raw).strip()
    t = t.rstrip("；;。，,、")          # 句末标点不影响金额判定（"…（¥6321247.23）；"仍有效）
    if not (1 <= len(t) <= 60):
        return False
    if t[0] in MONEY_LEADING_JUNK:
        return False
    if any(tok in t for tok in MONEY_NOISE_TOKENS):
        return False
    return True


def find_date(text, labels):
    """按标签取日期字段，返回 (标签, 'YYYY-MM-DD HH:MM') 或 ("", "")。"""
    for lab in sorted(labels or [], key=len, reverse=True):
        for m in re.finditer(re.escape(lab), text):
            raw = _line_tail(text, m.end())
            val = normalize.parse_date(raw)
            if val:
                return lab, val
    return "", ""


def segments_after(text, labels, max_len=300, stops=None, limit=6):
    """取标签之后的一段文本（用于候选名单、联合体成员等列表型字段）。"""
    stops = stops or SEGMENT_STOPS
    out = []
    for lab in sorted(labels or [], key=len, reverse=True):
        for m in re.finditer(re.escape(lab), text):
            seg = text[m.end():m.end() + max_len]
            cut = len(seg)
            for stop in stops:
                idx = seg.find(stop)
                if 0 < idx < cut:
                    cut = idx
            seg = seg[:cut]
            if seg.strip(" \t：:、,，;；\n"):
                out.append(seg)
            if len(out) >= limit:
                return out
    return out


# ==================================================================== 四、字段抽取
# 项目名前导"年份 / 序号"噪声：如"2026年星岛湖镇…"（年份需整体剥离，否则残留"年"字）
LEADING_YEAR_RE = re.compile(r"^\s*[（(]?\s*(?:19|20)\d{2}\s*年\s*[）)]?\s*")
LEADING_NUM_RE = re.compile(r"^[（(]?[一二三四五六七八九十\d]+[）)]?\s*")


def strip_leading_numbering(name):
    """剥离项目名开头的年份/序号（"2026年xx项目"→"xx项目"、"（1）xx"→"xx"）。"""
    name = LEADING_YEAR_RE.sub("", name or "")
    return LEADING_NUM_RE.sub("", name)


def extract_project_name(text, title):
    """项目名：正文「项目名称/工程名称」优先，否则用标题剥离阶段词。"""
    _, val = find_value(text, FIELD_LABELS["project_name"], max_len=80)
    name = val or ""
    if len(name) < 4:
        name = strip_title_stage(title)
    else:
        name = strip_title_stage(name)
    name = strip_leading_numbering(name)
    return name.strip(" \t：:、,，。;；-—")


def strip_title_stage(title):
    """标题 → 项目名（剥离阶段词后缀与尾部括号/文号）。"""
    t = normalize.normalize_text(title)
    for _ in range(3):
        new = TITLE_STAGE_SUFFIX.sub("", t).strip()
        new = re.sub(r"[（(【\[][^（()）【\[\]】]{0,30}[）)】\]]\s*$", "", new).strip()
        new = new.strip(" \t：:、,，。;；-—_")
        if new == t:
            break
        t = new
    return t.strip(" \t：:、,，。;；-—_")


def extract_method(text):
    """招标方式：先看标签值，再全局扫枚举词。"""
    _, val = find_value(text, FIELD_LABELS["bid_method"], max_len=40)
    for kw in METHOD_KEYWORDS:
        if val and kw in val:
            return kw
    for kw in METHOD_KEYWORDS:
        if kw in text:
            return kw
    return ""


def extract_candidates(text):
    """中标候选人列表：取「中标候选人」段落中的机构名。"""
    labels = ["中标候选人名单", "推荐的中标候选人", "中标候选人排序", "中标候选人", "成交候选人",
              "推荐成交供应商", "候选供应商", "定标候选人"]
    names = []
    for seg in segments_after(text, labels):
        names += extract_orgs(seg, max_items=10)
    return normalize.dedup_keep_order(names)[:10]


def extract_winner(text, candidates=None):
    """中标人/成交供应商：标签值优先，取不到则回退候选名单首位。"""
    labels = ["中标人名称", "中标单位名称", "成交供应商名称", "中标人", "中标单位", "成交供应商",
              "成交人", "中标供应商", "中标方"]
    for lab in labels:
        for m in re.finditer(re.escape(lab), text):
            raw = _line_tail(text, m.end())
            names = split_org_cell(raw, max_items=3) or extract_orgs(raw, max_items=3)
            if not names:
                continue
            name = names[0]
            if valid_org_field(name):
                return name
    fallback = (candidates or [""])[0]
    return fallback if valid_org_field(fallback) else ""


def extract_members(text, title=""):
    """联合体成员：从「联合体」相关段落与标题括号中拆解（在"第二中标候选人"处截断，避免误收）。"""
    labels = ["联合体成员名单", "联合体成员", "联合体各方", "联合体牵头人", "联合体", "成员单位"]
    stops = ["第二中标", "第三中标", "第2中标", "第3中标", "；", ";", "中标候选人", "中标人", "公示期"]
    names = []
    for seg in segments_after(text, labels, max_len=200, stops=stops):
        names += split_org_cell(seg)
    for m in re.finditer(r"联合体\s*[:：]\s*([^）)】；;\n]{2,120})", title + "\n" + text):
        names += split_org_cell(m.group(1))
    names = [re.sub(r"^(?:联合体)?(?:成员单位|成员|牵头人|主办方|各方)\s*[:：]?\s*", "", n).strip() for n in names]
    return normalize.dedup_keep_order([n for n in names if len(n) >= 4 and valid_org_field(n)])[:10]


def _tidy_lot(name):
    """标段名清洗：去空白/换行，去掉紧跟在"标段"后的残号（"…标段\\n4" → "…标段"）。"""
    name = re.sub(r"\s+", "", name or "")
    name = re.sub(r"(标段|标包|分标|合同包|标项)\d{1,2}$", r"\1", name)
    return name


def extract_lots(text, title=""):
    """标段列表：标题括号内 / 「标段名称」段落中的标段名（不做拆行，仅记录列表）。"""
    lots = []
    for src in (title, text):
        if not src:
            continue
        for m in LOT_RE.finditer(src):
            name = _tidy_lot(m.group(0).strip(" ：:、,，;；()（）"))
            if len(name) >= 3 and not any(x in name for x in LOT_NAME_NOISE):
                lots.append(name)
    for seg in segments_after(text, ["标段名称", "标段划分"], max_len=200):
        for p in re.split(r"[、,，;；\n\t]+", seg):
            p = _tidy_lot(p.strip(" ：:、,，;；。()（）"))
            if not (3 <= len(p) <= 40):
                continue
            # 段落里的"标段"多出现在说明性长句（"各投标人可就…所有标段…"）中，必须同时命中标段类词
            if not re.search(r"(?:标段|标包|分标|合同包|(?<!招)标项)", p):
                continue
            if any(x in p for x in LOT_NAME_NOISE):
                continue
            lots.append(p)
    return normalize.dedup_keep_order(lots)[:20]


def label_context_ok(text, start):
    """标签是否处于"独立字段行"上下文。

    公告正文常把多个词并列写在括号里（如"…综合评价分（包括资格要求和加分业绩、诚信综合评价分）"），
    此时标签是枚举项而非字段名，直接取其后文会得到半句残片。判定规则：
      1) 同一行标签前若以枚举符/连词结尾（、，和及括含等）→ 视为枚举项；
      2) 标签所在行若括号未闭合 → 视为括号内文字。
    """
    head = text[:start].rsplit("\n", 1)[-1].rstrip()
    if head.endswith(("、", "，", ",", "；", ";", "和", "及", "括", "含", "（", "(", "【", "[")):
        return False
    if head.count("（") + head.count("(") > head.count("）") + head.count(")"):
        return False
    return True


def _ordered_label_matches(text, labels):
    """按标签长度优先枚举所有出现位置，产出 (标签, match)；先给上下文可信的，再兜底全部。"""
    trusted, fallback = [], []
    for lab in sorted(labels, key=len, reverse=True):
        for m in re.finditer(re.escape(lab), text):
            (trusted if label_context_ok(text, m.start()) else fallback).append((lab, m))
    return trusted + fallback


def extract_qualification(text):
    """资质要求：可能跨行，取标签后最多 3 行 / 200 字。"""
    for lab, m in _ordered_label_matches(text, FIELD_LABELS["qualification"]):
        tail = text[m.end():]
        buf = []
        for line in tail.split("\n")[:4]:
            line = normalize.clean_value(line, max_len=120)
            if not line:
                continue
            buf.append(line)
            if len(" ".join(buf)) >= 120:
                break
        val = " ".join(buf).strip()
        if val:
            # 砍掉"【备注…】/中标价…/项目负责人…"等后续字段尾巴，并尽量从"具备…资质"处起头
            val = re.split(r"【|备注|中标价|中标金额|项目负责人|投标人须知|注[:：]", val)[0].strip()
            m2 = re.search(r"(?:具备|具有|须具有|应当具有).{2,80}", val)
            if m2 and m2.start() > 0:
                val = val[m2.start():]
            if val[:1] in ("和", "及", "、", "，", ",", "）", ")", "】", "的"):
                continue        # 截到的是并列句尾巴，不是资质要求字段
            return val[:200]
    return ""


# 建设内容/规模字段的终止标签：正文里紧随其后的其它字段名，避免把"开标时间/地点"吞进建设内容
SCALE_STOP_LABELS = ["开标时间", "开标地点", "投标截止时间", "公示开始时间", "公示截止时间",
                     "预中标人", "中标候选人", "项目负责人", "联系人", "联系电话"]


def extract_scale(text):
    """建设内容/规模：标签后最多 3 行，超长截断；遇到后续字段名即截断。"""
    for lab, m in _ordered_label_matches(text, FIELD_LABELS["scale"]):
        tail = text[m.end():]
        buf = []
        for line in tail.split("\n")[:5]:
            line = normalize.clean_value(line, max_len=160)
            if not line:
                continue
            buf.append(line)
            if len(" ".join(buf)) >= 180:
                break
        val = " ".join(buf).strip()
        cut = len(val)
        for stop in SCALE_STOP_LABELS:
            idx = val.find(stop)
            if 0 < idx < cut:
                cut = idx
        val = val[:cut].strip(" \t：:、,，。;；-—")
        if len(val) >= 4:
            return val[:300]
    return ""


PHONE_RE = re.compile(r"(?:1[3-9]\d{9})|(?:0\d{2,3}[-\s]?\d{7,8}(?:[-\s]?\d{1,5})?)")


def extract_contacts(text):
    """联系人 / 联系电话。"""
    _, name = find_value(text, FIELD_LABELS["contact_name"], max_len=30)
    name = re.sub(r"[（(].*?[)）]|(?:电话|手机|联系方式).*$", "", name).strip(" ：:、,，")
    _, phone_cell = find_value(text, FIELD_LABELS["contact_phone"], max_len=60)
    phones = PHONE_RE.findall(phone_cell or "")
    if not phones:
        phones = PHONE_RE.findall(text)
    phone = phones[0] if phones else ""
    return name[:20], phone[:20]


def parse_duration(text):
    """工期 → {"raw", "days"}（月按 30 天、年按 365 天折算，仅供排序参考）。

    取不到数字时（如仅剩"(日历天)"这类表头残句）raw 置空，视为缺失，避免把模板文字当工期入库。
    """
    raw = normalize.clean_value(text or "", max_len=60)
    if not raw:
        return {"raw": "", "days": None}
    segs = [s.strip() for s in re.split(r"[。;；,，]", raw) if re.search(r"\d", s)]
    raw = (segs[0] if segs else "")[:25]
    m = re.search(r"(\d+(?:\.\d+)?)\s*(日历天|个工作日|工作日|个月|月|年|天|日)", raw)
    if not m:
        return {"raw": raw if re.search(r"\d", raw) else "", "days": None}
    num = float(m.group(1))
    unit = m.group(2)
    factor = {"日历天": 1, "天": 1, "日": 1, "个工作日": 1, "工作日": 1, "个月": 30, "月": 30, "年": 365}[unit]
    return {"raw": raw, "days": int(round(num * factor))}


def build_project_id(project_name, tenderee, region=""):
    """项目聚合键：项目名 + 招标人 + 地区 的稳定哈希（同一项目多阶段公告归到同一 project_id）。"""
    key = "|".join([
        re.sub(r"[\s（）()【】\[\]、,，。.·\-—_]", "", normalize.normalize_text(project_name or "")),
        re.sub(r"[\s（）()【】\[\]、,，。.·\-—_]", "", normalize.normalize_text(tenderee or "")),
        re.sub(r"\s", "", normalize.normalize_text(region or "")),
    ])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# ==================================================================== 五、主抽取入口
def extract_notice(text, ctx=None):
    """单条公告文本 → 结构化结果。

    返回:
        {
          "fields": {字段名: 值},           # 金额字段为 dict（见 normalize.parse_money）
          "evidence": {字段名: 命中标签},   # 依据（可追溯到正文标签）
          "missing_required": [字段名],
          "missing_optional": [字段名],
          "project_id": str,
          "orgs": [机构名],                # 正文中出现的全部机构（辅助核对）
        }
    """
    ctx = ctx or {}
    text = normalize.normalize_text(text or "")
    title = normalize.normalize_text(ctx.get("title") or "")
    haystack = (title + "\n" + text) if title else text
    fields, evidence = {}, {}

    pn_label, pn_val = find_value(text, FIELD_LABELS["project_name"], max_len=80)
    project_name = strip_title_stage(pn_val) if len(pn_val or "") >= 4 else strip_title_stage(title)
    project_name = strip_leading_numbering(project_name)
    project_name = project_name.strip(" \t：:、,，。;；-—")
    fields["project_name"] = project_name
    evidence["project_name"] = pn_label or "标题阶段词剥离"

    label, val = find_value(text, FIELD_LABELS["tenderee"], max_len=80)
    tenderee = normalize.clean_org_name(val) if val else ""
    if not valid_org_field(tenderee):
        segs = segments_after(text, ["招标人", "采购人", "建设单位", "项目业主", "业主单位"])
        names = [n for n in (extract_orgs(segs[0]) if segs else []) if valid_org_field(n)]
        if names:
            tenderee, label = names[0], "招标人段落"
        else:
            tenderee, label = "", ""
    fields["tenderee"], evidence["tenderee"] = tenderee, label

    label, val = find_value(text, FIELD_LABELS["agency"], max_len=80)
    agency = normalize.clean_org_name(val) if val else ""
    if not valid_org_field(agency):
        agency, label = "", ""
    fields["agency"], evidence["agency"] = agency, label

    fields["bid_method"] = extract_method(text)
    evidence["bid_method"] = "方式枚举词" if fields["bid_method"] else ""

    for field in MONEY_FIELDS:
        label, parsed = find_money(text, FIELD_LABELS[field])
        fields[field] = parsed
        evidence[field] = label

    label, val = find_date(text, FIELD_LABELS["open_time"])
    if not val:
        label, val = find_date(text, DATE_LABELS_FALLBACK)
    fields["open_time"], evidence["open_time"] = val, label

    label, val = find_value(text, FIELD_LABELS["open_place"], max_len=80)
    fields["open_place"], evidence["open_place"] = val, label

    label, val = find_value(text, FIELD_LABELS["period"], max_len=60)
    fields["period"] = parse_duration(val)
    evidence["period"] = label

    candidates = extract_candidates(text)
    fields["candidates"] = candidates
    evidence["candidates"] = "中标候选人段落" if candidates else ""

    winner = extract_winner(text, candidates)
    fields["winner"] = winner
    evidence["winner"] = ("" if not winner else
                          ("候选名单首位(回退)" if winner in candidates else "中标人标签"))

    members = extract_members(text, title)
    fields["members"] = members
    evidence["members"] = "联合体段落/标题" if members else ""

    name, phone = extract_contacts(text)
    fields["contact_name"], fields["contact_phone"] = name, phone
    evidence["contact_name"] = "联系人标签" if name else ""
    evidence["contact_phone"] = "电话标签/正则可识别" if phone else ""

    fields["qualification"] = extract_qualification(text)
    evidence["qualification"] = "资质要求段落" if fields["qualification"] else ""
    fields["scale"] = extract_scale(text)
    evidence["scale"] = "建设内容/规模段落" if fields["scale"] else ""
    fields["lots"] = extract_lots(text, title)
    evidence["lots"] = "标段名称/标题" if fields["lots"] else ""

    fields["region"] = normalize.normalize_text(ctx.get("areaname") or "")
    fields["stage"] = ctx.get("stage") or ""
    fields["stage_key"] = ctx.get("stage_key") or ""
    fields["pub_time"] = normalize.parse_date(ctx.get("pub_time") or "") or (ctx.get("pub_time") or "")

    missing_required = [f for f in REQUIRED_FIELDS if not _has_value(fields.get(f))]
    missing_optional = [f for f in OPTIONAL_FIELDS if not _has_value(fields.get(f))]
    return {
        "fields": fields,
        "evidence": evidence,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "project_id": build_project_id(project_name, tenderee, fields["region"]),
        "orgs": extract_orgs(text, max_items=12),
        "text_len": len(text),
    }


def _has_value(value):
    if value is None:
        return False
    if isinstance(value, dict):
        return value.get("value") is not None or bool(value.get("raw"))
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return bool(str(value).strip())
