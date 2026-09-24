# -*- coding: utf-8 -*-
"""统一配置：代码内置安全默认值，.env 覆盖（标准库实现，无第三方依赖）。

设计原则（脱离 AI 自动运行）：
- 所有可环境相关的参数（路径 / 接口 / 调度口径）均可通过 .env 或环境变量覆盖，
  代码里不出现任何机器专属的绝对路径。
- 凭证类信息（如后续接入推送、同步的密钥）只允许放 .env，仓库内只提供 .env.example。
- 配置只在进程启动时读取一次，定时任务（cron / launchd）无需额外参数即可运行。
"""
import os
import re
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    REPO_ROOT = Path(sys.executable).resolve().parent
    if REPO_ROOT.name.lower() == "dist":
        REPO_ROOT = REPO_ROOT.parent
else:
    REPO_ROOT = Path(__file__).resolve().parent
ROOT_DIR = REPO_ROOT

# ------------------------------------------------------------------ 统一版本
APP_VERSION = "v0.3.2"

# ------------------------------------------------------------------ .env 解析
def load_env_file(path=None):
    """极简 .env 解析：KEY=VALUE，支持 # 注释、export 前缀、引号包裹。"""
    path = Path(path or os.environ.get("GXZB_ENV") or (REPO_ROOT / ".env"))
    if not path.exists():
        return {}
    data = {}
    content = ""
    try:
        raw_bytes = path.read_bytes()
    except Exception:
        return {}
    try:
        content = raw_bytes.decode("utf-8")
    except UnicodeError:
        try:
            content = raw_bytes.decode("gb18030")
        except UnicodeError:
            content = raw_bytes.decode("utf-8", errors="replace")
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        # 兼容处理历史 GBK 保存导致的中文乱码（若为 UTF-8 错解为 GBK，则自动修正）
        if any(ord(c) > 127 for c in val):
            try:
                val = val.encode("gbk").decode("utf-8")
            except Exception:
                pass
        data[key.strip()] = val
    return data


_ENV = load_env_file()


def get(key, default=""):
    """环境变量优先，其次 .env，最后默认值。"""
    val = os.environ.get(key)
    if val is not None and val != "":
        return val
    if key in _ENV:
        return _ENV[key]
    return default


def get_path(key, default):
    """路径型配置：相对路径一律相对仓库根目录解析，保证 cron / 双击运行结果一致。"""
    p = Path(os.path.expanduser(get(key, str(default))))
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p.resolve()


# ------------------------------------------------------------------ 站点与目录
SITE_DIR = get_path("SITE_DIR", REPO_ROOT / "dist")            # 页面产物根目录（同步到 VPS 的目录）
SITE_BASE_URL = get("SITE_BASE_URL", "https://ztb.139771.xyz").rstrip("/")
DATA_DIR = get_path("DATA_DIR", REPO_ROOT / "data")            # 运行数据（采集原始 / 每日入库 / 状态台账）
RAW_DIR = DATA_DIR / "raw"                                     # 采集原始响应（按日分目录）
COLLECT_DIR = DATA_DIR / "collect"                             # 当日全量采集（去重规范化）
DAILY_DIR = DATA_DIR / "daily"                                 # 当日入库条目（页面数据源）
STATE_DIR = DATA_DIR / "state"                                 # 全局 infoid 台账
LOG_DIR = get_path("LOG_DIR", SITE_DIR / "logs")               # 运行日志（与页面同域，便于核对）
REPORT_DIR = get_path("REPORT_DIR", SITE_DIR / "reports")      # 核验报告（缺失清单等）

TEMPLATE_PREVIEW = get_path("TEMPLATE_PREVIEW", REPO_ROOT / "templates" / "index-preview.html")
TEMPLATE_ARCHIVE_SAMPLE = get_path("TEMPLATE_ARCHIVE_SAMPLE", REPO_ROOT / "templates" / "index-sample.html")
# 视觉设计基准指纹：模板被改动时构建脚本会直接失败，避免样式漂移
PREVIEW_MD5 = get("TEMPLATE_PREVIEW_MD5", "4be81445d5a113d27d521e3cf59e9086")

