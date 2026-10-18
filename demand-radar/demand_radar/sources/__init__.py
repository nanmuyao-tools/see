"""数据源：每个源把原始内容统一成 SourceItem 列表，配置来自 sources.toml。"""

from __future__ import annotations

import tomllib

from ..models import SourceItem
from . import github, hn_algolia, rss, softwarerecs

# 源名 → 抓取函数(limit, 该源的配置) -> list[SourceItem]
REGISTRY = {
    "softwarerecs": softwarerecs.fetch,
    "hn_algolia": hn_algolia.fetch,
    "github": github.fetch,
    "rss": rss.fetch,
}


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def enabled(conf: dict) -> list[str]:
    """配置里启用的源；[[rss]] 有订阅即视为启用。"""
    names = []
    for name in REGISTRY:
        c = conf.get(name)
        if name == "rss":
            if c:
                names.append(name)
        elif c and c.get("enabled", True):
            names.append(name)
    return names


def fetch_all(names: list[str], limit: int, conf: dict) -> tuple[list[SourceItem], dict[str, str]]:
    """逐个源抓取。单个源失败只记录错误、不影响其他源；同一条内容去重。"""
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise ValueError(f"未知数据源: {', '.join(unknown)}；可用: {', '.join(REGISTRY)}")
    items: dict[tuple[str, str], SourceItem] = {}
    errors: dict[str, str] = {}
    for name in names:
        try:
            for it in REGISTRY[name](limit, conf.get(name) or {}):
                items.setdefault((it.source, it.external_id), it)
        except Exception as e:  # 网络、限流、格式变化等
            errors[name] = str(e).splitlines()[0][:200]
    return list(items.values()), errors
