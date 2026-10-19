"""StackExchange 数据源（默认软件推荐站 softwarerecs）。

官方 API，无需注册：不带 key 每天 300 次请求，设置 SE_API_KEY 可提高到每天 1 万次。
文档：https://api.stackexchange.com/docs/questions
"""

from __future__ import annotations

import os
import time
import urllib.parse

from ..models import SourceItem
from . import cursor
from .http import QuotaExhausted, clean, get_json

API = "https://api.stackexchange.com/2.3/questions"
QUOTA_RESERVE = 10  # 回填时给每天的增量抓取留的请求数
MAX_BACKOFF = 60    # API 要求的冷却秒数上限


def parse(site: str, data: dict) -> list[SourceItem]:
    return [
        SourceItem(
            source=f"stackexchange/{site}",
            external_id=str(q["question_id"]),
            title=clean(q.get("title"), 300),
            text=clean(q.get("body")),
            url=q.get("link", ""),
            score=q.get("score", 0),
            created_at=q.get("creation_date") or 0,
        )
        for q in data.get("items", [])
    ]


def _params(site: str, pagesize: int) -> dict:
    params = {
        "site": site, "sort": "creation", "order": "desc",
        "pagesize": min(pagesize, 100), "filter": "withbody",
    }
    if key := os.getenv("SE_API_KEY"):
        params["key"] = key
    return params


def fetch(limit: int, conf: dict, cursors: dict[str, int] | None = None) -> list[SourceItem]:
    """每个站抓最新 limit 条。有游标时只要游标之后发布的（fromdate 含端点，所以 +1）。"""
    items: list[SourceItem] = []
    for site in conf.get("sites", ["softwarerecs"]):
        params = _params(site, limit)
        if cursors and cursors.get(site):
            params["fromdate"] = cursors[site] + 1
        got = parse(site, get_json(f"{API}?{urllib.parse.urlencode(params)}"))
        cursor.advance(cursors, site, got)
        items += got
    return items


# ---- 历史回填 ----

def units(conf: dict) -> list[str]:
    return conf.get("sites", ["softwarerecs"])


def interval() -> float:
    return 1.0  # 翻页间隔；响应里另有 backoff 时按它再等


def page_before(conf: dict, site: str, before: int | None, pagesize: int) -> list[SourceItem]:
    """回填用：取 before 之前发布的最新 pagesize 条（todate 含端点，所以 -1）。

    按时间往回切而不是翻页码：新帖不断进来会让页码整体后移，按时间切不漏也不重。
    响应里的 backoff 是 API 要求的冷却秒数，必须遵守。配额快用完就停：这一页的内容
    不处理也无妨，进度没推进，下次从同一位置重拉。
    """
    params = _params(site, pagesize)
    if before:
        params["todate"] = before - 1
    data = get_json(f"{API}?{urllib.parse.urlencode(params)}")
    if data.get("backoff"):
        time.sleep(min(int(data["backoff"]), MAX_BACKOFF))
    if data.get("quota_remaining", QUOTA_RESERVE + 1) <= QUOTA_RESERVE:
        raise QuotaExhausted(f"StackExchange 今日配额仅剩 {data['quota_remaining']} 次，停止回填，明天再继续")
    return parse(site, data)
