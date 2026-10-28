"""各数据源共用的 HTTP 请求与文本清洗。"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__

# 带上项目地址：站长看到流量时能找到我们，而不是只能封 IP
USER_AGENT = f"Mozilla/5.0 (compatible; demand-radar/{__version__}; +https://github.com/nanmuyao-tools/see)"
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
MAX_TEXT = 1500  # 送给 LLM 的正文上限，避免长帖拖慢推理
RETRIES = 2
MAX_WAIT = 30  # 单次重试最多等待秒数


# ---- 限速：所有源的请求都经过 get()，在这里按域名统一控制，不靠各源自己记得 sleep ----
#
# 同一个域名的两次请求之间至少隔 interval 秒；被限流（429 / 5xx）后，按服务器给的 Retry-After
# 把这个域名后面的所有请求一起往后推，而不只是重试那一次。不同域名互不影响。
# 间隔可在 sources.toml 的 [http] 里覆盖：min_interval（默认值）和 [http.hosts]（按域名）。

DEFAULT_INTERVAL = 1.0
# 已知站点的默认间隔（秒），按各自公开的限额留余量
HOST_INTERVALS = {
    "api.stackexchange.com": 1.0,           # 官方建议每秒不超过 30 次，另有每天配额
    "hn.algolia.com": 1.0,                   # 每小时 1 万次
    "arctic-shift.photon-reddit.com": 1.0,   # 免费服务，限额随负载浮动
    "www.reddit.com": 3.0,                   # 不带 key 访问很容易 429
    # 两个应用商店都不是公开 API（条款见 docs/research/2026-10-20-app-store-reviews.md），
    # 调研时 3.5–10 秒一次没被限流过，这里取 5 秒；每个 app 每轮还有页数上限（见各自的源）
    "itunes.apple.com": 5.0,
    "play.google.com": 5.0,
}


def _github_interval() -> float:
    # 搜索接口：不带 token 每分钟 10 次，带 token 30 次
    return 2.5 if os.getenv("GITHUB_TOKEN") else 6.5


class RateLimiter:
    """按域名的最小间隔 + 限流冷却。clock / sleep 可注入，测试不用真等。"""

    def __init__(self, clock=None, sleep=None):
        # 调用时才取 time.monotonic / time.sleep，测试里 mock 掉 time.sleep 就不会真等
        self._clock = clock or (lambda: time.monotonic())
        self._sleep = sleep or (lambda s: time.sleep(s))
        self._next: dict[str, float] = {}  # 域名 → 下一次最早可以发请求的时刻
        self._lock = threading.Lock()
        self.default = DEFAULT_INTERVAL
        self.overrides: dict[str, float] = {}

    def interval(self, host: str) -> float:
        if host in self.overrides:
            return self.overrides[host]
        if host == "api.github.com":
            return _github_interval()
        return HOST_INTERVALS.get(host, self.default)

    def wait(self, host: str) -> float:
        """轮到这个域名才返回；返回实际等了多少秒。先在锁里占好时间片，再在锁外睡，并发时也不会挤在一起。"""
        with self._lock:
            now = self._clock()
            slot = max(now, self._next.get(host, 0.0))
            self._next[host] = slot + self.interval(host)
        if slot > now:
            self._sleep(slot - now)
        return max(slot - now, 0.0)

    def cooldown(self, host: str, seconds: float) -> None:
        """被限流了：这个域名在 seconds 秒内不再发请求。"""
        with self._lock:
            self._next[host] = max(self._next.get(host, 0.0), self._clock() + seconds)


limiter = RateLimiter()


def configure(conf: dict | None) -> None:
    """读 sources.toml 的 [http]：min_interval 改默认间隔，[http.hosts] 按域名覆盖。"""
    conf = conf or {}
    limiter.default = float(conf.get("min_interval", DEFAULT_INTERVAL))
    limiter.overrides = {h: float(v) for h, v in (conf.get("hosts") or {}).items()}


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


class QuotaExhausted(Exception):
    """数据源的调用配额快用完了。回填时应就此停下，把剩余配额留给每天的增量抓取。"""


def _wait_seconds(e: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = e.headers.get("Retry-After") if e.headers else None
    if retry_after and retry_after.isdigit():
        return min(int(retry_after), MAX_WAIT)
    return min(5 * (attempt + 1), MAX_WAIT)


def get(url: str, headers: dict[str, str] | None = None) -> bytes:
    """GET 请求。每次发出前先过限速；遇到 429 限流或 5xx 时，按 Retry-After 让整个域名冷却后重试。"""
    return _send(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})}))


def post(url: str, data: bytes, headers: dict[str, str] | None = None) -> bytes:
    """POST 请求，限速、冷却、重试和 get 完全相同（Google Play 的评论接口只接受 POST）。"""
    return _send(urllib.request.Request(url, data=data, method="POST",
                                        headers={"User-Agent": USER_AGENT, **(headers or {})}))


def _send(req: urllib.request.Request) -> bytes:
    host = _host(req.full_url)
    for attempt in range(RETRIES + 1):
        limiter.wait(host)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if attempt == RETRIES or not (e.code == 429 or e.code >= 500):
                if e.code == 429:  # 不再重试也要冷却，免得下一个查询接着撞上去
                    limiter.cooldown(host, _wait_seconds(e, attempt))
                raise
            limiter.cooldown(host, _wait_seconds(e, attempt))
    raise AssertionError("unreachable")


def get_json(url: str, headers: dict[str, str] | None = None):
    return json.loads(get(url, headers))


def clean(text: str | None, limit: int = MAX_TEXT) -> str:
    """去 HTML 标签、还原实体、压缩空白，并截断到 limit 字符。"""
    s = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()
    return s[:limit]
