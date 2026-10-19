"""增量抓取的游标：每个源的每个查询记住「上次抓到的最新一条发布于何时」，下次只要更新的。

游标统一用原帖发布时间的 Unix 秒。三个支持增量的 API 都能按发布时间过滤
（StackExchange fromdate、HN Algolia created_at_i、GitHub created:>），只是格式不同，
拼请求时各自转换；RSS 没有这种过滤，新旧靠入库去重。

游标只在内存里推进，由调用方在整批处理完后统一落库：中途崩溃就不落，下次重抓同一批，
已处理过的被入库去重挡住，既不会丢也不会重复抽取。
"""

from __future__ import annotations

import email.utils
from datetime import datetime, timezone

from ..models import SourceItem


def advance(cursors: dict[str, int] | None, key: str, items: list[SourceItem]) -> None:
    """把 key 的游标推到这批内容里最新的一条。cursors 为 None 表示本次不记游标。"""
    if cursors is None:
        return
    newest = max((it.created_at for it in items), default=0)
    if newest > cursors.get(key, 0):
        cursors[key] = newest


def epoch(s: str | None) -> int:
    """把各源的时间字符串转成 Unix 秒；解析不了返回 0，当作「未知」，不会推进游标。

    GitHub 和 Atom 用 ISO 8601（末尾 Z 或带时区偏移），RSS 2.0 的 pubDate 是 RFC 2822。
    """
    if not s:
        return 0
    try:
        dt = datetime.fromisoformat(s.strip())
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def github_time(epoch_s: int) -> str:
    """GitHub 搜索语法 created:>… 接受的格式。"""
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
