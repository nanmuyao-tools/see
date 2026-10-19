"""Hacker News 全文搜索（Algolia 官方 API，公开、无需 key）。

按短语精确搜索帖子和评论，比抓 Ask HN 首页的信噪比高得多。
文档：https://hn.algolia.com/api
"""

from __future__ import annotations

import urllib.parse

from ..models import SourceItem
from . import cursor
from .http import clean, get_json

API = "https://hn.algolia.com/api/v1/search_by_date"


def parse(data: dict) -> list[SourceItem]:
    items: list[SourceItem] = []
    for h in data.get("hits", []):
        if h.get("comment_text"):  # 评论：标题用所在帖子的标题
            title = clean(h.get("story_title"), 300)
            text = clean(h["comment_text"])
            kind = "comment"
        else:
            title = clean(h.get("title"), 300)
            text = clean(h.get("story_text"))
            kind = "post"
        items.append(SourceItem(
            source="hackernews",
            external_id=str(h["objectID"]),
            title=title,
            text=text,
            url=f"https://news.ycombinator.com/item?id={h['objectID']}",
            score=h.get("points") or 0,
            kind=kind,
            created_at=h.get("created_at_i") or 0,
        ))
    return items


def _params(query: str, pagesize: int) -> dict:
    return {"query": f'"{query}"', "tags": "(story,comment)", "hitsPerPage": pagesize}


def fetch(limit: int, conf: dict, cursors: dict[str, int] | None = None) -> list[SourceItem]:
    """每个查询抓最新 limit 条。有游标时用 numericFilters 只要游标之后发布的。"""
    items: list[SourceItem] = []
    for q in conf.get("queries", []):
        params = _params(q, limit)
        if cursors and cursors.get(q):
            params["numericFilters"] = f"created_at_i>{cursors[q]}"
        got = parse(get_json(f"{API}?{urllib.parse.urlencode(params)}"))
        cursor.advance(cursors, q, got)
        items += got
    return items
