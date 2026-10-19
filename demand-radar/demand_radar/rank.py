"""综合排序：分项打分加权 + 热度。

排序分在读取时计算、不入库，这样调整权重后不用重新抽取。

    排序分 = clarity×w_clarity + intensity×w_intensity + feasibility×w_feasibility
             + log(1 + 热度)×w_heat

热度取对数：几千赞的帖子不至于把其他需求全压下去。
权重可在 sources.toml 的 [ranking] 里覆盖。旧数据（v0.1 之前）没有分项打分，按 1 分计。
"""

from __future__ import annotations

import math

DEFAULT_WEIGHTS = {
    "clarity": 1.0,      # 需求描述得清不清楚
    "intensity": 1.5,    # 用户有多想要，最能说明值不值得做
    "feasibility": 1.0,  # 个人做起来容不容易
    "heat": 1.0,         # 原帖热度（点赞/得分/投票）
}


def weights(conf: dict | None = None) -> dict[str, float]:
    return {**DEFAULT_WEIGHTS, **(conf or {})}


def score(doc: dict, w: dict[str, float] | None = None) -> float:
    w = w or DEFAULT_WEIGHTS
    return (
        doc.get("clarity", 1) * w["clarity"]
        + doc.get("intensity", 1) * w["intensity"]
        + doc.get("feasibility", 1) * w["feasibility"]
        + math.log1p(max(doc.get("score", 0), 0)) * w["heat"]
    )


def sort(docs: list[dict], w: dict[str, float] | None = None) -> list[dict]:
    """按排序分降序；每条附上 rank_score 便于展示。"""
    for d in docs:
        d["rank_score"] = round(score(d, w), 2)
    return sorted(docs, key=lambda d: d["rank_score"], reverse=True)
