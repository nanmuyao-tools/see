"""App Store 用户评论（苹果的老「用户评论 RSS」，无需 key）。

每个 app、每个国家按最新排序，一页 50 条，最多 10 页（第 11 页苹果返回 400）。
只留低分评论（默认 1–3 星），星级写进标题让模型看到。评论人昵称不入库。

这个接口不稳定：经常返回没有评论的空 feed，而且被 CDN 缓存一段时间。
空 feed 时换 XML 格式再试一次，还是空就跳过这个 app、不推进游标，下一轮再来。
条款和实测见 docs/research/2026-10-20-app-store-reviews.md。
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET

from ..models import SourceItem
from . import cursor
from .http import clean, get

FEED = "https://itunes.apple.com/{cc}/rss/customerreviews/page={page}/id={id}/sortby=mostrecent/{fmt}"
APP_PAGE = "https://apps.apple.com/{cc}/app/id{id}"
PAGE_SIZE = 50
MAX_PAGES = 10  # 苹果的上限
DEFAULT_PAGES = 2
_NS = {"a": "http://www.w3.org/2005/Atom", "im": "http://itunes.apple.com/rss"}


def _label(e: dict, key: str) -> str:
    v = e.get(key)
    return v.get("label", "") if isinstance(v, dict) else ""


def parse_json(raw: bytes) -> list[dict]:
    feed = json.loads(raw).get("feed") or {}
    entries = feed.get("entry") or []
    if isinstance(entries, dict):  # 只有一条时不是列表
        entries = [entries]
    # 带 im:name 的是 app 本身的元数据，不是评论
    return [{
        "id": _label(e, "id"),
        "title": _label(e, "title"),
        "text": _label(e, "content"),
        "rating": int(_label(e, "im:rating") or 0),
        "votes": int(_label(e, "im:voteSum") or 0),
        "updated": _label(e, "updated"),
    } for e in entries if "im:name" not in e and "im:rating" in e]


def parse_xml(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    rows = []
    for e in root.findall("a:entry", _NS):
        rating = e.findtext("im:rating", "", _NS)
        if not rating:
            continue
        text = next((c.text or "" for c in e.findall("a:content", _NS) if c.get("type") == "text"), "")
        rows.append({
            "id": e.findtext("a:id", "", _NS),
            "title": e.findtext("a:title", "", _NS),
            "text": text,
            "rating": int(rating),
            "votes": int(e.findtext("im:voteSum", "0", _NS) or 0),
            "updated": e.findtext("a:updated", "", _NS),
        })
    return rows


def _page(cc: str, app_id: int | str, page: int) -> list[dict]:
    rows = parse_json(get(FEED.format(cc=cc, page=page, id=app_id, fmt="json")))
    if not rows:  # 空 feed 常见，换个格式只再试一次，不反复刷
        rows = parse_xml(get(FEED.format(cc=cc, page=page, id=app_id, fmt="xml")))
    return rows


def to_items(app: dict, cc: str, rows: list[dict], max_rating: int) -> list[SourceItem]:
    name = app["name"]
    items = []
    for r in rows:
        if not r["id"] or r["rating"] > max_rating:
            continue
        title = f"{name}（App Store {r['rating']} 星）"
        if r["title"]:
            title += f"：{clean(r['title'], 200)}"
        items.append(SourceItem(
            source=f"appstore/{cc}/{name}",
            external_id=r["id"],
            title=title,
            text=clean(r["text"]),
            url=APP_PAGE.format(cc=cc, id=app["id"]),
            score=r["votes"],
            kind="review",
            created_at=cursor.epoch(r["updated"]),
        ))
    return items


def fetch(limit: int, conf: dict, cursors: dict[str, int] | None = None) -> list[SourceItem]:
    """每个 app 的每个国家从最新一页往后翻，翻到游标、翻够 max_pages 页或凑够 limit 条低分评论为止。

    单个 app 失败或拿到空 feed 只跳过它；全部失败才报错。
    """
    max_rating = int(conf.get("max_rating", 3))
    max_pages = min(int(conf.get("max_pages", DEFAULT_PAGES)), MAX_PAGES)
    items: list[SourceItem] = []
    errors: list[str] = []
    for app in conf.get("apps", []):
        for cc in app.get("countries", ["us"]):
            key = f"{cc}/{app['id']}"
            since = (cursors or {}).get(key, 0)
            got: list[SourceItem] = []
            newest = 0
            try:
                for page in range(1, max_pages + 1):
                    rows = _page(cc, app["id"], page)
                    if not rows:
                        break
                    new = [r for r in rows if cursor.epoch(r["updated"]) > since]
                    newest = max([newest] + [cursor.epoch(r["updated"]) for r in new])
                    got += to_items(app, cc, new, max_rating)
                    if len(new) < len(rows) or len(rows) < PAGE_SIZE or len(got) >= limit:
                        break  # 翻到上次看过的、到底了、或者够了
            except Exception as e:
                errors.append(f"{app['name']}/{cc}: {str(e).splitlines()[0][:150]}")
                print(f"      ✗ appstore {errors[-1]}", file=sys.stderr)
                continue
            if not newest:
                print(f"      · appstore {app['name']}/{cc}: 没有新评论或苹果返回空 feed，本轮跳过",
                      file=sys.stderr)
                continue
            # 游标推到看到过的最新一条，高分评论也算，否则下次又翻同样几页
            if cursors is not None and newest > since:
                cursors[key] = newest
            items += got
    total = sum(len(a.get("countries", ["us"])) for a in conf.get("apps", []))
    if errors and len(errors) == total:
        raise RuntimeError("; ".join(errors))
    return items
