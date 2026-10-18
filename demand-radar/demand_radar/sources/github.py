"""GitHub issues 数据源：点赞多的 feature request。

用官方搜索 API：不带 token 每分钟 10 次，设置 GITHUB_TOKEN 可提高到每分钟 30 次。
文档：https://docs.github.com/en/rest/search/search#search-issues-and-pull-requests
"""

from __future__ import annotations

import os
import urllib.parse

from ..models import SourceItem
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
        ))
    return items


def fetch(limit: int, conf: dict) -> list[SourceItem]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    items: list[SourceItem] = []
    for q in conf.get("queries", []):
        params = {"q": q, "sort": "reactions", "order": "desc", "per_page": min(limit, 100)}
        items += parse(get_json(f"{API}?{urllib.parse.urlencode(params)}", headers))
    return items
