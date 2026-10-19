"""GitHub issues 数据源：点赞多的 feature request。

用官方搜索 API：不带 token 每分钟 10 次，设置 GITHUB_TOKEN 可提高到每分钟 30 次。
文档：https://docs.github.com/en/rest/search/search#search-issues-and-pull-requests
"""

from __future__ import annotations

import os
import urllib.parse

from ..models import SourceItem
from . import cursor
from .http import clean, get_json

API = "https://api.github.com/search/issues"


def parse(data: dict) -> list[SourceItem]:
    items: list[SourceItem] = []
    for i in data.get("items", []):
        repo = i["repository_url"].split("/repos/", 1)[-1]
        items.append(SourceItem(
            source="github",
            external_id=f"{repo}#{i['number']}",
            title=clean(f"[{repo}] {i.get('title', '')}", 300),
            text=clean(i.get("body")),
            url=i.get("html_url", ""),
            score=(i.get("reactions") or {}).get("+1", 0),
            created_at=cursor.epoch(i.get("created_at")),
        ))
    return items


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch(limit: int, conf: dict, cursors: dict[str, int] | None = None) -> list[SourceItem]:
    """每个查询按点赞数取前 limit 条。有游标时查询加 created:>，只要游标之后新开的 issue。"""
    headers = _headers()
    items: list[SourceItem] = []
    for q in conf.get("queries", []):
        query = q
        if cursors and cursors.get(q):
            query = f"{q} created:>{cursor.github_time(cursors[q])}"
        params = {"q": query, "sort": "reactions", "order": "desc", "per_page": min(limit, 100)}
        got = parse(get_json(f"{API}?{urllib.parse.urlencode(params)}", headers))
        cursor.advance(cursors, q, got)
        items += got
    return items


# ---- 历史回填 ----

def units(conf: dict) -> list[str]:
    return conf.get("queries", [])


def interval() -> float:
    """翻页间隔：搜索接口不带 token 每分钟 10 次、带 token 30 次，各留一点余量。"""
    return 2.5 if os.getenv("GITHUB_TOKEN") else 6.5


def page_before(conf: dict, query: str, before: int | None, pagesize: int) -> list[SourceItem]:
    """回填用：按创建时间倒序取 before 之前新开的 issue。

    和日常抓取不同，这里不按点赞排序：要把符合条件的 issue 完整往回遍历，只能按时间切；
    搜索接口每个查询最多给 1000 条，按时间不断缩小范围也绕开了这个上限。
    """
    q = f"{query} created:<{cursor.github_time(before)}" if before else query
    params = {"q": q, "sort": "created", "order": "desc", "per_page": min(pagesize, 100)}
    return parse(get_json(f"{API}?{urllib.parse.urlencode(params)}", _headers()))
