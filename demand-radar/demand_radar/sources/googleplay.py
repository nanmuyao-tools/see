"""Google Play 用户评论。

Google Play 没有能看别人 app 评论的公开 API。这里调的是 Play 网页自己用的内部接口
batchexecute（RPC oCPfdb），请求格式和字段位置参照开源库 google-play-scraper
（MIT，Copyright (c) 2019 PlanB，https://github.com/JoMingyu/google-play-scraper）。
没有直接依赖那个库：它一被导入就会关掉整个进程的 HTTPS 证书校验，出错时还会悄悄返回空列表。
这里走项目自己的 http 层，限速、限流冷却和别的源一样。

接口格式随时可能变：变了会在解析时报错，不会悄悄返回空。
条款和实测见 docs/research/2026-10-20-app-store-reviews.md。
"""

from __future__ import annotations

import json
import sys
import urllib.parse

from ..models import SourceItem
from . import http
from .http import clean

ENDPOINT = "https://play.google.com/_/PlayStoreUi/data/batchexecute?hl={hl}&gl={gl}"
APP_PAGE = "https://play.google.com/store/apps/details?id={package}&hl={hl}&gl={gl}"
SORT_NEWEST = 2
PAGE_SIZE = 100
DEFAULT_PAGES = 2
MAX_PAGES = 5
COOLDOWN = 60  # 被限流后整个域名歇这么久


class PlayFormatError(RuntimeError):
    """返回格式和预期不同：包名写错，或者 Google 改了接口。"""


def _body(package: str, token: str | None) -> bytes:
    page = [PAGE_SIZE, None, token] if token else [PAGE_SIZE]
    inner = [None, [2, SORT_NEWEST, page, None, [None, None, None, None, None, None, None, None, None]],
             [package, 7]]
    req = [[["oCPfdb", json.dumps(inner, separators=(",", ":")), None, "generic"]]]
    return ("f.req=" + urllib.parse.quote(json.dumps(req, separators=(",", ":"))) + "\n").encode()


def parse(raw: bytes) -> tuple[list[dict], str | None]:
    """返回 (评论列表, 下一页 token)。"""
    text = raw.decode("utf-8")
    if "PlayGatewayError" in text:
        raise http.QuotaExhausted("Google Play 限流（PlayGatewayError）")
    start = text.find("[")
    if start < 0:
        raise PlayFormatError("返回里没有 JSON")
    try:
        envelope = json.loads(text[start:text.rfind("]") + 1])
        payload = envelope[0][2]
    except (ValueError, IndexError, TypeError) as e:
        raise PlayFormatError(f"返回格式和预期不同：{e}") from None
    data = json.loads(payload) if payload else None
    if not data or not data[0]:
        # 包名写错时接口不报错，只返回一个没有评论的空壳；这里明说，不悄悄返回空
        raise PlayFormatError("没有拿到评论：包名可能写错了，或者这个 app 在该地区没有评论")
    rows = []
    for r in data[0]:
        try:
            rows.append({
                "id": r[0],
                "rating": int(r[2] or 0),
                "text": r[4] or "",
                "created_at": int(r[5][0]) if r[5] else 0,  # 接口给的就是 Unix 秒
                "thumbs": int(r[6] or 0),
            })
        except (IndexError, TypeError):
            continue  # 单条格式不对就跳过
    token = None
    try:
        t = data[-2][-1]
        token = t if isinstance(t, str) else None
    except (IndexError, TypeError):
        pass
    return rows, token


def _locale(loc: str) -> tuple[str, str]:
    """"zh-TW" → (hl="zh-TW", gl="tw")；"en-US" → ("en-US", "us")；只给 "en" 时地区默认 us。"""
    lang, _, region = loc.partition("-")
    return loc, (region or "us").lower()


def to_items(app: dict, hl: str, gl: str, rows: list[dict], max_rating: int) -> list[SourceItem]:
    name = app["name"]
    return [SourceItem(
        source=f"googleplay/{gl}/{name}",
        external_id=r["id"],
        title=f"{name}（Google Play {r['rating']} 星）",  # Play 的评论没有标题
        text=clean(r["text"]),
        url=APP_PAGE.format(package=app["package"], hl=hl, gl=gl),
        score=r["thumbs"],
        kind="review",
        created_at=r["created_at"],
    ) for r in rows if r["id"] and 0 < r["rating"] <= max_rating]


def fetch(limit: int, conf: dict, cursors: dict[str, int] | None = None) -> list[SourceItem]:
    """每个 app 的每个语言地区按最新排序翻页，翻到游标、翻够 max_pages 页或凑够 limit 条低分评论为止。

    单个 app 失败只跳过它；被限流时整个 play.google.com 冷却，本轮剩下的 app 也不再请求。
    """
    max_rating = int(conf.get("max_rating", 3))
    max_pages = min(int(conf.get("max_pages", DEFAULT_PAGES)), MAX_PAGES)
    items: list[SourceItem] = []
    errors: list[str] = []
    total = 0
    for app in conf.get("apps", []):
        for loc in app.get("locales", ["en-US"]):
            total += 1
            hl, gl = _locale(loc)
            key = f"{gl}/{app['package']}"
            since = (cursors or {}).get(key, 0)
            got: list[SourceItem] = []
            newest = 0
            token = None
            try:
                for page in range(max_pages):
                    raw = http.post(ENDPOINT.format(hl=hl, gl=gl), _body(app["package"], token),
                                    {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
                    try:
                        rows, token = parse(raw)
                    except PlayFormatError:
                        if page == 0:
                            raise
                        break  # 翻到底了
                    new = [r for r in rows if r["created_at"] > since]
                    newest = max([newest] + [r["created_at"] for r in new])
                    got += to_items(app, hl, gl, new, max_rating)
                    if not token or len(new) < len(rows) or len(got) >= limit:
                        break
            except http.QuotaExhausted as e:
                http.limiter.cooldown("play.google.com", COOLDOWN)
                errors.append(f"{app['name']}/{loc}: {e}")
                print(f"      ✗ googleplay {errors[-1]}，本轮不再请求 Google Play", file=sys.stderr)
                break
            except Exception as e:
                errors.append(f"{app['name']}/{loc}: {str(e).splitlines()[0][:150]}")
                print(f"      ✗ googleplay {errors[-1]}", file=sys.stderr)
                continue
            if cursors is not None and newest > since:
                cursors[key] = newest
            items += got
        else:
            continue
        break  # 限流了，外层也停
    if errors and not items and len(errors) >= total:
        raise RuntimeError("; ".join(errors))
    return items
