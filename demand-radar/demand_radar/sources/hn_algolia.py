"""Hacker News 全文搜索（Algolia 官方 API，公开、无需 key）。

按短语精确搜索帖子和评论，比抓 Ask HN 首页的信噪比高得多。
文档：https://hn.algolia.com/api

另有 Ask HN 模式（sources.toml 里写 [hn_algolia.ask_hn]）：每周约 240 个 Ask HN 帖，浓度低（调研抽样约 3/30
是需求），所以先在本地按关键词筛一遍再交给模型。它和短语查询用同一个来源名 hackernews，同一帖两边都命中会去重。
"""

from __future__ import annotations

import re
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


ASK_KEY = "ask_hn"      # Ask HN 在游标表里的键
ASK_PAGE = 100          # Ask HN 每次最多取多少帖：日均约 35 帖，一页够隔几天抓一次；--limit 只管短语查询


def ask_filter(items: list[SourceItem], keywords: list[str]) -> list[SourceItem]:
    """标题或正文里含任一关键词才留（不区分大小写，按整词匹配：app 不会命中 apple）。没配关键词就全留。"""
    if not keywords:
        return items
    pat = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b", re.IGNORECASE)
    return [it for it in items if pat.search(f"{it.title}\n{it.text}")]


def fetch_ask(conf: dict, cursors: dict[str, int] | None) -> list[SourceItem]:
    """Ask HN 帖子，取游标之后的最新 ASK_PAGE 条，本地关键词筛后返回。

    游标按筛之前的结果推进：被筛掉的帖子不用下次再拉一遍。"""
    params = {"query": "", "tags": "ask_hn", "hitsPerPage": conf.get("page", ASK_PAGE)}
    if cursors and cursors.get(ASK_KEY):
        params["numericFilters"] = f"created_at_i>{cursors[ASK_KEY]}"
    got = parse(get_json(f"{API}?{urllib.parse.urlencode(params)}"))
    cursor.advance(cursors, ASK_KEY, got)
    return ask_filter(got, conf.get("keywords", []))


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
    if conf.get("ask_hn") is not None:
        items += fetch_ask(conf["ask_hn"], cursors)
    return items


# ---- 历史回填 ----

MAX_PAGE = 1000  # hitsPerPage 上限


def units(conf: dict) -> list[str]:
    return conf.get("queries", [])


def interval() -> float:
    return 1.0  # 限额每小时 1 万次，很宽松；礼貌起见隔一秒


def page_before(conf: dict, query: str, before: int | None, pagesize: int) -> list[SourceItem]:
    """回填用：取 before 之前发布的最新 pagesize 条。按时间切而不是翻页码，新评论进来也不会错位。"""
    params = _params(query, min(pagesize, MAX_PAGE))
    if before:
        params["numericFilters"] = f"created_at_i<{before}"
    return parse(get_json(f"{API}?{urllib.parse.urlencode(params)}"))