# ------------------------------------------------------------------ 采集接口
API_URL = get("API_URL", "http://ggzy.jgswj.gxzf.gov.cn/inteligentsearchgxes/rest/esinteligentsearch/getFullTextDataNew")
API_REFERER = get("API_REFERER", "http://ggzy.jgswj.gxzf.gov.cn/nnggzy/jyxx/001001/tradeInfo.html")
API_TIMEOUT = int(get("API_TIMEOUT", "30"))
API_RETRY = int(get("API_RETRY", "2"))
API_PAGE_SIZE = int(get("API_PAGE_SIZE", "500"))
# 15 个交易中心（自治区本级 + 14 个地市）
CENTERS = [c.strip() for c in get("COLLECT_CENTERS", ",".join("%03d" % i for i in range(1, 16))).split(",") if c.strip()]
# 只收「工程建设」大类：001001 + 行业(3 位) + 业务环节(3 位)
CATEGORY_PREFIX = get("COLLECT_CATEGORY_PREFIX", "001001")

# 官方详情页地址模板：不信任数据源里的 url（历史数据存在被截断 / 旧路径失效问题）
DETAIL_URL_TPL = get("DETAIL_URL_TPL",
                     "http://ggzy.jgswj.gxzf.gov.cn/gxggzy/projectDetails.html?infoid={infoid}&categorynum={categorynum}")

TIMEZONE = get("TIMEZONE", "Asia/Shanghai")

# ------------------------------------------------------------------ 采集加固（P1）
# 反爬安全阈值：同一 Host 两次请求的最小间隔（秒），低于 3 秒会被源站限流
API_MIN_INTERVAL = float(get("API_MIN_INTERVAL", "3.0"))
# 403 / 429 触发熔断后的冷却时长（秒），默认 15 分钟
API_BREAKER_COOLDOWN = int(get("API_BREAKER_COOLDOWN", "900"))
# 5xx 重试的指数退避基数（秒）：第 n 次退避 = API_BACKOFF_BASE ** n
API_BACKOFF_BASE = float(get("API_BACKOFF_BASE", "2"))
# 单个中心的翻页上限保护（防接口异常导致死循环）
API_MAX_PAGES = int(get("API_MAX_PAGES", "200"))
# 服务端 categorynum 前缀过滤：站点前端口径为 isLike=true + likeType=2（前缀匹配）
CATEGORY_LIKE_TYPE = int(get("CATEGORY_LIKE_TYPE", "2"))
# 是否启用服务端发布时间窗（time 数组，字段 infodatepx），默认启用
API_DAY_WINDOW = get("API_DAY_WINDOW", "1").strip() not in ("0", "false", "False", "")
# 熔断状态文件（跨进程生效：冷却期内重跑直接拒绝，不打源站）
BREAKER_PATH = STATE_DIR / "breaker.json"

# ------------------------------------------------------------------ 监控调度窗口与定时刷新
MONITOR_START_TIME = get("MONITOR_START_TIME", "08:00").strip()
MONITOR_END_TIME = get("MONITOR_END_TIME", "20:00").strip()
MONITOR_INTERVAL_MINUTES = int(get("MONITOR_INTERVAL_MINUTES", "10"))
ENABLE_YESTERDAY_FINAL = get("ENABLE_YESTERDAY_FINAL", "1").strip() not in ("0", "false", "False", "")
YESTERDAY_FINAL_TIME = get("YESTERDAY_FINAL_TIME", "00:10").strip()


# ------------------------------------------------------------------ 正文抓取与规则抽取（P1）
# ------------------------------------------------------------------ 历史回扫与滞后公开/隐匿公告检测
# 回扫时间窗口（天）：默认回扫过去 30 天
BACKSCAN_DAYS = int(get("BACKSCAN_DAYS", "30"))
# 滞后公开判定阈值（天）：首次发现日期与官方标称发布日期差 >= 2 天即判定为滞后公开
BACKSCAN_MIN_DELAY = int(get("BACKSCAN_MIN_DELAY", "2"))
# 是否在每日流水线中启用历史回扫
BACKSCAN_ENABLED = get("BACKSCAN_ENABLED", "1").strip() not in ("0", "false", "False", "")
# 全局首次发现注册表（持久化存储 infoid 与首次发现时间、滞后判定元数据）
DELAYED_REGISTRY_PATH = STATE_DIR / "delayed_registry.json"


