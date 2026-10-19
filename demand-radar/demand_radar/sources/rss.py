"""通用 RSS / Atom 数据源：订阅地址在 sources.toml 的 [[rss]] 里配置。"""

from __future__ import annotations

import sys
import time
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


FEED_INTERVAL = 2.0  # 订阅之间的间隔秒数，对网站友好，也降低被限流的概率


def fetch(limit: int, conf: list[dict], cursors: dict[str, int] | None = None) -> list[SourceItem]:
    """逐个订阅抓取。单个订阅失败只跳过它；全部失败才报错。

    cursors 不用：RSS 没有按时间过滤的参数，订阅本身只给最新几十条，新旧靠入库去重。
    参数保留是为了和其他源的 fetch 签名一致。
    """
    items: list[SourceItem] = []
    errors: list[str] = []
    for i, feed in enumerate(conf):
        if i:
            time.sleep(FEED_INTERVAL)
        try:
            items += parse(feed["name"], get(feed["url"]), limit)
        except Exception as e:
            msg = f"{feed['name']}: {str(e).splitlines()[0][:150]}"
            errors.append(msg)
            print(f"      ✗ rss {msg}", file=sys.stderr)
    if errors and not items:
        raise RuntimeError(f"全部 {len(errors)} 个订阅都失败了")
    return items
