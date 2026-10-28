"""Qdrant 后端：把 MongoDB 里的向量同步进 Qdrant 建 HNSW 索引，并用它做检索。

可选功能，默认不启用：没有它时一切照旧。依赖 qdrant-client（`pip install 'demand-radar[qdrant]'`），
只在这个模块里导入；Qdrant 服务用 docker compose 的 qdrant profile 启动。

几条设计决定（教学上每一条都值得讲）：
  · MongoDB 是唯一的数据来源，Qdrant 只是一份派生索引，丢了用 `index --rebuild` 重建；
  · 点 ID 用 uuid5(需求 id)：Qdrant 只收无符号整数或 UUID，而我们的 id 形如 reddit/x:t3_1，
    确定性 UUID 让重复 index 是幂等的（同一条永远覆盖同一个点）；原 id 放在 payload 的 _id 里；
  · 榜单（board）、领域、来源、抽取时间、可信度、簇 key 都放 payload，过滤在 Qdrant 里做，
    并给这些字段建 payload 索引——过滤字段不建索引，数据一多就退化成全量扫描；
  · 距离用 Cosine，分数和暴力检索是同一个量纲，相关度标签（0.7 / 0.55）才通用；
  · 同一簇只留一条：用 group_by(group_key) 完成，没有簇的需求 group_key 就是它自己的 id；
  · 索引会过期（需求、簇、待确认状态都会变）：index 会 upsert 并删掉不再入选的点，
    检索时对比条数，发现不一致就提醒去 index；命中的 id 还会回 MongoDB 核对一遍是否仍然入选。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from . import rank, store
from .retriever import Hit, Retrieval

BATCH = 256
INSTALL_HINT = "缺少 qdrant-client：pip install 'demand-radar[qdrant]'（Docker 里用 DR_WITH_QDRANT=1 重新构建镜像）"


def _models():
    try:
        from qdrant_client import models
    except ImportError as e:
        raise SystemExit(INSTALL_HINT) from e
    return models


def connect(url: str):
    """连 Qdrant；连不上给出能照着做的提示，不会悄悄退回暴力检索（那会掩盖教学要看的东西）。"""
    try:
        from qdrant_client import QdrantClient
    except ImportError as e:
        raise SystemExit(INSTALL_HINT) from e
    client = QdrantClient(location=url, timeout=30)  # ":memory:" 是进程内模式，测试用；其余当服务地址
    try:
        client.get_collections()
    except Exception as e:
        raise RuntimeError(f"连不上 Qdrant（{url}）：先启动它 `docker compose --profile qdrant up -d qdrant`，"
                           f"再运行 `demand-radar index` 建索引。原因：{type(e).__name__}") from e
    return client


def collection_name(model: str) -> str:
    """一个嵌入模型一个集合：不同模型的向量不能混在一起比，维度也可能不同。"""
    return "demand_radar__" + re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")


def point_id(doc_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id))


def _ts(dt: datetime | None) -> int:
    if not dt:
        return 0
    return int((dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp())


def payload_of(doc: dict) -> dict:
    """进 payload 的字段：只放过滤和去重要用的，显示用的内容回 MongoDB 取，保持索引小、内容不过期。"""
    return {"_id": doc["_id"], "board": rank.board(doc), "domain": doc.get("domain") or "",
            "source": doc.get("source") or "", "extracted_ts": _ts(doc.get("extracted_at")),
            "confidence": float(doc.get("confidence") or 0.0),
            "group_key": doc.get("cluster_id") or doc["_id"]}


def _indexable(coll, model: str) -> dict:
    """要进索引的：入选（不含可信度阈值，阈值每次查询不同，放 payload 里过滤）且有这个模型的向量。"""
    return {**store._listed(0.0), "embedding_model": model, "embedding": {"$exists": True}}


def ensure_collection(client, name: str, dim: int, rebuild: bool = False) -> None:
    m = _models()
    if rebuild and client.collection_exists(name):
        client.delete_collection(name)
    if client.collection_exists(name):
        return
    client.create_collection(name, vectors_config=m.VectorParams(size=dim, distance=m.Distance.COSINE))
    for field, schema in (("board", m.PayloadSchemaType.KEYWORD), ("domain", m.PayloadSchemaType.KEYWORD),
                          ("source", m.PayloadSchemaType.KEYWORD), ("group_key", m.PayloadSchemaType.KEYWORD),
                          ("extracted_ts", m.PayloadSchemaType.INTEGER),
                          ("confidence", m.PayloadSchemaType.FLOAT)):
        client.create_payload_index(name, field, schema)


def sync(coll, client, model: str, rebuild: bool = False, batch: int = BATCH) -> dict:
    """把 MongoDB 里入选且有向量的需求同步进 Qdrant：upsert 现有的，删掉不再入选的。幂等，可重复运行。"""
    m = _models()
    name = collection_name(model)
    docs = coll.find(_indexable(coll, model))
    pts, current, n, dim = [], set(), 0, 0
    for d in docs:
        vec = list(d["embedding"])
        if not dim:
            dim = len(vec)
            ensure_collection(client, name, dim, rebuild)
        pid = point_id(d["_id"])
        current.add(pid)
        pts.append(m.PointStruct(id=pid, vector=vec, payload=payload_of(d)))
        if len(pts) >= batch:
            client.upsert(name, pts)
            n += len(pts)
            pts = []
    if pts:
        client.upsert(name, pts)
        n += len(pts)
    deleted = 0
    if client.collection_exists(name):
        stale = []
        offset = None
        while True:
            got, offset = client.scroll(name, limit=1000, offset=offset, with_payload=False, with_vectors=False)
            stale += [p.id for p in got if p.id not in current]
            if offset is None:
                break
        if stale:
            client.delete(name, points_selector=m.PointIdsList(points=stale))
            deleted = len(stale)
    return {"collection": name, "dim": dim, "indexed": n, "deleted": deleted}


class QdrantRetriever:
    """用 Qdrant 检索。和 BruteForceRetriever 的约定一致（见 retriever.py），结果应当相同。"""

    name = "qdrant"

    def __init__(self, coll, client, model: str, min_confidence: float = 0.5):
        self.coll, self.client, self.model, self.min_confidence = coll, client, model, min_confidence
        self.collection = collection_name(model)

    def _filter(self, board, domain, source, since):
        m = _models()
        must = [m.FieldCondition(key="confidence", range=m.Range(gte=self.min_confidence))]
        for key, val in (("board", board), ("domain", domain), ("source", source)):
            if val:
                must.append(m.FieldCondition(key=key, match=m.MatchValue(value=val)))
        if since is not None:
            must.append(m.FieldCondition(key="extracted_ts", range=m.Range(gte=_ts(since))))
        return m.Filter(must=must)

    def query(self, qvec, *, k, board=None, domain=None, source=None, since=None, min_similarity=0.0):
        if not self.client.collection_exists(self.collection):
            raise RuntimeError(f"Qdrant 里还没有集合 {self.collection}：先运行 `demand-radar index` 建索引。")
        flt = self._filter(board, domain, source, since)
        res = self.client.query_points_groups(
            self.collection, query=list(qvec), group_by="group_key", limit=k, group_size=1,
            query_filter=flt, score_threshold=min_similarity, with_payload=["_id"])
        hits = [Hit(g.hits[0].payload["_id"], g.hits[0].score) for g in res.groups if g.hits]
        searched = self.client.count(self.collection, count_filter=flt, exact=True).count
        # 「没向量」的条数和暴力检索同一口径：只数这次搜索范围（领域 / 来源 / 时间）内的
        base = store.dashboard_query(self.min_confidence, held=False, domain=domain, source=source, since=since)
        missing = self.coll.count_documents(base) - self.coll.count_documents(
            {**base, "embedding_model": self.model, "embedding": {"$exists": True}})
        notes = []
        in_mongo = self.coll.count_documents(_indexable(self.coll, self.model))
        in_index = self.client.count(self.collection, exact=True).count
        if in_mongo != in_index:
            notes.append(f"Qdrant 索引可能已过期：库里 {in_mongo} 条入选需求有向量，索引里有 {in_index} 条；"
                         f"运行 `demand-radar index` 同步")
        return Retrieval(hits, searched=searched, missing_embeddings=missing, notes=notes)
