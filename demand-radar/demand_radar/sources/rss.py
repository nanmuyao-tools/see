"""通用 RSS / Atom 数据源：订阅地址在 sources.toml 的 [[rss]] 里配置。"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

from ..models import SourceItem
from . import cursor
from .http import clean, get

_ATOM = "{http://www.w3.org/2005/Atom}"


def _text(el: ET.Element | None) -> str:
    return el.text or "" if el is not None else ""


def parse(feed_name: str, data: bytes, limit: int) -> list[SourceItem]:
    """解析 Atom 或 RSS 2.0。"""
    root = ET.fromstring(data)
    items: list[SourceItem] = []

    for e in root.findall(f"{_ATOM}entry")[:limit]:  # Atom（Reddit 等）
        link = e.find(f"{_ATOM}link")
        url = link.get("href", "") if link is not None else ""
        body = e.find(f"{_ATOM}content")
        if body is None:
            body = e.find(f"{_ATOM}summary")
        items.append(SourceItem(
            source=feed_name,
            external_id=_text(e.find(f"{_ATOM}id")) or url,
            title=clean(_text(e.find(f"{_ATOM}title")), 300),
            text=clean(_text(body)),
            url=url,
            created_at=cursor.epoch(_text(e.find(f"{_ATOM}published"))
                                    or _text(e.find(f"{_ATOM}updated"))),
        ))

    for e in root.findall("./channel/item")[:limit]:  # RSS 2.0
        url = _text(e.find("link"))
        items.append(SourceItem(
            source=feed_name,
            external_id=_text(e.find("guid")) or url,
            title=clean(_text(e.find("title")), 300),
            text=clean(_text(e.find("description"))),
            url=url,
            created_at=cursor.epoch(_text(e.find("pubDate"))),
        ))
    return items


def fetch(limit: int, conf: list[dict], cursors: dict[str, int] | None = None) -> list[SourceItem]:
    """逐个订阅抓取。单个订阅失败只跳过它；全部失败才报错。

    cursors 不用：RSS 没有按时间过滤的参数，订阅本身只给最新几十条，新旧靠入库去重。
    参数保留是为了和其他源的 fetch 签名一致。
    每个订阅可以用 limit 单独设条数，覆盖全局的 --limit：V2EX 这类每天几十帖的订阅，
    全局取 10 条会漏掉大半。
    """
    items: list[SourceItem] = []
    errors: list[str] = []
    for feed in conf:  # 同一网站的订阅之间由 http 统一限速（Reddit 默认 3 秒）
        try:
            items += parse(feed["name"], get(feed["url"]), feed.get("limit", limit))
        except Exception as e:
            msg = f"{feed['name']}: {str(e).splitlines()[0][:150]}"
            errors.append(msg)
            print(f"      ✗ rss {msg}", file=sys.stderr)
    if errors and not items:
        raise RuntimeError(f"全部 {len(errors)} 个订阅都失败了")
    return items
