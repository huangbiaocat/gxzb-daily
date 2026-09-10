# -*- coding: utf-8 -*-
"""统一配置：代码内置安全默认值，.env 覆盖（标准库实现，无第三方依赖）。

设计原则（脱离 AI 自动运行）：
- 所有可环境相关的参数（路径 / 接口 / 调度口径）均可通过 .env 或环境变量覆盖，
  代码里不出现任何机器专属的绝对路径。
- 凭证类信息（如后续接入推送、同步的密钥）只允许放 .env，仓库内只提供 .env.example。
- 配置只在进程启动时读取一次，定时任务（cron / launchd）无需额外参数即可运行。
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# ------------------------------------------------------------------ .env 解析
def load_env_file(path=None):
    """极简 .env 解析：KEY=VALUE，支持 # 注释、export 前缀、引号包裹。"""
    path = Path(path or os.environ.get("GXZB_ENV") or (REPO_ROOT / ".env"))
    if not path.exists():
        return {}
    data = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
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
        data[key.strip()] = val
    return data


_ENV = load_env_file()


def get(key, default=""):
    """环境变量优先，其次 .env，最后默认值。"""
    val = os.environ.get(key)
    if val:
        return val
    val = _ENV.get(key)
    return default if not val else val


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
FOCUS_KEYWORDS = [k.strip() for k in get("FOCUS_KEYWORDS", "公路,医院,学校,安置,水库,治理,灌区,道路").split(",") if k.strip()]

# ------------------------------------------------------------------ 运行开关
# 页面入库口径：
#   report  = 用当日已入库条目生成页面，缺失条目只出核验报告（默认，人工确认后再合并）
#   merge   = 自动把全量采集中缺失的条目并入入库条目后生成页面（全自动无人值守口径）
RECONCILE = get("RECONCILE", "report").strip().lower()
# 生成完成后需要执行的外部命令（例如推送脚本），留空则跳过；密钥请写在 .env
PUSH_CMD = get("PUSH_CMD", "").strip()
KEEP_DAYS = int(get("KEEP_DAYS", "0"))  # >0 时保留最近 N 天采集原始响应，0 表示全部保留


def ensure_dirs():
    for d in (SITE_DIR, DATA_DIR, RAW_DIR, COLLECT_DIR, DAILY_DIR, STATE_DIR, LOG_DIR, REPORT_DIR):
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
