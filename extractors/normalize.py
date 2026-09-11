# -*- coding: utf-8 -*-
"""字段标准化（纯函数，无 IO、无第三方依赖）。

对外能力:
    normalize_text(text)                全角→半角、统一空白、去零宽/BOM
    cn_number_to_int(text)              汉字数字 → int（十五 / 一百二十三 / 一亿两千万）
    parse_money(text)                   金额 → 元（万/亿/汉字数字统一换算；费率型保留百分比）
    format_money(value)                 元 → 人类可读（元 / 万元 / 亿元）
    parse_percent(text)                 比率文本 → 百分比数值（5% / 下浮 5 个点）
    parse_date(text)                    日期时间 → 'YYYY-MM-DD HH:MM'
    clean_org_name(text)                机构名规范化（后缀白名单截断 + 属性词截断 + 标段前缀剔除 + 别名括号剥离）
    strip_org_prefix / strip_alias_paren / truncate_at_suffix
    clean_value(text)                   通用字段值清洗（去标签残留、截断、去尾部标点）
    dedup_keep_order(items)             去重且保持原顺序

设计约定:
- 一切"拿不准"都返回 None 或 unit_kind='unknown'，绝不猜数；调用方据此计入 missing。
- 费率型金额（下浮率/费率/折扣率）不换算成元，value 即百分比数值（5 表示 5%）。
"""
import re
import unicodedata

# ------------------------------------------------------------------ 基础清洗
ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)


def normalize_text(text):
    """全角转半角 + 统一换行/空白 + 去零宽字符。"""
    if text is None:
        return ""
    text = str(text).translate(ZERO_WIDTH)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0\u3000]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def dedup_keep_order(items):
    seen, out = set(), []
    for it in items:
        key = it if isinstance(it, str) else repr(it)
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out


# ------------------------------------------------------------------ 汉字数字
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "贰": 2, "两": 2, "三": 3, "叁": 3,
             "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "七": 7, "柒": 7,
             "八": 8, "捌": 8, "九": 9, "玖": 9}
CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
CN_SECTIONS = {"万": 10 ** 4, "萬": 10 ** 4, "亿": 10 ** 8, "億": 10 ** 8}
_CN_NOISE = "人民币元整正约计共"


def cn_number_to_int(text):
    """汉字数字 → int；无法解析返回 None。支持 十五 / 一百二十三 / 一亿两千万 / 壹佰伍拾万。"""
    if not text:
        return None
    text = re.sub(r"[\s,，]", "", str(text))
    text = "".join(ch for ch in text if ch not in _CN_NOISE)
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return int(text)
    if not all(ch in CN_DIGITS or ch in CN_UNITS or ch in CN_SECTIONS for ch in text):
        return None
    total = section = number = 0
    for ch in text:
        if ch in CN_DIGITS:
            number = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            if number == 0:
                number = 1
            section += number * CN_UNITS[ch]
            number = 0
        else:
            section = (section + number) or 1
            total += section * CN_SECTIONS[ch]
            section = number = 0
    return total + section + number


