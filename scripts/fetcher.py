# -*- coding: utf-8 -*-
"""统一取数层（P1 采集加固）：同 Host 限速 + 熔断 + 退避重试。

所有对源站的请求（列表接口 collect.py、详情页 fetch_detail.py）都必须经过本模块，
保证限流与熔断口径只有一处实现、不因新增脚本而失效。

策略（与 docs/pipeline.md §3、docs/extraction-rules.md §7 一致）：
- **限速**：同一 Host 两次请求之间强制间隔 ≥ `API_MIN_INTERVAL`（默认 3 秒）；
- **熔断**：遇 403 / 429 立即熔断 `API_BREAKER_COOLDOWN`（默认 900 秒 = 15 分钟），
  熔断状态落盘到 `<DATA_DIR>/state/breaker.json`，冷却期内重跑直接拒绝、不触达源站；
- **退避**：5xx 与网络类错误按 `API_BACKOFF_BASE ** n` 指数退避重试 `API_RETRY` 次；
- **不重试**：404 / 410（资源确实不存在）与其他非 5xx 的 4xx，直接抛出、不浪费请求。

无第三方依赖（仅标准库），错误统一抛 `HttpError` / `CircuitOpen`，便于调用方精确处理。
"""
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
import config  # noqa: E402

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; gxzb-daily/1.0; +https://github.com/gxzb-daily)",
    "Accept": "*/*",
}
# 熔断状态在本进程内的缓存，避免每次请求都读盘
_BREAKER_STATE = None


class CircuitOpen(RuntimeError):
    """熔断中：冷却期未过，本次任务不应继续请求该 Host。"""

    def __init__(self, message, until=None, host=""):
        super().__init__(message)
        self.until = until
        self.host = host


class HttpError(RuntimeError):
    """HTTP 层错误：带状态码与 URL，便于调用方判定是否需要重试。"""

    def __init__(self, status, url, reason=""):
        super().__init__("HTTP %s %s%s" % (status, url, (" | " + reason) if reason else ""))
        self.status = int(status)
        self.url = url
        self.reason = reason


class RateLimiter:
    """同 Host 最小请求间隔限制器（进程内生效）。"""

    def __init__(self, min_interval=None):
        self.min_interval = float(config.API_MIN_INTERVAL if min_interval is None else min_interval)
        self._last = {}

    def wait(self, url):
        host = urllib.parse.urlsplit(url).netloc.lower()
        last = self._last.get(host)
        if last is not None:
            gap = self.min_interval - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last[host] = time.monotonic()


class CircuitBreaker:
    """403 / 429 熔断器，状态落盘，跨进程生效。"""

    def __init__(self, path=None, cooldown=None):
        self.path = Path(path or config.BREAKER_PATH)
        self.cooldown = int(config.API_BREAKER_COOLDOWN if cooldown is None else cooldown)

    # ---------------------------------------------------------------- 状态
    def _load(self):
        global _BREAKER_STATE
        if _BREAKER_STATE is None:
            try:
                _BREAKER_STATE = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                _BREAKER_STATE = {}
        return _BREAKER_STATE

    def _save(self, state):
        global _BREAKER_STATE
        _BREAKER_STATE = state
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ---------------------------------------------------------------- 操作
    def remaining(self, host=None):
        """返回剩余冷却秒数（0 表示未熔断）。"""
        state = self._load()
        until = float(state.get("until") or 0)
        if host and state.get("host") and state["host"] != host:
            return 0
        return max(0.0, until - time.time())

    def check(self, url=""):
        host = urllib.parse.urlsplit(url).netloc.lower()
        left = self.remaining(host)
        if left > 0:
            state = self._load()
            raise CircuitOpen(
                "熔断中：%s 因 %s 被熔断，剩余 %d 秒（%.1f 分钟）不可请求，稍后再试"
                % (host, state.get("reason") or "403/429", int(left), left / 60.0),
                until=state.get("until"), host=host)

    def trip(self, url, status, reason=""):
        host = urllib.parse.urlsplit(url).netloc.lower()
        until = time.time() + self.cooldown
        self._save({"host": host, "status": int(status), "reason": reason or ("HTTP %s" % status),
                    "tripped_at": int(time.time()), "until": until,
                    "url": url, "cooldown": self.cooldown})
        return until

    def reset(self):
        self._save({})


_LIMITER = None
_BREAKER = None


def get_limiter():
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = RateLimiter()
    return _LIMITER


