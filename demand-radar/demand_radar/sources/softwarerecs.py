"""StackExchange 数据源（默认软件推荐站 softwarerecs）。

官方 API，无需注册：不带 key 每天 300 次请求，设置 SE_API_KEY 可提高到每天 1 万次。
文档：https://api.stackexchange.com/docs/questions
"""

from __future__ import annotations

import os
import urllib.parse

from ..models import SourceItem
from .http import clean, get_json

API = "https://api.stackexchange.com/2.3/questions"


def parse(site: str, data: dict) -> list[SourceItem]:
    return [
        SourceItem(
            source=f"stackexchange/{site}",
            external_id=str(q["question_id"]),
            title=clean(q.get("title"), 300),
            text=clean(q.get("body")),
            url=q.get("link", ""),
            score=q.get("score", 0),
        )
        for q in data.get("items", [])
    ]


def fetch(limit: int, conf: dict) -> list[SourceItem]:
    items: list[SourceItem] = []
    for site in conf.get("sites", ["softwarerecs"]):
        params = {
            "site": site, "sort": "creation", "order": "desc",
            "pagesize": min(limit, 100), "filter": "withbody",
        }
        if key := os.getenv("SE_API_KEY"):
            params["key"] = key
        items += parse(site, get_json(f"{API}?{urllib.parse.urlencode(params)}"))
    return items
