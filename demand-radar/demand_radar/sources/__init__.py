"""数据源：每个源把原始内容统一成 SourceItem 列表，配置来自 sources.toml。"""

from __future__ import annotations

import tomllib

from ..models import SourceItem
from . import appstore, arctic_shift, github, googleplay, hn_algolia, http, rss, softwarerecs

# 源名 → 抓取函数(limit, 该源的配置, 游标) -> list[SourceItem]
# arctic_shift 排在 rss 前面：同一条 Reddit 帖子两边都抓到时，fetch_all 保留先到的那条，
# 也就是带 score 和正文的 Arctic Shift 版本
REGISTRY = {
    "softwarerecs": softwarerecs.fetch,
    "hn_algolia": hn_algolia.fetch,
    "github": github.fetch,
    "arctic_shift": arctic_shift.fetch,
    "rss": rss.fetch,
    "appstore": appstore.fetch,
    "googleplay": googleplay.fetch,
}

# 支持历史回填的源：模块要提供 units(conf)、page_before(conf, unit, before, pagesize)、interval()
BACKFILL = {
    "softwarerecs": softwarerecs,
    "hn_algolia": hn_algolia,
    "github": github,
    "arctic_shift": arctic_shift,
}


def backfill_module(name: str):
    if name not in BACKFILL:
        raise ValueError(f"{name} 不支持回填（RSS 订阅和应用商店评论没有按时间翻页）；"
                         f"可回填的源: {', '.join(BACKFILL)}")
    return BACKFILL[name]


def load_config(path: str) -> dict:
    """读 sources.toml；顺带把 [http] 的限速设置交给 http 模块，所有命令都经过这里。"""
    try:
        with open(path, "rb") as f:
            conf = tomllib.load(f)
    except FileNotFoundError:
        raise SystemExit(f"找不到数据源配置文件 {path}。用 --sources-file 指定，或去掉这个参数用默认配置") from None
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"数据源配置文件 {path} 格式有误：{e}") from None
    http.configure(conf.get("http"))
    return conf


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


def fetch_all(names: list[str], limit: int, conf: dict,
              cursors: dict[str, dict[str, int]] | None = None) -> tuple[list[SourceItem], dict[str, str]]:
    """逐个源抓取。单个源失败只记录错误、不影响其他源；同一条内容去重。

    cursors：源名 → {查询: 游标}。抓取时读，抓完原地推进；只有成功抓到的查询会动。
    传 None 表示不用游标，行为和首次运行一样。
    """
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise ValueError(f"未知数据源: {', '.join(unknown)}；可用: {', '.join(REGISTRY)}")
    items: dict[tuple[str, str], SourceItem] = {}
    errors: dict[str, str] = {}
    for name in names:
        cur = None if cursors is None else cursors.setdefault(name, {})
        try:
            for it in REGISTRY[name](limit, conf.get(name) or {}, cur):
                items.setdefault((it.source, it.external_id), it)
        except Exception as e:  # 网络、限流、格式变化等
            errors[name] = str(e).splitlines()[0][:200]
    return list(items.values()), errors
