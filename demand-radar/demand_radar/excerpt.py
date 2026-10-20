"""从原文里挑一句最能体现需求的话作为摘录。

为什么不让模型写：实测 qwen2.5:3b 被要求「照抄原文」时，约 3 成的摘录不是原文——
多数被翻译成中文或改写成概括，少数是把几句话拼在一起。改由程序挑选：摘录一定是
原文，模型也少输出一个字段，速度更快。

挑选规则：
1. 帖子的标题本身带求助说法（is there / looking for / 有没有 ……），直接用标题——
   标题是作者自己对需求的概括，StackExchange、Reddit 的提问标题几乎都比正文任何一句贴切
2. 否则按原文顺序，取正文里第一句带求助说法的句子
3. 正文里没有，帖子用标题；评论用正文第一句（评论的标题是所在帖子的标题，不是评论者的话）
单纯的问号不算求助说法：「Does this exist?」「So why not go all the way?」这类泛泛的问句
实测会被误选。
"""

from __future__ import annotations

import re

from .models import SourceItem

MAX_LEN = 300

_CUE = re.compile(
    r"\b(is there|are there|does (anyone|anybody) know|any (app|apps|tool|tools|software|way)|"
    r"looking for|i need|we need|i want|i'd like|i would like|i wish|wish there|"
    r"would (happily )?pay|someone should|somebody (make|build)|someone (make|build)|"
    r"alternative to|recommend)\b"
    r"|有没有|求|想要|希望有|推荐",
    re.I,
)
# 句子切分：英文句末标点后跟空白，或中文句末标点，或换行
_SPLIT = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])|\n+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text or "") if len(s.strip()) >= 8]


def _has_cue(s: str) -> bool:
    return bool(_CUE.search(s))


def _trim(s: str) -> str:
    """过长时在词边界截断，截出来的仍是原文的前缀。"""
    if len(s) <= MAX_LEN:
        return s
    cut = s[:MAX_LEN]
    return cut[: cut.rfind(" ")] if " " in cut else cut


def pick(item: SourceItem) -> str:
    title = (item.title or "").strip()
    if item.kind == "post" and _has_cue(title):
        return _trim(title)
    sentences = _sentences(item.text)
    for s in sentences:  # 按原文顺序，第一句带求助说法的
        if _has_cue(s):
            return _trim(s)
    if item.kind == "post" and title:
        return _trim(title)
    if sentences:
        return _trim(sentences[0])
    # 应用商店的短评常常不到一句话（「ipad冇中文」），整条就是原话
    return _trim(item.text.strip()) if item.kind == "review" else ""
