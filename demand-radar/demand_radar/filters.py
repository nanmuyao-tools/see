"""规则预过滤：明显不是需求的内容不交给模型，省调用、减误报。

每条规则是一个纯函数：命中返回 True。check() 返回第一条命中规则的名字，
都没命中返回 None。规则可在 sources.toml 的 [filters] 里按名字关闭。

设计原则：宁可漏过（交给模型再判），不可误杀。所以规则都很窄：
- 帖子类规则（show_hn、self_promo）只作用于帖子本身，不碰帖子下的评论，
  Show HN 帖子下常有真需求的评论
- 正文为空时，标题里若有求助意图的词就保留，如「Car Information App」
"""

from __future__ import annotations

import re
from collections.abc import Callable

from .models import SourceItem

# Reddit RSS 正文里的模板内容
_BOILERPLATE = re.compile(r"submitted by\s*/u/\S+|\[link\]|\[comments\]", re.I)
_MIN_BODY = 20  # 去掉模板后少于这么多字符，视为没有正文
# 应用商店评论单独放宽：中文短评十来个字就能说清缺什么（「为什么不可以多人共享旧帐本」13 字），
# 按帖子的 20 字门槛会挡掉 18% 的评论（见 docs/research/2026-10-20-app-store-reviews.md 4.2）
_MIN_REVIEW_BODY = 6
# 标题里出现这些词，说明可能是在求某个东西，即使没有正文也保留
_REQUEST_CUE = re.compile(
    r"\b(app|apps|tool|tools|software|program|extension|plugin|widget|service|website|site|"
    r"is there|somebody|someone|anyone|looking for|need|wish|want|alternative)\b|求|有没有|推荐",
    re.I,
)
_SELF_PROMO = re.compile(
    r"^(i|we)(\s+(just|have|'ve))?\s+(made|built|created|launched|released|shipped)\b"
    r"|^i'?m\s+building\b|^we'?re\s+building\b",
    re.I,
)


def _body(item: SourceItem) -> str:
    return _BOILERPLATE.sub("", item.text).strip()


def empty_body(item: SourceItem, conf: dict) -> bool:
    if item.kind == "review":
        # 标题是程序拼的「app 名（N 星）：评论标题」，只把评论人自己写的标题算进来：
        # App Store 的短评常常标题「开屏广告」、正文「广告多」，两边合起来才说清楚
        own_title = item.title.partition("：")[2]
        return len(own_title + _body(item)) < _MIN_REVIEW_BODY
    return len(_body(item)) < _MIN_BODY and not _REQUEST_CUE.search(item.title)


def show_hn(item: SourceItem, conf: dict) -> bool:
    return item.kind == "post" and item.title.lower().startswith("show hn")


def self_promo(item: SourceItem, conf: dict) -> bool:
    return item.kind == "post" and bool(_SELF_PROMO.search(item.title.strip()))


def bot_json(item: SourceItem, conf: dict) -> bool:
    return _body(item).startswith(("{", "[{"))


def subreddit_listing(item: SourceItem, conf: dict) -> bool:
    return item.external_id.startswith("t5_")


def supply_source(item: SourceItem, conf: dict) -> bool:
    return item.source in conf.get("supply_sources", [])


# 顺序即优先级，命中第一条即返回
RULES: dict[str, Callable[[SourceItem, dict], bool]] = {
    "subreddit_listing": subreddit_listing,
    "supply_source": supply_source,
    "bot_json": bot_json,
    "show_hn": show_hn,
    "self_promo": self_promo,
    "empty_body": empty_body,
}


def check(item: SourceItem, conf: dict | None = None) -> str | None:
    """返回命中的规则名；都没命中返回 None。conf 来自 sources.toml 的 [filters]。"""
    conf = conf or {}
    disabled = set(conf.get("disabled", []))
    for name, rule in RULES.items():
        if name not in disabled and rule(item, conf):
            return name
    return None
