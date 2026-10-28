"""综合排序：分项打分加权 + 热度。

排序分在读取时计算、不入库，这样调整权重后不用重新抽取。

    排序分 = clarity×w_clarity + intensity×w_intensity + feasibility×w_feasibility
             + log(1 + 热度)×w_heat

热度取对数：几千赞的帖子不至于把其他需求全压下去。
权重可在 sources.toml 的 [ranking] 里覆盖。旧数据（v0.1 之前）没有分项打分，按 1 分计。

两个榜：热度信号实际上只有 GitHub 有（HN 评论、softwarerecs、Reddit 新帖基本是 0），不同源的热度没法比，
混在一起排，GitHub 上给 VSCode、terraform 提的高赞 feature request 会占满头部。所以按来源分两个榜——
「愿望榜」（wish，有人想要一个还不存在的东西）和「功能请求榜」（project，给已有项目提的请求，GitHub）——
清单、周报、看板分开展示。热度权重也按榜取：功能请求榜用 heat（热度本来就是主要信号），
愿望榜用 heat_wish（默认 0.3，主要靠分项打分和簇的大小）。
"""

from __future__ import annotations

import math

DEFAULT_WEIGHTS = {
    "clarity": 1.0,      # 需求描述得清不清楚
    "intensity": 1.5,    # 用户有多想要，最能说明值不值得做
    "feasibility": 1.0,  # 个人做起来容不容易
    "heat": 1.0,         # 原帖热度（点赞/得分/投票）；功能请求榜用
    "heat_wish": 0.3,    # 同上，愿望榜用：这些源基本没有热度信号，热度只作微调
}

# 给已有项目提的功能请求：来源名等于它或以「它/」开头（如 github/discussions）。其余都进愿望榜
PROJECT_SOURCES = ("github",)


def weights(conf: dict | None = None) -> dict[str, float]:
    return {**DEFAULT_WEIGHTS, **(conf or {})}


def board_of_source(source: str | None) -> str:
    source = source or ""
    return "project" if any(source == p or source.startswith(p + "/") for p in PROJECT_SOURCES) else "wish"


def board(doc: dict) -> str:
    """需求属于哪个榜：project（给已有项目提的功能请求）或 wish（愿望）。"""
    return board_of_source(doc.get("source"))


def score(doc: dict, w: dict[str, float] | None = None) -> float:
    w = w or DEFAULT_WEIGHTS
    heat_w = w["heat"] if board(doc) == "project" else w.get("heat_wish", DEFAULT_WEIGHTS["heat_wish"])
    return (
        doc.get("clarity", 1) * w["clarity"]
        + doc.get("intensity", 1) * w["intensity"]
        + doc.get("feasibility", 1) * w["feasibility"]
        + math.log1p(max(doc.get("score", 0), 0)) * heat_w
    )


def sort(docs: list[dict], w: dict[str, float] | None = None) -> list[dict]:
    """按排序分降序；每条附上 rank_score 和 board。排序分只在同一个榜内有可比性，
    输出层（清单、周报、看板）按 board 分开展示。"""
    for d in docs:
        d["rank_score"] = round(score(d, w), 2)
        d["board"] = board(d)
    return sorted(docs, key=lambda d: d["rank_score"], reverse=True)


# ---- 簇的排序（v0.3）----
# 同一需求出现得越多越说明有人要，但三个零赞小帖不该压过一个几百赞的大帖：
# 在簇内最高排序分上加 log(成员数)×cluster_size，取对数、权重小（默认 0.5：5 条约 +0.8，20 条约 +1.5，
# 而一档分项打分就是 1–1.5 分）。只有一条时 log(1)=0，单条需求的排序分不变。
DEFAULT_WEIGHTS["cluster_size"] = 0.5


def cluster_score(best_rank_score: float, size: int, w: dict[str, float] | None = None) -> float:
    w = w or DEFAULT_WEIGHTS
    return best_rank_score + math.log(max(size, 1)) * w.get("cluster_size", DEFAULT_WEIGHTS["cluster_size"])
