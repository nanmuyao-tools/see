"""Hacker News 数据源。

用官方 Firebase API（完全公开、无需 key）：
  - Ask HN 列表: https://hacker-news.firebaseio.com/v0/askstories.json
  - 条目详情:    https://hacker-news.firebaseio.com/v0/item/{id}.json

Ask HN 帖子本身常是「求一个能做 X 的工具」，信噪比高，适合 v0。
"""

from __future__ import annotations

import json
import re
import urllib.request
from html import unescape

from ..models import SourceItem

API = "https://hacker-news.firebaseio.com/v0"
_TAG = re.compile(r"<[^>]+>")


def _get(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "demand-radar/0.0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _clean(html: str) -> str:
    """HN 的 text 是 HTML 片段，去标签、还原实体。"""
    return unescape(_TAG.sub("", html or "")).strip()


def fetch(limit: int) -> list[SourceItem]:
    ids = _get(f"{API}/askstories.json") or []
    items: list[SourceItem] = []
    for hn_id in ids[:limit]:
        item = _get(f"{API}/item/{hn_id}.json")
        if not item or item.get("dead") or item.get("deleted"):
            continue
        title = item.get("title", "")
        text = _clean(item.get("text", ""))
        items.append(
            SourceItem(
                source="hackernews",
                external_id=str(hn_id),
                title=title,
                text=text,
                url=f"https://news.ycombinator.com/item?id={hn_id}",
            )
        )
    return items
