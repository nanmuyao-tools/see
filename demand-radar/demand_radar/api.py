"""看板后端：FastAPI 直接读 MongoDB，不调模型。

排序分复用 rank.sort（读取时算，不入库），筛选条件复用 store 里的只读查询，
看板里看到的和清单 / 周报里的是同一批、同一个顺序。
静态页在 web/index.html：一个文件、不引外部资源，离线也能开。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.responses import HTMLResponse

from . import rank, store

WEB_DIR = Path(__file__).parent / "web"

# 每条需求返回的字段：和 report._entry 展示的一致，另加 id / confidence / kind 便于前端
ITEM_FIELDS = ("need", "domain", "pain_point", "excerpt", "clarity", "intensity", "feasibility",
               "rank_score", "score", "confidence", "source", "kind", "url", "title", "review_cue")


def _aware(dt: datetime) -> datetime:
    """pymongo 返回不带时区的 UTC 时间，补上时区；前端拿到 ISO 串后自己换算本地时间。"""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt else None


def _jsonable(v: Any) -> Any:
    """运行记录原样返回：ObjectId 转字符串、时间转 ISO，其余类型 JSON 本来就能编码。"""
    if isinstance(v, datetime):
        return _iso(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def _item(d: dict) -> dict:
    out = {k: d.get(k) for k in ITEM_FIELDS}
    out["id"] = d["_id"]
    out["extracted_at"] = _iso(d.get("extracted_at"))
    out["created_at"] = _iso(d.get("created_at"))
    return out


def _run(d: dict) -> dict:
    out = _jsonable({k: v for k, v in d.items() if k != "_id"})
    out["id"] = str(d["_id"])
    return out


def _order(docs: list[dict], sort: str, w: dict[str, float]) -> list[dict]:
    """先算 rank_score（不管按什么排都要展示），再按指定方式排。"""
    docs = rank.sort(docs, w)
    if sort == "heat":
        docs.sort(key=lambda d: d.get("score") or 0, reverse=True)
    elif sort == "time":
        docs.sort(key=lambda d: _aware(d["extracted_at"]).timestamp() if d.get("extracted_at") else 0,
                  reverse=True)
    return docs


def create_app(coll, weights: dict[str, float] | None = None, default_min_confidence: float = 0.5) -> FastAPI:
    """coll 是 store.connect() 返回的 demands 集合（测试里传 mongomock 的）。

    weights 是 sources.toml 里的 [ranking]，和出清单用同一套；default_min_confidence 是
    请求没带 min_confidence 时的筛选阈值，来自配置（--min-confidence / DR_MIN_CONFIDENCE）。
    """
    w = rank.weights(weights)
    app = FastAPI(title="需求雷达看板", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

    def filters(domain: str | None = None,
                days: int | None = Query(None, ge=1, description="最近 N 天，按抽取时间算"),
                min_confidence: float = Query(default_min_confidence, ge=0.0, le=1.0),
                source: str | None = None,
                q: str | None = Query(None, max_length=200, description="在需求描述和原帖标题里包含匹配")) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
        return {"min_confidence": min_confidence, "domain": domain, "source": source, "since": since, "q": q}

    def paging(sort: str = Query("rank", pattern="^(rank|heat|time)$"),
               limit: int = Query(50, ge=1, le=500),
               offset: int = Query(0, ge=0)) -> dict:
        return {"sort": sort, "limit": limit, "offset": offset}

    def listing(held: bool, f: dict, p: dict) -> dict:
        # 排序分要全量算完才能分页；几千条在内存里排很快，不值得为此入库
        docs = _order(store.query_demands(coll, held=held, **f), p["sort"], w)
        page = docs[p["offset"]:p["offset"] + p["limit"]]
        return {"total": len(docs), "offset": p["offset"], "limit": p["limit"], "items": [_item(d) for d in page]}

    @app.get("/api/demands")
    def demands(f: dict = Depends(filters), p: dict = Depends(paging)) -> dict:
        """通过筛选、进清单的需求。"""
        return listing(False, f, p)

    @app.get("/api/held")
    def held(f: dict = Depends(filters), p: dict = Depends(paging)) -> dict:
        """命中有害规则、待人工确认的需求（review_cue 非空），筛选和排序参数同上。"""
        return listing(True, f, p)

    @app.get("/api/domains")
    def domains(f: dict = Depends(filters), held: bool = False) -> list[dict]:
        """各领域条数（held=true 数待确认的）。前端不传 domain，这样选中一个领域时其他领域的数字还在。"""
        return store.facet_counts(coll, "domain", held=held, **f)

    @app.get("/api/sources")
    def sources(f: dict = Depends(filters), held: bool = False) -> list[dict]:
        """各来源条数，给来源下拉框用。"""
        return store.facet_counts(coll, "source", held=held, **f)

    @app.get("/api/runs")
    def runs(limit: int = Query(20, ge=1, le=200)) -> list[dict]:
        """最近的运行记录，新的在前，含各步计数和耗时。"""
        return [_run(d) for d in store.recent_runs(coll, limit)]

    @app.get("/api/stats")
    def stats(min_confidence: float = Query(default_min_confidence, ge=0.0, le=1.0)) -> dict:
        """总条数、入选数、待确认数、最近一次运行。"""
        last = store.recent_runs(coll, 1)
        return {**store.overview(coll, min_confidence), "min_confidence": min_confidence,
                "last_run": _run(last[0]) if last else None}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        # 每次都读文件：页面只有一个、很小，改完刷新即见，不用重启
        return (WEB_DIR / "index.html").read_text(encoding="utf-8")

    return app