def get_breaker():
    global _BREAKER
    if _BREAKER is None:
        _BREAKER = CircuitBreaker()
    return _BREAKER


def _content_type(headers):
    try:
        return (headers.get("Content-Type") or "").lower()
    except AttributeError:
        return ""


def request(url, data=None, headers=None, timeout=None, retries=None, json_body=False,
            limiter=None, breaker=None, charset=None):
    """带限速 / 熔断 / 退避的 GET(或 POST) 请求，返回响应原始 bytes。

    参数:
        data      : POST 请求体（bytes / str / dict，dict 时自动 JSON 序列化）
        json_body : data 为 dict 时是否按 JSON 提交（默认 False 表示 urlencode）
        retries   : 覆盖 config.API_RETRY
    """
    limiter = limiter or get_limiter()
    breaker = breaker or get_breaker()
    retries = config.API_RETRY if retries is None else int(retries)
    timeout = config.API_TIMEOUT if timeout is None else timeout
    hdrs = dict(DEFAULT_HEADERS)
    hdrs.update(headers or {})

    body = None
    if data is not None:
        if isinstance(data, dict):
            if json_body:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json;charset=UTF-8")
            else:
                body = urllib.parse.urlencode(data).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data

    last_err = None
    for attempt in range(retries + 1):
        breaker.check(url)
        limiter.wait(url)
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            reason = _http_error_snippet(exc)
            # 403 / 429：被限流或封禁，立即熔断且不重试
            if status in (403, 429):
                breaker.trip(url, status, "HTTP %s %s" % (status, reason))
                raise HttpError(status, url, "已触发熔断（冷却 %d 秒）：%s" % (breaker.cooldown, reason))
            # 5xx：指数退避重试
            if 500 <= status < 600 and attempt < retries:
                time.sleep(_retry_after(exc) or (config.API_BACKOFF_BASE ** (attempt + 1)))
                last_err = HttpError(status, url, reason)
                continue
            # 404 / 410 及其他 4xx：不重试
            raise HttpError(status, url, reason)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(config.API_BACKOFF_BASE ** (attempt + 1))
                continue
            raise HttpError(0, url, "%s: %s" % (type(exc).__name__, exc))
    raise HttpError(getattr(last_err, "status", 0), url, str(last_err or "请求失败"))


def _http_error_snippet(exc, limit=200):
    try:
        raw = exc.read()
        text = raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""
    text = " ".join(text.split())
    return text[:limit]


def _retry_after(exc):
    """尊重服务端 Retry-After（秒数或 HTTP 日期），上限 60 秒。"""
    try:
        val = exc.headers.get("Retry-After")
    except AttributeError:
        return None
    if not val:
        return None
    try:
        return min(60.0, float(val))
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(val)
        return max(0.0, min(60.0, dt.timestamp() - time.time()))
    except Exception:  # noqa: BLE001
        return None


def _decode(raw, charset=None, headers=None):
    if charset:
        return raw.decode(charset, "replace")
    ctype = _content_type(headers)
    if "charset=" in ctype:
        return raw.decode(ctype.split("charset=")[-1].split(";")[0].strip(), "replace")
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def get_text(url, charset=None, **kwargs):
    """GET 并解码为文本；详情页（HTML）默认 utf-8，失败回退 gb18030。"""
    raw = request(url, **kwargs)
    return _decode(raw, charset=charset)


def post_json(url, payload, referer=None, **kwargs):
    """POST JSON 并解析响应（官方列表接口口径）。"""
    headers = kwargs.pop("headers", None) or {}
    if referer:
        headers.setdefault("Referer", referer)
    headers.setdefault("Content-Type", "application/json;charset=UTF-8")
    raw = request(url, data=payload, headers=headers, json_body=True, **kwargs)
    return json.loads(_decode(raw, charset="utf-8"))


if __name__ == "__main__":  # 自检：打印限速/熔断配置与当前熔断状态
    b = get_breaker()
    print(json.dumps({
        "同一 Host 最小间隔(秒)": config.API_MIN_INTERVAL,
        "熔断冷却(秒)": config.API_BREAKER_COOLDOWN,
        "退避基数": config.API_BACKOFF_BASE,
        "重试次数": config.API_RETRY,
        "熔断状态文件": str(config.BREAKER_PATH),
        "当前熔断剩余(秒)": int(b.remaining()),
    }, ensure_ascii=False, indent=1))
