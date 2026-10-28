"""Qdrant 后端和暴力检索的一致性测试：同一批数据，两个后端必须给出相同的结果。

用 QdrantClient(":memory:")（进程内，不需要服务）。它和真实服务共用同一套过滤 / 分组 / 距离逻辑，
但不建 HNSW 索引（是精确检索），所以这里验证的是「接口约定一致」，近似索引的召回率另见 bench/。
"""

from __future__ import annotations

import random
import unittest
from datetime import UTC, datetime, timedelta

from demand_radar import qdrant_backend as qb
from demand_radar import search
from demand_radar.retriever import BruteForceRetriever

try:
    import mongomock
    from qdrant_client import QdrantClient
except ImportError:  # pragma: no cover
    mongomock = QdrantClient = None

MODEL = "fake-embed:1"
DIM = 16
NOW = datetime.now(UTC)


def vec(seed: str) -> list[float]:
    rnd = random.Random(seed)
    return [rnd.uniform(-1, 1) for _ in range(DIM)]


def embed(texts):
    return [vec("q:" + t) for t in texts]


def doc(_id, source="hackernews", domain="效率", days_ago=1, confidence=0.9, cluster_id=None, model=MODEL,
        emb=True, review_cue="", is_request=True, **kw):
    d = {"_id": _id, "source": source, "need": f"需求 {_id}", "title": f"帖子 {_id}", "url": f"https://x/{_id}",
         "domain": domain, "pain_point": "痛点", "excerpt": "原话", "score": 0, "confidence": confidence,
         "is_request": is_request, "buildable": True, "review_cue": review_cue, "status": "extracted",
         "extracted_at": NOW - timedelta(days=days_ago), **kw}
    if emb:
        d["embedding"], d["embedding_model"] = vec(_id), model
    if cluster_id:
        d["cluster_id"] = cluster_id
    return d


def seed(db):
    db.demands.insert_many([
        doc("hn:1"), doc("hn:2", domain="开发工具"), doc("hn:3", days_ago=40),
        doc("hn:4", confidence=0.4),                          # 可信度不够
        doc("gh:1", source="github"), doc("gh:2", source="github", domain="开发工具"),
        doc("rd:1", source="reddit/macapps"),
        doc("c:1", cluster_id="c:x"), doc("c:2", cluster_id="c:x"), doc("c:3", cluster_id="c:x"),  # 同一簇
        doc("held:1", review_cue="追踪"),                      # 待确认，永远不返回
        doc("notreq:1", is_request=False),                    # 不是需求
        doc("other:1", model="other-model"),                  # 别的模型的向量
        doc("novec:1", emb=False),                            # 没有向量
    ])
    db.clusters.insert_one({"_id": "c:x", "rep_id": "c:1", "members": ["c:1", "c:2", "c:3"], "size": 3,
                            "sources": ["hackernews"]})


@unittest.skipIf(mongomock is None or QdrantClient is None,
                 "需要 mongomock 和 qdrant-client：pip install -e '.[dev]'")
