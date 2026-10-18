"""数据源：每个源把原始内容统一成 SourceItem 列表。"""

from __future__ import annotations

from ..models import SourceItem
from . import hackernews

# 源名 → 抓取函数(limit) -> list[SourceItem]
REGISTRY = {
    "hackernews": hackernews.fetch,
}


def fetch(source: str, limit: int) -> list[SourceItem]:
    if source not in REGISTRY:
        raise ValueError(f"未知数据源: {source}；可用: {', '.join(REGISTRY)}")
    return REGISTRY[source](limit)