def _first_number(text):
    """取文本中第一个阿拉伯数字（含小数、千分位）；没有返回 None。"""
    m = re.search(r"\d+(?:\.\d+)?", text.replace(",", "").replace("，", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


# ------------------------------------------------------------------ 金额
RATE_KEYWORDS = ("下浮率", "下浮", "费率", "折扣率", "折扣", "优惠率", "让利率", "浮动率")
_EMPTY_MONEY = {"", "/", "-", "--", "—", "无", "不报价", "未报价", "无报价", "空", "略"}


def _money(value, kind, raw, inferred=False):
    return {"value": value, "kind": kind, "raw": str(raw or "").strip(), "unit_inferred": inferred}


def parse_money(text, default_unit="yuan"):
    """金额标准化 → 元。

    返回 dict: {"value": float|None, "kind": yuan|wan|yi|rate|empty|unknown, "raw": str,
                "unit_inferred": bool}
    - 万元/亿元/千元 → 统一换算为元；汉字数字同样支持（如"壹佰万元"）；
    - 费率型（含 % 或 下浮率/费率/折扣率 等）**不换算**，value 为百分比数值（5.0 表示 5%）；
    - 无单位纯数字按 default_unit（招投标公告表格多为元）处理并标记 unit_inferred=True。
    """
    raw = text
    if text is None:
        return _money(None, "empty", raw)
    t = normalize_text(text).replace(",", "").replace("，", "")
    if not t or t.strip() in _EMPTY_MONEY:
        return _money(None, "empty", raw)

    is_rate = any(k in t for k in RATE_KEYWORDS) or ("%" in t) or ("％" in t)

    # 大写汉字金额（"贰佰叁拾柒万玖仟陆佰壹拾捌元整"）：万/亿已计入数值，不能再乘单位
    cn_m = re.search(r"[零〇一壹二贰两三叁四肆五伍六陆七柒八捌九玖十拾百佰千仟万亿]{2,}(?:元|圆)", t)
    cn_val = cn_number_to_int(cn_m.group(0)) if cn_m else None

    if is_rate:
        num = _first_number(t)
        if num is None and cn_val is not None:
            num = float(cn_val)
        return _money(num, "rate", raw)

    # 人民币符号后的阿拉伯金额：精确到元，优先级最高（"…（¥2379618.00元）"）
    ascii_m = re.search(r"[¥￥]\s*([0-9]+(?:\.[0-9]+)?)", t)
    if ascii_m:
        val = float(ascii_m.group(1))
        return _money(val, _magnitude_kind(val), raw)

    if cn_val is not None:
        return _money(float(cn_val), _magnitude_kind(cn_val), raw)

    num = _first_number(t)
    if num is None:
        # 无"元/万/亿"后缀的汉字数字：仅在足够长时采信，避免把"一""二"这类孤字当金额
        if len(t) >= 3:
            cn2 = cn_number_to_int(t)
            if cn2 is not None:
                return _money(float(cn2), _magnitude_kind(cn2), raw)
        return _money(None, "unknown", raw)

    if "亿" in t:
        return _money(num * 10 ** 8, "yi", raw)
    if "万" in t:
        return _money(num * 10 ** 4, "wan", raw)
    if "千元" in t:
        return _money(num * 10 ** 3, "qian", raw)
    if "元" in t:
        return _money(num, "yuan", raw)
    # 无单位的纯数字：只有"整段基本就是一个数字"时才按 default_unit 采信（避免把 "2.2.2" 这类条款编号当金额）
    if re.fullmatch(r"[^0-9]{0,8}\d+(?:\.\d+)?[^0-9]{0,4}", t) and len(t) <= 25:
        return _money(num, default_unit, raw, inferred=True)
    return _money(None, "unknown", raw)


def _magnitude_kind(value):
    """按数量级给出 kind：亿 / 万 / 元。"""
    v = abs(float(value))
    if v >= 10 ** 8:
        return "yi"
    if v >= 10 ** 4:
        return "wan"
    return "yuan"


def format_money(value):
    """元 → 人类可读字符串（亿元 / 万元 / 元，2 位小数、去尾零）。"""
    if value is None:
        return ""
    v = float(value)
    if abs(v) >= 10 ** 8:
        return ("%.2f亿元" % (v / 10 ** 8)).replace(".00亿", "亿")
    if abs(v) >= 10 ** 4:
        return ("%.2f万元" % (v / 10 ** 4)).replace(".00万", "万")
    return ("%.2f元" % v).replace(".00元", "元")


def parse_percent(text):
    """比率文本 → 百分比数值（'5%' → 5.0；'下浮 5 个点' → 5.0）。无则 None。"""
    t = normalize_text(text)
    if not t:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[%％]", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(?:下浮|费率|折扣率|优惠率|浮动率)\D{0,6}(\d+(?:\.\d+)?)\s*(?:个点|%)?", t)
    if m:
        return float(m.group(1))
    return None


# ------------------------------------------------------------------ 日期
_DATE_RE = re.compile(
    r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?"
    r"(?:[T\s]*(\d{1,2})\s*[:：时点]\s*(\d{1,2})?)?")
_TIME_ONLY_RE = re.compile(r"(\d{1,2})\s*[:：时点]\s*(\d{2})")


def parse_date(text):
    """日期时间 → 'YYYY-MM-DD HH:MM'；只有日期补 00:00；无法解析返回 None。"""
    t = normalize_text(text)
    if not t:
        return None
    m = _DATE_RE.search(t)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    hour, minute = 0, 0
    if m.group(4) is not None:
        hour = int(m.group(4))
        minute = int(m.group(5)) if m.group(5) else 0
        if hour > 23:
            hour, minute = 0, 0
        if minute > 59:
            minute = 0
    return "%04d-%02d-%02d %02d:%02d" % (year, month, day, hour, minute)


# ------------------------------------------------------------------ 机构名
def truncate_at_suffix(text, suffixes=None):
    """在法定后缀白名单的**最后一次**出现处截断，返回到后缀结尾的机构名。"""
    if not text:
        return ""
    if suffixes is None:
        suffixes = _rules().ORG_SUFFIXES
    best_end = -1
    for suf in suffixes:
        idx = text.rfind(suf)
        if idx >= 0 and idx + len(suf) > best_end:
            best_end = idx + len(suf)
    return text[:best_end] if best_end > 0 else text


def strip_org_prefix(text, patterns=None):
    """剔除标段/包组前缀（如 '一标段'、'2分标'、'A包'），只保留机构主体。"""
    if not text:
        return ""
    if patterns is None:
        patterns = _rules().SECTION_PREFIX_PATTERNS
    out = text.strip("：: -—、,，.")
    for pat in patterns:
        out = re.sub(pat, "", out)
    return out.strip("：: -—、,，.")


def strip_alias_paren(text, keep_keywords=None):
    """剥离机构名中的别名/简称括号（如 '广西某某建设集团有限公司（原某某公司）'）。

    括号内若是「曾用名/原/简称/以下简称/前身/更名」等别名语义 → 整段括号删除；
    若括号内本身含 '公司/集团/院/中心/银行/局' 等机构特征词（可能存在联合体成员）则保留。
    """
    if not text:
        return ""
    if keep_keywords is None:
        keep_keywords = _rules().ORG_KEEP_PAREN_KEYWORDS
    alias_words = _rules().ORG_ALIAS_KEYWORDS

    def repl(m):
        inner = m.group(1)
        if any(k in inner for k in alias_words):
            return ""
        if any(k in inner for k in keep_keywords):
            return m.group(0)
        return m.group(0) if len(inner) <= 12 else ""

    return re.sub(r"[（(]([^（）()]*)[）)]", repl, text).strip()


def clean_org_name(text, suffixes=None):
    """机构名规范化 = 去空白/脚注 + 截断到法定后缀 + 剥离别名括号 + 剔除标段前缀。

    例:
        '（原：广西某某公司）广西建工集团第一建筑工程有限责任公司（以下简称甲方）'
        → '广西建工集团第一建筑工程有限责任公司'
        '一标段：广西某某工程有限公司' → '广西某某工程有限公司'
    """
    t = normalize_text(text)
    if not t:
        return ""
    t = re.sub(r"[（(]\s*(?:以下简称|简称|下称)[^）)]*[）)]$", "", t)     # 尾部"（以下简称甲方）"
    t = re.sub(r"^\s*(?:中标人|中标单位|中标候选人|第一中标候选人|成交供应商|供应商|投标人|招标人)\s*[:：]\s*", "", t)
    t = re.sub(r"^(?:为|系|是|由)\s*", "", t)          # "招标人为XX公司" 取值时残留的系词
    t = strip_alias_paren(t)
    t = strip_org_prefix(t)
    t = t.strip("：: -—、,，.;；。")
    t = re.sub(r"^(?:名称|单位)\s*[:：]\s*", "", t)
    t = truncate_at_suffix(t, suffixes=suffixes)
    for kw in _rules().ORG_ATTR_KEYWORDS:                                # 属性词断言截断
        idx = t.find(kw)
        if idx >= 1:
            t = t[:idx]
    return t.strip("：: -—、,，.;；。")


def looks_like_org(text, min_len=4):
    """粗判是否像机构名：含机构特征词且长度合理。

    短特征词（所/站/会/社/厂/校/院/局/厅）必须落在词尾才算数，否则"所有标段进行投标"里的"所"会误判为机构名。
    """
    if not text:
        return False
    t = normalize_text(text)
    if len(t) < min_len or len(t) > 60:
        return False
    for k in _rules().ORG_FEATURE_WORDS:
        if k not in t:
            continue
        if len(k) >= 3 or t.endswith(k):
            return True
    return False


def clean_value(text, max_len=200):
    """通用字段值清洗：去冒号分隔符、压空白、截断到下一个字段标签、去尾部标点。"""
    if text is None:
        return ""
    t = normalize_text(text)
    t = re.sub(r"^\s*[:：\-—=]\s*", "", t)
    t = re.split(r"\s{2,}", t)[0]
    t = re.split(_rules().NEXT_FIELD_SPLIT, t)[0]
    t = t.strip(" \t.。,，;；、:：")
    return t[:max_len]


_rules_cache = None


def _rules():
    """延迟导入规则模块（避免 extractors 包与脚本直跑两种导入路径下的循环依赖）。"""
    global _rules_cache
    if _rules_cache is None:
        try:
            from . import rules as _r
        except ImportError:      # 直接以脚本方式运行、extractors 目录在 sys.path 上
            import rules as _r
        _rules_cache = _r
    return _rules_cache
