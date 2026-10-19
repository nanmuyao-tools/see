"""可能有害的需求：用规则标记，交给人工确认，不让模型判断。

「有害」指做出来主要用于追踪、监视、曝光、骚扰或欺骗特定的人。命中规则的需求不进正文清单，
放到报告末尾的「待人工确认」区，并写明命中了哪段原文。周报发布前本来就要人看一遍，
正常需求被误标也不会悄悄消失。

为什么不让模型判断（qwen2.5:3b 实测）：
- 作为字段放进主抽取：4 条有害只挡住 2 条，还连带「能否做成」判断变差，F1 83.7% → 76.0%
- 单独调一次模型判断：模型会想象任何软件被滥用的可能，API 测试工具、推送服务器、借款记账
  都被判有害，60 条评测中英文分别误伤 6 条、12 条；改提示词、改成「选择工具用在谁身上」都没改善
- 先过规则、命中的再问模型：最关键的真实样本（追踪并公开市议员行踪）被模型放过，
  命中规则的正常帖子 5 条里又误判 3 条

规则在 254 条真实帖子里命中 6 条（约 2%），人工确认的负担很小。规则宁宽勿窄：
多标几条只是多看一眼，漏掉才会让有害需求进清单。
"""

from __future__ import annotations

import re

from .models import SourceItem

_CUES = [
    # 特定的他人
    r"\b(my|his|her|their)\s+(wife|husband|girlfriend|boyfriend|partner|spouse|ex|ex-\w+|"
    r"coworker|co-worker|colleague|boss|employee|neighbou?r|roommate|landlord|tenant)\b",
    r"\bsomeone'?s\b", r"\bsomebody'?s\b", r"\b(this|that|a certain|some)\s+(guy|girl|person|user)\b",
    r"\bwhere\s+(he|she|they)\s+(lives?|is|are|goes?|go)\b",
    r"\b(real|true)\s+(person|identity|name|people)\s+behind\b", r"\banonymous\s+(account|user|poster)",
    r"\b(home\s+)?address(es)?\s+(of|from)\b",
    # 隐瞒对方
    r"\bwithout\s+(him|her|them|anyone|the\s+person)\s+(knowing|noticing|finding\s+out|knowledge)",
    r"\bwithout\s+(his|her|their)\s+(knowledge|consent|permission)",
    r"\b(secretly|covertly|discreetly|undetected|stealth(ily)?)\b",
    # 追踪、监视、曝光特定的人
    r"\b(track|tracks|tracking|monitor|monitors|monitoring|spy|spying|stalk\w*|follow)\s+"
    r"(the\s+)?(movements?|location|whereabouts|messages|chats|phone|texts)\s+of\b",
    r"\b(spy|spying|stalk\w*|dox|doxx\w*|doxing|doxed)\b",
    r"\b(make|makes|making)\s+public\b", r"\bexpose\s+(him|her|them|people|who)\b",
    r"\bread\s+(his|her|their|someone'?s)\b",
    # 批量骚扰、操纵
    r"\bmass[- ]?(report|reporting|dm|message|messaging|flag)", r"\breport\s+\w+\s+until\b",
    r"\b(fake|bought|paid)\s+(reviews?|followers|likes|accounts)\b",
    r"\b(thousands|hundreds|dozens)\s+of\s+(\w+[- ]?\w*\s+){0,4}(accounts|reviews|sms|texts|messages|emails)\b",
    # 冒充欺骗
    r"\b(look|looks|appear|appears)\s+like\s+(it\s+)?(comes?\s+from\s+)?(a|the|my|their)\s+bank\b",
    r"\b(spoof\w*|phish\w*|impersonat\w*|scam\s+(people|users|customers))\b",
    # 中文
    r"(老婆|老公|女朋友|男朋友|对象|前任|前女友|前男友|同事|领导|邻居|室友)的",
    r"(偷偷|悄悄|秘密|暗中|不让(他|她|对方)知道|不被(他|她|对方)发现)",
    r"(人肉|开盒|定位(他|她|别人|对方)|查(他|她|别人|对方)的?(住址|地址|位置))",
    r"(批量|群发|刷)(举报|好评|评论|短信|账号)", r"(冒充|仿冒|伪装成)",
]
_RE = re.compile("|".join(f"(?:{c})" for c in _CUES), re.IGNORECASE)


def needs_check(item: SourceItem) -> str:
    """命中的那段文字；没命中返回空串。"""
    m = _RE.search(f"{item.title}\n{item.text}")
    return m.group(0) if m else ""
