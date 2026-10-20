"""Reddit 数据源：走 Arctic Shift（第三方公开归档 API，无需 key）。调研与条款见 docs/arctic-shift.md。

比起直接抓 Reddit 的 RSS：不会被 429、能按时间往回翻历史、老帖带真实 score。
代价是依赖一个志愿者维护的免费服务（文档原话「No uptime or performance guarantees」），
所以 RSS 订阅保留做兜底：两边产出同样的 source 名（reddit/<版块>）和 external_id（t3_…），
入库时互相去重，不会重复抽取。
文档：https://github.com/ArthurHeitmann/arctic_shift/blob/master/api/README.md
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.parse

from ..models import SourceItem
from . import cursor
from .http import clean, get_json

API = "https://arctic-shift.photon-reddit.com/api/posts/search"
# fields= 只接受文档列出的白名单（name、permalink 等都会 400），所以 t3_ 前缀和帖子链接自己拼
FIELDS = "id,subreddit,title,selftext,created_utc,retrieved_on,score,num_comments,url,crosspost_parent,over_18"
MAX_PAGE = 100          # limit 上限（"auto" 可到 1000，但视服务器负载而定，不用）
RATE_WAIT_MAX = 60      # 限额按 60 秒窗口计，X-RateLimit-Reset 是到下个窗口的秒数
RATE_WAIT_DEFAULT = 10  # 429 却没给 X-RateLimit-Reset 时等多久
# 422「Timeout. Maybe slow down a bit」是服务端查询超时，和参数无关、随机出现：2026-10-20 实测同一条查询
# 连续 3 次 422、8 秒后原样重发就 200，像是首次查询慢、后台预热后可用。所以退避重试三次，不是一次
TIMEOUT_WAITS = (5, 10, 20)
_REMOVED = {"[removed]", "[deleted]"}  # 被删的帖子归档里正文只剩这个占位


def parse(data: dict) -> list[SourceItem]:
    """把一页响应转成 SourceItem。成人版块内容跳过：不是软件需求，也是 Reddit 政策限制访问的内容。"""
    items: list[SourceItem] = []
    for p in data.get("data") or []:
        if p.get("over_18"):
            continue
        sub = p.get("subreddit") or ""
        text = p.get("selftext") or ""
        if text.strip() in _REMOVED:
            text = ""  # 交给 empty_body 规则：标题像求助就留，否则丢
        items.append(SourceItem(
            source=f"reddit/{sub}",
            external_id=f"t3_{p['id']}",
            title=clean(p.get("title"), 300),
            text=clean(text),
            url=f"https://www.reddit.com/r/{sub}/comments/{p['id']}/",
            score=p.get("score") or 0,
            created_at=p.get("created_utc") or 0,
        ))
    return items


def matches(item: SourceItem, keywords: list[str] | None) -> bool:
    """本地关键词过滤：标题或正文含任一短语（不区分大小写）。没配关键词就全要。

    为什么不用 API 的 title= / query= 参数：实测 4 次全部 422 超时，文档也说活跃版块不支持，
    按版块拉全量再本地筛才稳定。
    """
    if not keywords:
        return True
    hay = f"{item.title}\n{item.text}".lower()
    return any(k.lower() in hay for k in keywords)


def _reset_seconds(e: urllib.error.HTTPError) -> float:
    v = e.headers.get("X-RateLimit-Reset") if e.headers else None
    return min(int(v), RATE_WAIT_MAX) if v and v.isdigit() else RATE_WAIT_DEFAULT


def _get(params: dict) -> dict:
    """请求一页。429 按 X-RateLimit-Reset 等到窗口重置再试一次；422 查询超时按 TIMEOUT_WAITS 退避重试。

    http.get 已按 Retry-After 重试 429，但这个服务不发 Retry-After、只发 X-RateLimit-Reset，
    所以最后一次机会在这里按它的头再等一回。
    """
    url = f"{API}?{urllib.parse.urlencode(params)}"
    waits = list(TIMEOUT_WAITS)
    rate_retried = False
    while True:
        try:
            return get_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 422 and waits:
                time.sleep(waits.pop(0))
            elif e.code == 429 and not rate_retried:
                rate_retried = True
                time.sleep(_reset_seconds(e))
            else:
                raise


def _params(subreddit: str, pagesize: int) -> dict:
    return {"subreddit": subreddit, "limit": min(pagesize, MAX_PAGE), "sort": "desc", "fields": FIELDS}


def fetch(limit: int, conf: dict, cursors: dict[str, int] | None = None) -> list[SourceItem]:
    """每个版块抓最新 limit 条；有游标时用 after= 只要游标之后发布的（after 不含端点，实测）。

    和其他源一样每次只取最新 limit 条：版块日发帖量超过 limit 时中间的会跳过，调大 --limit 即可。
    游标按抓到的全部帖子推进，关键词没命中的也算见过，下次不再拉。

    一个版块重试后仍 422 / 429 只跳过它（游标不动，下次再拉），其他版块照抓；全部版块都失败才算该源失败。
    """
    keywords = conf.get("keywords") or {}
    items: list[SourceItem] = []
    subs = conf.get("subreddits", [])
    failed: list[tuple[str, Exception]] = []
    for sub in subs:  # 版块之间的间隔由 http 统一限速
        params = _params(sub, limit)
        if cursors and cursors.get(sub):
            params["after"] = cursors[sub]
        try:
            got = parse(_get(params))
        except urllib.error.HTTPError as e:
            if e.code not in (422, 429):
                raise
            print(f"      ✗ arctic_shift r/{sub} 跳过：HTTP {e.code}（服务端超时或限流，下次再拉）", file=sys.stderr)
            failed.append((sub, e))
            continue
        cursor.advance(cursors, sub, got)
        items += [it for it in got if matches(it, keywords.get(sub))]
    if failed and len(failed) == len(subs):
        raise failed[-1][1]
    return items


# ---- 历史回填 ----

def units(conf: dict) -> list[str]:
    return conf.get("subreddits", [])


def interval() -> float:
    return 2.0  # 文档说每秒几次没问题，但限额随负载浮动、又是免费服务，宁慢勿快


def page_before(conf: dict, subreddit: str, before: int | None, pagesize: int) -> list[SourceItem]:
    """回填用：取 before 之前发布的最新 pagesize 条（before 不含端点，实测）。

    这里不做关键词过滤：回填进度按页上最早一条推进，整页都被筛掉会被当成「到头」，
    历史也本来就该完整拉回来，噪音交给规则预过滤和模型。
    """
    params = _params(subreddit, pagesize)
    if before:
        params["before"] = before
    return parse(_get(params))
