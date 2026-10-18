"""各数据源共用的 HTTP 请求与文本清洗。"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.request

USER_AGENT = "Mozilla/5.0 (compatible; demand-radar/0.1)"
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
MAX_TEXT = 1500  # 送给 LLM 的正文上限，避免长帖拖慢推理
RETRIES = 2
MAX_WAIT = 30  # 单次重试最多等待秒数


def _wait_seconds(e: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = e.headers.get("Retry-After") if e.headers else None
    if retry_after and retry_after.isdigit():
        return min(int(retry_after), MAX_WAIT)
    return min(5 * (attempt + 1), MAX_WAIT)


def get(url: str, headers: dict[str, str] | None = None) -> bytes:
    """GET 请求；遇到 429 限流或 5xx 时按 Retry-After 等待后重试。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if attempt == RETRIES or not (e.code == 429 or e.code >= 500):
                raise
            time.sleep(_wait_seconds(e, attempt))
    raise AssertionError("unreachable")


def get_json(url: str, headers: dict[str, str] | None = None):
    return json.loads(get(url, headers))


def clean(text: str | None, limit: int = MAX_TEXT) -> str:
    """去 HTML 标签、还原实体、压缩空白，并截断到 limit 字符。"""
    s = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()
    return s[:limit]
