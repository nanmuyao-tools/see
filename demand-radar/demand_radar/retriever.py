"""检索后端：同一个接口，两种实现。

  BruteForceRetriever  默认。从 MongoDB 取出候选和它们的向量，在内存里逐条算余弦再排序。
                       没有索引、没有额外服务，几千条规模几毫秒——这是第一课：向量就是一串数，检索就是排序。
  QdrantRetriever      可选（见 qdrant_backend.py）。向量放进 Qdrant，用 HNSW 近似索引查。
                       第二课：为什么不精确也够用、过滤怎么和相似度检索结合、什么数据量才需要它。

两者的约定完全一样（search.py 只认这个接口，测试里同一批用例跑两遍，保证结果一致）：
  · 只返回入选的需求（is_request、buildable、可信度够、review_cue 为空），待人工确认的永远不返回；
  · 只在 embedding_model 相同的需求里找；
  · 同一簇只留最像的一条（没有簇的各算一条）；
  · 相似度是余弦，低于 min_similarity 的不返回。
MongoDB 始终是数据的唯一来源；Qdrant 里只是一份可以随时重建的派生索引。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from . import cluster, rank, store


@dataclass
class Hit:
    id: str
    similarity: float
    doc: dict | None = None  # 后端手里已经有完整文档就带上，省一次查库；没有由 search.py 按 id 补


@dataclass
class Retrieval:
    hits: list[Hit]
    searched: int = 0  # 这次实际参与检索的需求条数
    missing_embeddings: int = 0  # 入选但还没有向量的条数，调用方据此提示「先跑 cluster」
    notes: list[str] = field(default_factory=list)  # 给人看的提醒，如「索引可能过期」


class Retriever(Protocol):
    name: str

    def query(self, qvec: list[float], *, k: int, board: str | None = None, domain: str | None = None,
              source: str | None = None, since: datetime | None = None,
              min_similarity: float = 0.0) -> Retrieval: ...


class BruteForceRetriever:
    """暴力检索：每次都把候选的向量从库里读出来，逐条比。"""

    name = "brute"

    def __init__(self, coll, model: str, min_confidence: float = 0.5):
        self.coll, self.model, self.min_confidence = coll, model, min_confidence

    def query(self, qvec, *, k, board=None, domain=None, source=None, since=None, min_similarity=0.0):
        docs, missing = store.search_candidates(self.coll, self.model, self.min_confidence,
                                                domain=domain, source=source, since=since)
        if board:
            docs = [d for d in docs if rank.board(d) == board]
        qv = cluster.unit(qvec)
        scored = sorted(((cluster._dot(qv, cluster.unit(d["embedding"])), d) for d in docs),
                        key=lambda t: t[0], reverse=True)
        hits, seen = [], set()
        for sim, d in scored:
            if sim < min_similarity or len(hits) >= k:
                break
            cid = d.get("cluster_id")
            if cid and cid in seen:
                continue
            seen.add(cid)
            hits.append(Hit(d["_id"], sim, d))
        return Retrieval(hits, searched=len(docs), missing_embeddings=missing)
