"""MongoDB 存储：原文 + 处理状态 + 抽取结果，按来源去重。

选 MongoDB 而非关系库：需求的字段会不断演进（加标签、聚类 id、多源
原文等），文档型存储无需改表结构，更灵活。

每条内容都入库，用 status 记录处理到哪一步：
  filtered   被规则预过滤丢弃，filter_reason 记原因，不再处理
  extracted  模型抽取成功
  retry      抽取失败，attempts 未到上限，下次运行再试
  failed     抽取失败次数到上限，不再重试
"""

from __future__ import annotations

from datetime import datetime, timezone
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
    return datetime.now(timezone.utc)


def _base(item: SourceItem) -> dict[str, Any]:
    return {
        "_id": _doc_id(item.source, item.external_id),
        "source": item.source,
        "external_id": item.external_id,
        "kind": item.kind,
        "title": item.title,
        "url": item.url,
        "score": item.score,
        "schema_version": SCHEMA_VERSION,
    }


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
    doc = {**_base(item), "status": status, "attempts": attempts,
           "last_error": error[:300], "fetched_at": _now()}
    coll.replace_one({"_id": _id}, doc, upsert=True)
    return attempts


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