def get_delayed_today_path(day):
    return STATE_DIR / f"delayed_today_{day}.json"


DETAIL_DIR = DATA_DIR / "details"          # 公告正文缓存（按日/按 infoid）
EXTRACT_DIR = DATA_DIR / "extract"         # 规则抽取结果（按日）
DB_PATH = get_path("DB_PATH", DATA_DIR / "gxzb.sqlite3")   # SQLite 入库
# 详情页地址：优先用接口返回的 linkurl（静态页，含正文）；
# linkurl 缺失时回退到 DETAIL_URL_TPL（前端路由页，可能无正文）
DETAIL_URL_HOST = get("DETAIL_URL_HOST", "http://ggzy.jgswj.gxzf.gov.cn").rstrip("/")
# 详情页正文容器（站点前端固定 class，改动时只需改这里）
DETAIL_BODY_CLASS = get("DETAIL_BODY_CLASS", "ewb-details-info")
DETAIL_TITLE_CLASS = get("DETAIL_TITLE_CLASS", "ewb-details-title")

# ------------------------------------------------------------------ 业务字典
INDUSTRY_MAP = {
    "001": "房建市政工程",
    "002": "水利工程",
    "003": "交通工程",
    "004": "铁路工程",
    "005": "其他项目",
}
STAGE_MAP = {
    "001": "招标计划",
    "002": "招标公告",
    "003": "澄清/答疑",
    "004": "控制价公示",
    "005": "中标公示",
    "006": "中标公告",
}
STAGE_KEY_MAP = {
    "招标计划": "plan",
    "招标公告": "notice",
    "澄清/答疑": "clarify",
    "控制价公示": "control_price",
    "中标公示": "candidate",
    "中标公告": "result",
}
BADGE_CLASS_MAP = {
    "招标计划": "bg-blue-100 text-blue-800 border-blue-200",
    "招标公告": "bg-emerald-100 text-emerald-800 border-emerald-200",
    "澄清/答疑": "bg-amber-100 text-amber-800 border-amber-200",
    "控制价公示": "bg-purple-100 text-purple-800 border-purple-200",
    "中标公示": "bg-sky-100 text-sky-800 border-sky-200",
    "中标公告": "bg-green-100 text-green-800 border-green-200",
}
# 重点预警关键词：标题命中即标记，纯规则判断，不依赖人工
FOCUS_KEYWORDS = [k.strip() for k in get("FOCUS_KEYWORDS", "").replace("\n", ",").replace("，", ",").split(",") if k.strip()]
FOCUS_PROJECTS = [k.strip() for k in get("FOCUS_PROJECTS", "").replace("\n", ",").replace("，", ",").split(",") if k.strip()]
FOCUS_OWNERS = [k.strip() for k in get("FOCUS_OWNERS", "").replace("\n", ",").replace("，", ",").split(",") if k.strip()]
FOCUS_PROJECT_TYPES = [k.strip() for k in get("FOCUS_PROJECT_TYPES", "").replace("\n", ",").split(",") if k.strip()]
FOCUS_MIN_AMOUNT_RAW = get("FOCUS_MIN_AMOUNT", "").strip()

def parse_min_amount_val(val: str) -> float:
    """解析金额阈值字符串为元数值（float）。界面输入默认单位为万元。"""
    if not val:
        return 0.0
    s = str(val).replace(",", "").replace("，", "").strip()
    try:
        if "亿" in s:
            return float(s.replace("亿", "").replace("元", "").strip()) * 100000000.0
        elif "万" in s:
            return float(s.replace("万", "").replace("元", "").strip()) * 10000.0
        elif "元" in s:
            return float(s.replace("元", "").strip())
        num = float(s)
        # 纯数字录入时默认单位为万元（如 500 表示 500 万元）
        if num > 0 and num < 1000000:
            return num * 10000.0
        return num
    except Exception:
        return 0.0

FOCUS_MIN_AMOUNT = parse_min_amount_val(FOCUS_MIN_AMOUNT_RAW)

