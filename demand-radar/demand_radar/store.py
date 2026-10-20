"""MongoDB 存储：原文 + 处理状态 + 抽取结果，按来源去重。

选 MongoDB 而非关系库：需求的字段会不断演进（加标签、聚类 id、多源
原文等），文档型存储无需改表结构，更灵活。

每条内容都入库，用 status 记录处理到哪一步：
  filtered   被规则预过滤丢弃，filter_reason 记原因，不再处理
  extracted  模型抽取成功
  retry      抽取失败，attempts 未到上限，下次运行再试（正文一并存下，重试不依赖再次抓到）
  failed     抽取失败次数到上限，不再重试

同库另有两个集合：
  cursors    增量抓取的游标（kind=run）与历史回填的进度（kind=backfill），每个源的每个查询一条
  runs       每次运行一条：起止时间、各源抓取数、过滤 / 抽取 / 失败数、耗时，出问题时有据可查
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .models import Demand, SourceItem

SCHEMA_VERSION = 2
MAX_ATTEMPTS = 3


def connect(uri: str, db_name: str, collection: str = "demands"):
    """连接 MongoDB，返回集合对象。延迟导入，缺依赖时报错更清晰。"""
    try:
        from pymongo import MongoClient
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "缺少 pymongo 依赖，请先 `pip install -r requirements.txt`"
        ) from e
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    coll = client[db_name][collection]
    # (source, external_id) 唯一，保证去重
    coll.create_index([("source", 1), ("external_id", 1)], unique=True)
    coll.create_index([("status", 1), ("extracted_at", -1)])
    return coll


def _doc_id(source: str, external_id: str) -> str:
    return f"{source}:{external_id}"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    """pymongo 默认返回不带时区的 UTC 时间，补上时区再参与计算。"""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _to_epoch(dt: datetime | None) -> int:
    return int(_aware(dt).timestamp()) if dt else 0


def _base(item: SourceItem) -> dict[str, Any]:
    doc = {
        "_id": _doc_id(item.source, item.external_id),
        "source": item.source,
        "external_id": item.external_id,
        "kind": item.kind,
        "title": item.title,
        "url": item.url,
        "score": item.score,
        "schema_version": SCHEMA_VERSION,
    }
    if item.created_at:  # 源没给发布时间就不写，读取方按「未知」处理
        doc["created_at"] = datetime.fromtimestamp(item.created_at, tz=UTC)
    return doc


def should_process(coll, item: SourceItem) -> bool:
    """没见过，或者上次失败但还没到重试上限，才需要处理。"""
    doc = coll.find_one({"_id": _doc_id(item.source, item.external_id)},
                        {"status": 1, "attempts": 1})
    if doc is None:
        return True
    return doc.get("status") == "retry" and doc.get("attempts", 0) < MAX_ATTEMPTS


def save_filtered(coll, item: SourceItem, reason: str) -> None:
    doc = {**_base(item), "status": "filtered", "filter_reason": reason, "fetched_at": _now()}
    coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)


def save(coll, item: SourceItem, d: Demand) -> None:
    now = _now()
    doc = {**_base(item), **d.model_dump(), "status": "extracted",
           "fetched_at": now, "extracted_at": now}
    # 重复抓取时覆盖更新（例如之前失败过，这次成功）
    coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)


def record_failure(coll, item: SourceItem, error: str) -> int:
    """记一次失败，返回累计失败次数；到上限后状态改为 failed，不再重试。"""
    _id = _doc_id(item.source, item.external_id)
    prev = coll.find_one({"_id": _id}, {"attempts": 1}) or {}
    attempts = prev.get("attempts", 0) + 1
    status = "failed" if attempts >= MAX_ATTEMPTS else "retry"
    # 正文一并存下：增量抓取后同一条不会再被抓到，重试只能从库里重建
    doc = {**_base(item), "status": status, "attempts": attempts, "text": item.text,
           "last_error": error[:300], "fetched_at": _now()}
    coll.replace_one({"_id": _id}, doc, upsert=True)
    return attempts


def retry_items(coll) -> list[SourceItem]:
    """上次抽取失败、还没到上限的条目，重建成 SourceItem 再试。

    旧版本的失败记录没存正文，无法重建，跳过；它们再次被抓到时仍按 should_process 重试。
    """
    docs = coll.find({"status": "retry", "attempts": {"$lt": MAX_ATTEMPTS}, "text": {"$exists": True}})
    return [
        SourceItem(source=d["source"], external_id=d["external_id"], title=d.get("title", ""),
                   text=d["text"], url=d.get("url", ""), score=d.get("score", 0),
                   kind=d.get("kind", "post"), created_at=_to_epoch(d.get("created_at")))
        for d in docs
    ]


# ---- 增量抓取的游标 ----

def _cursors(coll):
    """游标集合。connect() 返回的是 demands 集合，同库的其他集合从它的 database 上取。"""
    return coll.database["cursors"]


def load_cursors(coll) -> dict[str, dict[str, int]]:
    """源名 → {查询: 上次最新一条的 Unix 秒}。没跑过就是空字典，抓取行为和首次运行一样。"""
    out: dict[str, dict[str, int]] = {}
    for d in _cursors(coll).find({"kind": "run"}):
        out.setdefault(d["source"], {})[d["unit"]] = d["value"]
    return out


def save_cursors(coll, cursors: dict[str, dict[str, int]]) -> None:
    """整批处理完后调用。每个查询一条文档，重复写同一个值也无妨。"""
    now = _now()
    for source, units in cursors.items():
        for unit, value in units.items():
            _id = f"run:{source}:{unit}"
            _cursors(coll).replace_one(
                {"_id": _id},
                {"_id": _id, "kind": "run", "source": source, "unit": unit, "value": value, "updated_at": now},
                upsert=True,
            )


def load_backfill(coll, source: str, unit: str) -> dict[str, Any]:
    """回填进度：oldest 是已拉到的最早一条的 Unix 秒（0 = 还没开始），done 表示已到头。"""
    d = _cursors(coll).find_one({"_id": f"backfill:{source}:{unit}"}) or {}
    return {"oldest": d.get("oldest", 0), "pages": d.get("pages", 0), "done": d.get("done", False)}


def save_backfill(coll, source: str, unit: str, progress: dict[str, Any]) -> None:
    """每拉完一页就记，中断后从这里继续。和增量游标同一个集合，用 kind 区分。"""
    _id = f"backfill:{source}:{unit}"
    _cursors(coll).replace_one(
        {"_id": _id},
        {"_id": _id, "kind": "backfill", "source": source, "unit": unit, **progress, "updated_at": _now()},
        upsert=True,
    )


# ---- 运行记录 ----

def _runs(coll):
    return coll.database["runs"]


def start_run(coll, command: str, **meta) -> Any:
    """运行一开始就写一条 status=running：进程中途死掉也留痕。返回记录 id，结束时用。"""
    doc = {"command": command, "status": "running", "started_at": _now(), **meta}
    return _runs(coll).insert_one(doc).inserted_id


def finish_run(coll, run_id, status: str = "done", **stats) -> float:
    """补上结束时间、耗时和各步计数，返回耗时（秒）。"""
    now = _now()
    doc = _runs(coll).find_one({"_id": run_id}, {"started_at": 1}) or {}
    duration = round((now - _aware(doc["started_at"])).total_seconds(), 1) if doc.get("started_at") else 0.0
    _runs(coll).update_one({"_id": run_id}, {"$set": {
        "status": status, "finished_at": now, "duration_s": duration, **stats}})
    return duration


def source_counts(items: list[SourceItem]) -> list[dict[str, Any]]:
    """各源抓到几条，按数量降序。用列表而不是 {源名: 数} 的字典：源名可能含「.」，不能当字段名。"""
    counts: dict[str, int] = {}
    for it in items:
        counts[it.source] = counts.get(it.source, 0) + 1
    return [{"source": s, "count": n} for s, n in sorted(counts.items(), key=lambda kv: -kv[1])]


def _candidates(min_confidence: float) -> dict[str, Any]:
    return {"is_request": True, "buildable": True, "confidence": {"$gte": min_confidence}}


def _listed(min_confidence: float) -> dict[str, Any]:
    # 旧数据没有 review_cue 字段，按未命中处理（$in 里的 None 能匹配字段缺失）
    return {**_candidates(min_confidence), "review_cue": {"$in": ["", None]}}


def _held(min_confidence: float) -> dict[str, Any]:
    return {**_candidates(min_confidence), "review_cue": {"$nin": ["", None]}}


def held_demands(coll, min_confidence: float, since: datetime | None = None) -> list[dict]:
    """命中有害规则、等待人工确认的需求。"""
    q = _held(min_confidence)
    if since is not None:
        q["extracted_at"] = {"$gte": since}
    return list(coll.find(q))


def top_demands(coll, min_confidence: float) -> list[dict]:
    """取出通过筛选的全部需求（排序交给 rank.sort）。"""
    return list(coll.find(_listed(min_confidence)))


def recent_demands(coll, min_confidence: float, since: datetime) -> list[dict]:
    """取出 since 之后抽取、通过筛选的需求，给每周摘要用。"""
    return list(coll.find({**_listed(min_confidence), "extracted_at": {"$gte": since}}))


# ---- 相似需求合并（v0.3）：向量与簇 ----
# demands 文档加 embedding / embedding_model / cluster_id；同库 clusters 集合每簇一条：
#   rep_id 代表（簇内排序分最高的那条）、need、domain、members（成员 id，按排序分降序）、sources（去重）、
#   total_score（成员热度之和）、best_rank_score、size、model（向量模型，换模型要重建）、updated_at

def _clusters(coll):
    return coll.database["clusters"]


def to_embed(coll, model: str, min_confidence: float) -> list[dict]:
    """通过筛选、还没有当前模型向量的需求。只取向量化要用的字段。"""
    q = {**_listed(min_confidence),
         "$or": [{"embedding": {"$exists": False}}, {"embedding_model": {"$ne": model}}]}
    return list(coll.find(q, {"need": 1, "title": 1}))


def save_embedding(coll, _id: str, vector: list[float], model: str) -> None:
    coll.update_one({"_id": _id}, {"$set": {"embedding": list(vector), "embedding_model": model}})


def unclustered(coll, model: str, min_confidence: float) -> list[dict]:
    """通过筛选、已有当前模型的向量、还没归簇的需求，连向量和排序要用的字段一起取出。"""
    q = {**_listed(min_confidence), "embedding_model": model, "cluster_id": {"$in": [None]}}
    return list(coll.find(q, {"need": 1, "title": 1, "embedding": 1, "score": 1,
                              "clarity": 1, "intensity": 1, "feasibility": 1}))


def member_docs(coll, ids) -> list[dict]:
    """按 id 取成员文档，不带向量（展示和算统计用不着，几百维 × 几千条白白占内存）。"""
    return list(coll.find({"_id": {"$in": list(ids)}}, {"embedding": 0}))


def load_clusters(coll) -> list[dict]:
    return list(_clusters(coll).find())


def rep_vectors(coll, clusters: list[dict]) -> dict[str, list[float]]:
    """簇 id → 代表的向量。向量只存在 demands 里，不在 clusters 里重复一份。"""
    rep_of = {c["rep_id"]: c["_id"] for c in clusters if c.get("rep_id")}
    docs = coll.find({"_id": {"$in": list(rep_of)}, "embedding": {"$exists": True}}, {"embedding": 1})
    return {rep_of[d["_id"]]: d["embedding"] for d in docs}


def save_cluster(coll, doc: dict) -> None:
    _clusters(coll).replace_one({"_id": doc["_id"]}, doc, upsert=True)


def set_cluster(coll, ids: list[str], cluster_id: str) -> None:
    coll.update_many({"_id": {"$in": ids}}, {"$set": {"cluster_id": cluster_id}})


def clear_clusters(coll) -> None:
    """丢掉全部簇和归属，向量保留（换模型时向量会按 embedding_model 不匹配自动重算）。"""
    _clusters(coll).delete_many({})
    coll.update_many({"cluster_id": {"$exists": True}}, {"$unset": {"cluster_id": ""}})


# ---- 看板的只读查询（api.py 用；只读，不改任何文档）----

def dashboard_query(min_confidence: float, *, held: bool = False, domain: str | None = None,
                    source: str | None = None, since: datetime | None = None,
                    q: str | None = None) -> dict[str, Any]:
    """看板筛选条件：入选 / 待确认二选一，再叠加领域、来源、时间、关键词。

    关键词在 need 和 title 里做不分大小写的包含匹配：几千条规模不值得建全文索引，
    而且需求描述是模型写的（中文或英文），全文分词反而不准。
    """
    query = _held(min_confidence) if held else _listed(min_confidence)
    if domain:
        query["domain"] = domain
    if source:
        query["source"] = source
    if since is not None:
        query["extracted_at"] = {"$gte": since}
    if q and q.strip():
        pattern = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"need": pattern}, {"title": pattern}]
    return query


def query_demands(coll, min_confidence: float, **filters) -> list[dict]:
    """按看板条件取需求，排序交给 rank.sort（排序分在读取时算，不入库）。不带向量：几百维 × 几千条用不着。"""
    return list(coll.find(dashboard_query(min_confidence, **filters), {"embedding": 0}))


def facet_counts(coll, field: str, min_confidence: float, **filters) -> list[dict]:
    """某字段（domain / source）各取值的条数，按数量降序，给看板的筛选栏用。

    几千条规模在内存里数就够了，不用 aggregate；空值归为空字符串，由前端显示成「其他」。
    """
    counts: dict[str, int] = {}
    for d in coll.find(dashboard_query(min_confidence, **filters), {field: 1}):
        key = (d.get(field) or "").strip()
        counts[key] = counts.get(key, 0) + 1
    return [{field: k, "count": n} for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def recent_runs(coll, limit: int = 20) -> list[dict]:
    """最近的运行记录，新的在前。同一毫秒开始的用 _id 决胜（ObjectId 单调递增），顺序才稳定。"""
    return list(_runs(coll).find().sort([("started_at", -1), ("_id", -1)]).limit(limit))


def overview(coll, min_confidence: float) -> dict[str, Any]:
    """看板首屏的几个数：库里总条数、各状态条数、入选数、待确认数。"""
    statuses = ("extracted", "filtered", "retry", "failed")
    return {
        "total": coll.count_documents({}),
        "by_status": {s: coll.count_documents({"status": s}) for s in statuses},
        "listed": coll.count_documents(_listed(min_confidence)),
        "held": coll.count_documents(_held(min_confidence)),
    }
