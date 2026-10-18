"""MongoDB 存储：原文 + 抽取结果，按来源去重。

选 MongoDB 而非关系库：需求的字段会不断演进（加标签、聚类 id、多源
原文等），文档型存储无需改表结构，更灵活。
"""

from __future__ import annotations

from typing import Any
from .models import Demand, SourceItem


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
    return coll


def _doc_id(source: str, external_id: str) -> str:
    return f"{source}:{external_id}"


def already_seen(coll, source: str, external_id: str) -> bool:
    return coll.find_one({"_id": _doc_id(source, external_id)}) is not None


def save(coll, item: SourceItem, d: Demand) -> None:
    doc: dict[str, Any] = {
        "_id": _doc_id(item.source, item.external_id),
        "source": item.source,
        "external_id": item.external_id,
        "title": item.title,
        "url": item.url,
        **d.model_dump(),
    }
    # 重复抓取时覆盖更新
    coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)


def top_demands(coll, min_confidence: float) -> list[dict]:
    """取出通过过滤的需求，按可信度降序。"""
    cursor = coll.find(
        {
            "is_request": True,
            "buildable": True,
            "confidence": {"$gte": min_confidence},
        }
    ).sort("confidence", -1)
    return list(cursor)