def extract_amount_from_title(title: str):
    """从标题中提取金额（元），提取不到则返回 None。"""
    if not title:
        return None
    import re
    from extractors.normalize import parse_money
    patterns = [r'[¥￥]\s*([0-9]+(?:\.[0-9]+)?)', r'([0-9]+(?:\.[0-9]+)?)\s*(?:亿|万|千|百)?元', r'([0-9]+(?:\.[0-9]+)?)\s*(?:亿|万)']
    for p in patterns:
        m = re.search(p, title)
        if m:
            res = parse_money(m.group(0))
            if res.get('value') is not None and res.get('kind') != 'rate':
                return float(res['value'])
    return None

# ------------------------------------------------------------------ 模糊匹配算法
ALIAS_PAIRS = [
    ("中医院", "中医医院"),
    ("住建局", "住房和城乡建设局"),
    ("交投", "交通投资"),
    ("北投", "北部湾投资"),
    ("水投", "水务投资"),
    ("水投", "水利电业"),
    ("妇幼", "妇幼保健院"),
    ("疾控中心", "疾病预防控制中心"),
    ("医科大", "医科大学"),
]

def normalize_match_text(text: str) -> str:
    """去标点、括号与多余空白，用于文本归一化比对。"""
    if not text:
        return ""
    text = str(text).lower()
    return re.sub(r"[\s\(\)（）\[\]【】\{\}\-—_·、，,。；;:：!！\?？\"\'“”]+", "", text)

def _generate_variants(pattern: str):
    """生成行业常见简称、同义词变体。"""
    variants = {pattern}
    for a, b in ALIAS_PAIRS:
        cur = list(variants)
        for v in cur:
            if a in v:
                variants.add(v.replace(a, b))
            if b in v:
                variants.add(v.replace(b, a))
    return list(variants)

def _match_single_variant(norm_p: str, norm_t: str) -> bool:
    if not norm_p or not norm_t:
        return False
    # 1. 归一化子串完全包含
    if norm_p in norm_t:
        return True

    # 2. 跨度容错：模式串字符保持原序出现在文本中，且两字之间跨度 <= 20（用于容纳"广西"、"一期"、标段等插入词）
    t_idx = 0
    for ch in norm_p:
        pos = norm_t.find(ch, t_idx)
        if pos == -1:
            return False
        gap = pos - t_idx
        if gap > 20:
            return False
        t_idx = pos + 1
    return True

def fuzzy_match(pattern: str, text: str) -> bool:
    """通用招投标模糊匹配：支持精确、多关键词AND(空格/+/星号)、OR(|)、简称归一化和中间修饰词跨度容错。"""
    if not pattern or not text:
        return False
    pattern = str(pattern).strip()
    text = str(text).strip()
    if not pattern or not text:
        return False

    if pattern in text:
        return True

    if "|" in pattern:
        return any(fuzzy_match(sub_p.strip(), text) for sub_p in pattern.split("|") if sub_p.strip())

    clean_parts = [p for p in re.split(r"[\s\+\*]+", pattern) if p]
    if len(clean_parts) > 1:
        return all(fuzzy_match(p, text) for p in clean_parts)

    norm_t = normalize_match_text(text)
    for v in _generate_variants(pattern):
        norm_v = normalize_match_text(v)
        if _match_single_variant(norm_v, norm_t):
            return True

    for suffix in ["建设项目", "建设工程", "实施方案", "工程", "项目"]:
        norm_p = normalize_match_text(pattern)
        if norm_p.endswith(suffix) and len(norm_p) - len(suffix) >= 4:
            stem = norm_p[:-len(suffix)]
            if _match_single_variant(stem, norm_t):
                return True

    return False

fuzzy_match_project = fuzzy_match

