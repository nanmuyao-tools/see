"""Bluesky 帖子搜索（官方公开 AppView，未登录）。

按短语搜最新的帖子。调研（2026-10-28）里「is there an app」约每周 14 条、抽样约 8/29 是需求，
浓度是几个源里最高的，量小。注意：
- 只有 api.bsky.app 未登录可用；public.api.bsky.app 返回 403，带 cursor 翻页也是 403，所以只取第一页；
- 帖子没有标题，用正文开头当标题；热度取点赞数；
- 链接里带作者的 handle（要有它才能打开原帖），但作者信息本身不入库。
文档：https://docs.bsky.app/docs/api/app-bsky-feed-search-posts
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime

from ..models import SourceItem
from . import cursor
from .http import clean, get_json

API = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
MAX_LIMIT = 100  # 接口单页上限


def parse(data: dict) -> list[SourceItem]:
    items: list[SourceItem] = []
    for p in data.get("posts", []):
        text = clean((p.get("record") or {}).get("text"))
        handle = (p.get("author") or {}).get("handle", "")
        uri = p.get("uri", "")
        if not (text and handle and uri):
            continue
        items.append(SourceItem(
            source="bluesky",
            external_id=uri,
            title=text[:100],
            text=text,
            url=f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}",
            score=p.get("likeCount") or 0,
            created_at=cursor.epoch((p.get("record") or {}).get("createdAt")),
        ))
    return items


def fetch(limit: int, conf: dict, cursors: dict[str, int] | None = None) -> list[SourceItem]:
    """每个查询抓最新 limit 条（上限 100）。有游标时用 since 只要游标之后发布的。"""
    items: list[SourceItem] = []
    for q in conf.get("queries", []):
        params = {"q": f'"{q}"', "sort": "latest", "limit": min(limit, MAX_LIMIT)}
        if cursors and cursors.get(q):
            params["since"] = datetime.fromtimestamp(cursors[q] + 1, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        got = parse(get_json(f"{API}?{urllib.parse.urlencode(params)}"))
        if cursors and cursors.get(q):  # since 按索引时间过滤，发布时间更早的补录帖再挡一次
            got = [it for it in got if it.created_at > cursors[q]]
        cursor.advance(cursors, q, got)
        items += got
    return items
