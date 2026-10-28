"""数据源：每个源把原始内容统一成 SourceItem 列表，配置来自 sources.toml。"""

from __future__ import annotations

import tomllib

from ..models import SourceItem
from . import appstore, arctic_shift, bluesky, github, googleplay, hn_algolia, http, rss, softwarerecs

# 源名 → 抓取函数(limit, 该源的配置, 游标) -> list[SourceItem]
# arctic_shift 排在 rss 前面：同一条 Reddit 帖子两边都抓到时，fetch_all 保留先到的那条，
# 也就是带 score 和正文的 Arctic Shift 版本
REGISTRY = {
    "softwarerecs": softwarerecs.fetch,
    "hn_algolia": hn_algolia.fetch,
    "bluesky": bluesky.fetch,
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


# 每个源的使用风险，给用户自己选源时看（`demand-radar sources` 列出）。这是我们对条款的理解，不是法律意见，
# 具体以各平台当前条款为准；平台条款会变（Reddit 2025-11 起收紧官方 API 就是例子）。
#   open    官方公开接口，内容公开，守限速就行
#   terms   官方接口，但内容协议或条款有要遵守的地方
#   gray    官方不允许、不鼓励，或来源本身是第三方归档：默认关闭，用户自己决定要不要开
#   varies  取决于订阅哪个站点，每个订阅单独用 enabled 开关
PROVIDERS = {
    "softwarerecs": {"risk": "terms", "terms": "https://api.stackexchange.com/docs",
                     "note": "内容是 CC BY-SA，要署名（清单里每条都附原帖链接）；条款对用内容训练模型有限制，我们只做抽取分析"},
    "hn_algolia": {"risk": "open", "terms": "https://hn.algolia.com/api", "note": "官方公开搜索接口"},
    "github": {"risk": "open", "terms": "https://docs.github.com/en/rest", "note": "官方 REST 接口，公开 issue"},
    "bluesky": {"risk": "open", "terms": "https://bsky.social/about/support/tos",
                "note": "开放协议的官方公开接口；未登录只有 api.bsky.app 可用"},
    "arctic_shift": {"risk": "gray", "terms": "docs/arctic-shift.md",
                     "note": "Reddit 官方 API 禁止商业使用，这里是志愿者维护的第三方归档，无可用性承诺；条款仍是灰色"},
    "rss": {"risk": "varies", "terms": "", "note": "取决于订阅的站点；自带的 Reddit、V2EX 订阅默认关闭，"
                                                   "V2EX 的条款页当时打不开，没读到原文"},
    "appstore": {"risk": "gray", "terms": "docs/research/2026-10-20-app-store-reviews.md",
                 "note": "苹果的条款和 robots.txt 都不鼓励自动抓取"},
    "googleplay": {"risk": "gray", "terms": "docs/research/2026-10-20-app-store-reviews.md",
                   "note": "Google 的条款和 robots.txt 都不鼓励自动抓取"},
}


def describe(conf: dict) -> list[dict]:
    """每个源一行：名字、是否启用、风险档、条款链接、说明；rss 另附每个订阅的开关。"""
    on = set(enabled(conf))
    rows = []
    for name in REGISTRY:
        row = {"name": name, "enabled": name in on, **PROVIDERS[name]}
        if name == "rss":
            row["feeds"] = [(f.get("name", "?"), f.get("enabled", True)) for f in conf.get("rss") or []]
        rows.append(row)
    return rows


def notices(names: list[str]) -> list[str]:
    """本次要用的源里，风险档是 gray / varies 的，各给一行提示（谁开的、读哪里的条款）。"""
    out = []
    for n in names:
        meta = PROVIDERS.get(n)
        if meta and meta["risk"] in ("gray", "varies"):
            out.append(f"{n}（{meta['risk']}）：{meta['note']}" + (f"。条款：{meta['terms']}" if meta["terms"] else ""))
    return out


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
    """配置里启用的源；[[rss]] 有一个没关掉的订阅即视为启用。"""
    names = []
    for name in REGISTRY:
        c = conf.get(name)
        if name == "rss":
            if c and any(f.get("enabled", True) for f in c):  # 订阅各有开关，全关等于整源关闭
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