# ------------------------------------------------------------------ 运行开关
# 页面入库口径：
#   report  = 用当日已入库条目生成页面，缺失条目只出核验报告（默认，人工确认后再合并）
#   merge   = 自动把全量采集中缺失的条目并入入库条目后生成页面（全自动无人值守口径）
RECONCILE = get("RECONCILE", "report").strip().lower()
# 生成完成后需要执行的外部命令（例如推送脚本），留空则跳过；密钥请写在 .env
PUSH_CMD = get("PUSH_CMD", "").strip()
# 微信服务号模版消息推送配置（留空则不推送）
WECHAT_APPID = get("WECHAT_APPID", "").strip()
WECHAT_APPSECRET = get("WECHAT_APPSECRET", "").strip()
WECHAT_TOUSER = get("WECHAT_TOUSER", "").strip()  # 日常日报接收人：支持单个、逗号分隔多个，或填 @all 全体关注者
WECHAT_ADMIN_TOUSER = get("WECHAT_ADMIN_TOUSER", "").strip()  # 异常告警接收人：仅接收任务执行失败等报错告警，不干扰普通关注用户
WECHAT_TEMPLATE_ID = get("WECHAT_TEMPLATE_ID", "").strip()  # 日常日报模版
WECHAT_ALERT_TEMPLATE_ID = get("WECHAT_ALERT_TEMPLATE_ID", "").strip()  # 异常告警模版
# 微信推送触发条件配置
PUSH_TRIGGER_MODE = get("PUSH_TRIGGER_MODE", "any_complete").strip().lower()  # any_complete | focus_only | batch_time
PUSH_ALERT_FOCUS = get("PUSH_ALERT_FOCUS", "true").strip().lower() in ("true", "1", "yes", "on")
PUSH_MIN_COUNT = int(get("PUSH_MIN_COUNT", "1"))
PUSH_NOTIFY_ERROR = get("PUSH_NOTIFY_ERROR", "true").strip().lower() in ("true", "1", "yes", "on")
PUSH_BATCH_HOURS = get("PUSH_BATCH_HOURS", "08:00, 17:30").strip()
# 微信条件发送（增量检测推送模式）：仅当自上次发送以来存在新增标讯时才触发发送；若无新增则跳过
PUSH_CONDITIONAL_INCREMENTAL = get("PUSH_CONDITIONAL_INCREMENTAL", "true").strip().lower() in ("true", "1", "yes", "on")

# 触发推送多规则配置 (允许多条规则同时生效)
# 可选规则: 'focus'(重点标讯即时推送), 'batch_time'(定时批次归集), 'error'(系统异常告警)
PUSH_TRIGGER_RULES_RAW = get("PUSH_TRIGGER_RULES", "").strip()
if PUSH_TRIGGER_RULES_RAW:
    PUSH_TRIGGER_RULES = [r.strip() for r in PUSH_TRIGGER_RULES_RAW.split(",") if r.strip()]
else:
    # 兼容历史单选模式与开关
    _rules = []
    if PUSH_ALERT_FOCUS:
        _rules.append("focus")
    if PUSH_TRIGGER_MODE == "batch_time":
        _rules.append("batch_time")
    elif PUSH_TRIGGER_MODE == "focus_only":
        if "focus" not in _rules:
            _rules.append("focus")
    if PUSH_NOTIFY_ERROR:
        _rules.append("error")
    if not _rules:
        _rules = ["focus", "batch_time", "error"]
    PUSH_TRIGGER_RULES = _rules

AUTO_UPLOAD_VPS = get("AUTO_UPLOAD_VPS", "true").strip().lower() in ("true", "1", "yes", "on")
VPS_HOST = get("VPS_HOST", "217.142.149.2").strip() or "217.142.149.2"
VPS_PORT = get("VPS_PORT", "22").strip() or "22"
VPS_USER = get("VPS_USER", "root").strip() or "root"
VPS_PATH = get("VPS_PATH", "/opt/1panel/www/tender_site/").strip() or "/opt/1panel/www/tender_site/"
VPS_KEY_PATH = get("VPS_KEY_PATH", "").strip()
MANAGER_PORT = int(get("MANAGER_PORT", "8089"))
KEEP_DAYS = int(get("KEEP_DAYS", "0"))  # >0 时保留最近 N 天采集原始响应，0 表示全部保留


def ensure_dirs():
    for d in (SITE_DIR, DATA_DIR, RAW_DIR, COLLECT_DIR, DAILY_DIR, STATE_DIR, LOG_DIR, REPORT_DIR,
              DETAIL_DIR, EXTRACT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def today():
    """按 .env 指定时区取当天日期（默认 Asia/Shanghai，避免跨时区跑偏）。"""
    import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.datetime.now().strftime("%Y-%m-%d")


def now_stamp():
    import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