class ParityTest(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        seed(self.db)
        self.client = QdrantClient(":memory:")
        self.stats = qb.sync(self.db.demands, self.client, MODEL)

    def run_both(self, query="找个东西", min_confidence=0.5, **kw):
        kw.setdefault("min_similarity", -1.0)  # 假向量是随机的，相似度一半为负；默认下限 0 会把它们都滤掉
        out = []
        for r in (BruteForceRetriever(self.db.demands, MODEL, min_confidence),
                  qb.QdrantRetriever(self.db.demands, self.client, MODEL, min_confidence)):
            out.append(search.search(self.db.demands, embed, MODEL, query, retriever=r,
                                     min_confidence=min_confidence, **kw))
        return out

    def assert_same(self, **kw):
        b, q = self.run_both(**kw)
        self.assertEqual([r["id"] for r in b["results"]], [r["id"] for r in q["results"]], kw)
        for x, y in zip(b["results"], q["results"], strict=True):
            self.assertAlmostEqual(x["similarity"], y["similarity"], places=3)
            self.assertEqual((x["board"], x["cluster_size"], x["need"]), (y["board"], y["cluster_size"], y["need"]))
        self.assertEqual(b["searched"], q["searched"], kw)
        self.assertEqual(b["missing_embeddings"], q["missing_embeddings"], kw)
        return b, q

    def test_plain_query_same_ids_order_and_scores(self):
        b, _ = self.assert_same(k=20)
        self.assertGreater(len(b["results"]), 3)

    def test_each_filter(self):
        for kw in ({"board": "wish"}, {"board": "project"}, {"domain": "开发工具"}, {"source": "github"},
                   {"days": 7}, {"min_similarity": 0.2}, {"min_similarity": 0.99}, {"min_similarity": 0.0},
                   {"k": 1}, {"k": 3}):
            with self.subTest(kw=kw):
                self.assert_same(**{"k": 20, **kw})

    def test_combined_filters(self):
        self.assert_same(k=20, board="wish", domain="开发工具", days=7)

    def test_min_confidence_is_applied_at_query_time(self):
        lo, _ = self.assert_same(k=30, min_confidence=0.3)
        hi, _ = self.assert_same(k=30, min_confidence=0.5)
        self.assertIn("hn:4", [r["id"] for r in lo["results"]])
        self.assertNotIn("hn:4", [r["id"] for r in hi["results"]])

    def test_cluster_deduped_and_still_returns_k(self):
        b, q = self.assert_same(k=30)
        for res in (b, q):
            self.assertEqual(len([r for r in res["results"] if r["id"].startswith("c:")]), 1)
        self.assertEqual(len(self.run_both(k=5)[1]["results"]), 5)  # 去重后仍凑满 k，不会变少

    def test_held_notrequest_otherfmodel_never_returned(self):
        _, q = self.assert_same(k=50)
        ids = {r["id"] for r in q["results"]}
        self.assertFalse(ids & {"held:1", "notreq:1", "other:1", "novec:1"})

    def test_missing_embeddings_reported_the_same(self):
        b, q = self.run_both(k=5)
        self.assertEqual(b["missing_embeddings"], 2)  # other:1（别的模型）和 novec:1
        self.assertEqual(q["missing_embeddings"], 2)

    def test_point_ids_are_deterministic_uuid5_and_index_is_idempotent(self):
        n = self.client.count(qb.collection_name(MODEL)).count
        self.assertEqual(n, self.stats["indexed"])
        again = qb.sync(self.db.demands, self.client, MODEL)
        self.assertEqual(self.client.count(qb.collection_name(MODEL)).count, n)  # 重复 index 不增加点
        self.assertEqual(again["deleted"], 0)
        self.assertEqual(qb.point_id("hn:1"), qb.point_id("hn:1"))
        self.assertNotEqual(qb.point_id("hn:1"), qb.point_id("hn:2"))
        pts, _ = self.client.scroll(qb.collection_name(MODEL), limit=1, with_payload=True)
        self.assertIn("_id", pts[0].payload)  # 原 id 在 payload 里

    def test_collection_is_per_model_and_dim_matches(self):
        self.assertEqual(qb.collection_name("qwen3-embedding:0.6b"), "demand_radar__qwen3_embedding_0_6b")
        self.assertEqual(self.stats["dim"], DIM)

    def test_stale_index_is_flagged_and_stale_hits_are_dropped(self):
        """需求后来被转去待确认：索引还没同步时，结果不能把它带出来，并且要提醒索引过期。"""
        self.db.demands.update_one({"_id": "hn:1"}, {"$set": {"review_cue": "追踪"}})
        _, q = self.run_both(k=50)
        self.assertNotIn("hn:1", [r["id"] for r in q["results"]])
        self.assertTrue(any("过期" in n for n in q["notes"]), q["notes"])
        again = qb.sync(self.db.demands, self.client, MODEL)
        self.assertEqual(again["deleted"], 1)  # 同步后从索引里删掉
        _, q2 = self.run_both(k=50)
        self.assertEqual(q2["notes"], [])

    def test_new_demand_needs_index_to_be_found(self):
        self.db.demands.insert_one(doc("new:1"))
        _, q = self.run_both(k=50)
        self.assertNotIn("new:1", [r["id"] for r in q["results"]])
        self.assertTrue(q["notes"])
        qb.sync(self.db.demands, self.client, MODEL)
        self.assert_same(k=50)

    def test_cluster_change_is_picked_up_after_reindex(self):
        self.db.demands.update_one({"_id": "hn:1"}, {"$set": {"cluster_id": "c:x"}})
        qb.sync(self.db.demands, self.client, MODEL)
        self.assert_same(k=50)

    def test_rebuild_recreates_collection(self):
        qb.sync(self.db.demands, self.client, MODEL, rebuild=True)
        self.assertEqual(self.client.count(qb.collection_name(MODEL)).count, self.stats["indexed"])

    def test_missing_collection_is_a_clear_error(self):
        r = qb.QdrantRetriever(self.db.demands, self.client, "never-indexed", 0.5)
        with self.assertRaises(RuntimeError) as e:
            search.search(self.db.demands, embed, "never-indexed", "x", retriever=r)
        self.assertIn("demand-radar index", str(e.exception))

    def test_empty_db_indexes_nothing(self):
        db = mongomock.MongoClient().db
        self.assertEqual(qb.sync(db.demands, QdrantClient(":memory:"), MODEL)["indexed"], 0)

    def test_batching(self):
        c = QdrantClient(":memory:")
        s = qb.sync(self.db.demands, c, MODEL, batch=3)
        self.assertEqual(s["indexed"], self.stats["indexed"])
        self.assertEqual(c.count(qb.collection_name(MODEL)).count, self.stats["indexed"])


if __name__ == "__main__":
    unittest.main()
