"""相似需求合并的离线测试：向量全是假的（几维的小向量），不碰嵌入服务，库用 mongomock。"""

from __future__ import annotations

import os
import tempfile
import unittest

from demand_radar import cluster, rank, report

try:
    import mongomock
except ImportError:  # pragma: no cover
    mongomock = None

# 假向量：同一方向 = 同一需求。文本 → 向量，sync 和 score_pairs 都用它当 embed 函数
VECS = {
    "快递追踪": [1.0, 0.0, 0.0],
    "包裹物流一处看": [0.98, 0.2, 0.0],     # 和「快递追踪」余弦 ≈ 0.98
    "密码管理器": [0.0, 1.0, 0.0],
    "自托管密码库": [0.1, 0.99, 0.0],        # 和「密码管理器」≈ 0.99
    "菜单栏监控": [0.0, 0.0, 1.0],
    "提醒吃药": [0.5, 0.5, 0.7],              # 和谁都不到 0.85
}


class FakeEmbed:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return [VECS.get(t, [0.3, 0.3, 0.9]) for t in texts]


def vec_math_close(a, b):
    return abs(a - b) < 1e-6


class VectorMathTest(unittest.TestCase):
    def test_unit_and_cosine(self):
        self.assertTrue(vec_math_close(sum(x * x for x in cluster.unit([3.0, 4.0])), 1.0))
        self.assertTrue(vec_math_close(cluster.cosine([1, 0], [2, 0]), 1.0))
        self.assertTrue(vec_math_close(cluster.cosine([1, 0], [0, 5]), 0.0))
        self.assertEqual(cluster.unit([0.0, 0.0]), [0.0, 0.0])  # 零向量不除零

    def test_text_of_prefers_need_then_title(self):
        self.assertEqual(cluster.text_of({"need": " 一个工具 ", "title": "t"}), "一个工具")
        self.assertEqual(cluster.text_of({"need": "", "title": "旧数据标题"}), "旧数据标题")
        self.assertEqual(cluster.text_of({}), "")


class AssignTest(unittest.TestCase):
    def test_joins_closest_cluster_above_threshold(self):
        reps = [("c:a", cluster.unit([1, 0, 0])), ("c:b", cluster.unit([0, 1, 0]))]
        got = cluster.assign([("x", cluster.unit([0.9, 0.3, 0]))], reps, 0.85)
        self.assertEqual(got, {"x": "c:a"})
        self.assertEqual(len(reps), 2)  # 没开新簇

    def test_far_item_seeds_new_cluster_that_later_items_can_join(self):
        reps = [("c:a", cluster.unit([1, 0, 0]))]
        got = cluster.assign([("y", cluster.unit([0, 0, 1])), ("z", cluster.unit([0, 0.1, 1]))], reps, 0.85)
        self.assertEqual(got, {"y": "c:y", "z": "c:y"})
        self.assertEqual([cid for cid, _ in reps], ["c:a", "c:y"])

    def test_threshold_is_inclusive_and_respected(self):
        reps = [("c:a", [1.0, 0.0])]
        self.assertEqual(cluster.assign([("x", [1.0, 0.0])], list(reps), 1.0), {"x": "c:a"})
        self.assertEqual(cluster.assign([("x", [0.0, 1.0])], list(reps), 0.5), {"x": "c:x"})

    def test_empty(self):
        self.assertEqual(cluster.assign([], [], 0.85), {})


def demand(_id, need, source="hackernews", score=0, confidence=0.9, **extra):
    return {"_id": _id, "source": source, "external_id": _id, "title": f"帖子 {_id}", "url": f"https://x/{_id}",
            "need": need, "domain": "效率", "pain_point": "", "excerpt": "", "is_request": True, "buildable": True,
            "review_cue": "", "confidence": confidence, "score": score, "clarity": 3, "intensity": 3,
            "feasibility": 3, "status": "extracted", **extra}


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -r requirements-dev.txt")
class SyncTest(unittest.TestCase):
    def setUp(self):
        self.coll = mongomock.MongoClient().db.demands
        self.embed = FakeEmbed()
        self.w = rank.weights()

    def sync(self, model="m1", **kw):
        return cluster.sync(self.coll, self.embed, model, 0.85, self.w, 0.5, log=lambda *_: None, **kw)

    def clusters(self):
        return {c["_id"]: c for c in self.coll.database["clusters"].find()}

    def test_embeds_listed_only_and_merges_similar(self):
        self.coll.insert_many([
            demand("a", "快递追踪", score=10),
            demand("b", "包裹物流一处看", source="reddit/software", score=3),
            demand("c", "密码管理器"),
            demand("held", "快递追踪", review_cue="my ex"),        # 待人工确认的不参与
            demand("low", "快递追踪", confidence=0.2),              # 没过阈值的不参与
            {"_id": "f", "status": "filtered", "source": "s", "external_id": "f"},
        ])
        stats = self.sync()
        self.assertEqual(stats, {"embedded": 3, "assigned": 3, "new_clusters": 2, "rebuilt": False})
        self.assertEqual(sorted(sum(self.embed.calls, [])), ["包裹物流一处看", "密码管理器", "快递追踪"])

        a, b, c = (self.coll.find_one({"_id": i}) for i in "abc")
        self.assertEqual((a["embedding"], a["embedding_model"]), (VECS["快递追踪"], "m1"))
        self.assertEqual(a["cluster_id"], b["cluster_id"])
        self.assertNotEqual(a["cluster_id"], c["cluster_id"])
        self.assertNotIn("cluster_id", self.coll.find_one({"_id": "held"}))

        cl = self.clusters()[a["cluster_id"]]
        self.assertEqual(cl["_id"], "c:a")  # a 分高，先处理，成为种子
        self.assertEqual((cl["rep_id"], cl["need"], cl["members"]), ("a", "快递追踪", ["a", "b"]))
        self.assertEqual(cl["sources"], ["hackernews", "reddit/software"])
        self.assertEqual((cl["total_score"], cl["size"], cl["model"]), (13, 2, "m1"))
        self.assertEqual(cl["best_rank_score"], round(rank.score(a, self.w), 2))
        self.assertIn("updated_at", cl)

    def test_incremental_run_only_embeds_new_and_joins_existing(self):
        self.coll.insert_many([demand("a", "快递追踪", score=5), demand("c", "密码管理器")])
        self.sync()
        self.coll.insert_many([demand("d", "自托管密码库", source="softwarerecs", score=50)])
        stats = self.sync()
        self.assertEqual(stats, {"embedded": 1, "assigned": 1, "new_clusters": 0, "rebuilt": False})
        self.assertEqual(self.embed.calls[-1], ["自托管密码库"])
        cl = self.clusters()["c:c"]
        self.assertEqual(sorted(cl["members"]), ["c", "d"])
        self.assertEqual(cl["rep_id"], "d")  # 新成员分更高，代表换成它；簇 id 仍按种子
        self.assertEqual(cl["sources"], ["hackernews", "softwarerecs"])
        self.assertEqual(self.coll.find_one({"_id": "d"})["cluster_id"], "c:c")

    def test_nothing_to_do_is_a_noop(self):
        self.coll.insert_many([demand("a", "快递追踪")])
        self.sync()
        stats = self.sync()
        self.assertEqual(stats, {"embedded": 0, "assigned": 0, "new_clusters": 0, "rebuilt": False})
        self.assertEqual(len(self.embed.calls), 1)

    def test_model_change_reembeds_and_rebuilds(self):
        self.coll.insert_many([demand("a", "快递追踪"), demand("b", "包裹物流一处看")])
        self.sync("m1")
        stats = self.sync("m2")
        self.assertEqual(stats, {"embedded": 2, "assigned": 2, "new_clusters": 1, "rebuilt": True})
        self.assertEqual(self.coll.find_one({"_id": "a"})["embedding_model"], "m2")
        (cl,) = self.clusters().values()
        self.assertEqual((cl["model"], sorted(cl["members"])), ("m2", ["a", "b"]))

    def test_rebuild_flag_drops_clusters_but_keeps_vectors(self):
        self.coll.insert_many([demand("a", "快递追踪"), demand("c", "密码管理器")])
        self.sync()
        stats = self.sync(rebuild=True)
        self.assertEqual(stats, {"embedded": 0, "assigned": 2, "new_clusters": 2, "rebuilt": True})
        self.assertEqual(len(self.embed.calls), 1)

    def test_rebuild_on_empty_db_is_not_flagged(self):
        self.assertEqual(self.sync(rebuild=True)["rebuilt"], False)

    def test_batches_and_saves_per_batch(self):
        self.coll.insert_many([demand(str(i), f"需求 {i}") for i in range(cluster.BATCH + 3)])
        self.sync()
        self.assertEqual([len(c) for c in self.embed.calls], [cluster.BATCH, 3])

    def test_skips_docs_with_nothing_to_embed(self):
        self.coll.insert_many([demand("e", "", title=""), demand("a", "快递追踪")])
        self.assertEqual(self.sync()["embedded"], 1)


class GroupTest(unittest.TestCase):
    def rows(self):
        rows = [demand("a", "快递追踪", score=10), demand("b", "包裹物流一处看", source="reddit/software", score=3),
                demand("c", "密码管理器", score=100), demand("old", "旧数据")]
        for r in rows[:2]:
            r["cluster_id"] = "c:a"
        rows[2]["cluster_id"] = "c:c"
        return rank.sort(rows)

    def clusters(self):
        return [{"_id": "c:a", "rep_id": "a", "members": ["a", "b", "x"], "sources": ["hackernews", "reddit/software"],
                 "size": 3, "best_rank_score": 0},
                {"_id": "c:c", "rep_id": "c", "members": ["c"], "sources": ["hackernews"], "size": 1}]

    def test_folds_members_under_best_ranked_and_keeps_plain_rows(self):
        members = [demand("x", "快递追踪 旧成员", source="softwarerecs", score=1, confidence=0.3)]  # 不在视图里的成员
        got = cluster.group(self.rows(), self.clusters(), members)
        self.assertEqual([r["_id"] for r in got], ["c", "a", "old"])  # c 热度 100 排第一；b 折进 a；old 原样
        a = got[1]
        self.assertEqual((a["cluster_size"], a["cluster_sources"]), (3, ["hackernews", "reddit/software"]))
        self.assertEqual([m["_id"] for m in a["cluster_members"]], ["b", "x"])
        self.assertNotIn("cluster_size", got[2])
        self.assertAlmostEqual(a["rank_score"], round(rank.cluster_score(rank.score(a), 3), 2))
        self.assertEqual(got[0]["rank_score"], round(rank.score(got[0]), 2))  # 单条簇没有加成

    def test_size_bonus_can_reorder(self):
        w = rank.weights({"cluster_size": 50.0})
        got = cluster.group(self.rows(), self.clusters(), [], w)
        self.assertEqual(got[0]["_id"], "a")

    def test_unknown_cluster_id_is_treated_as_plain(self):
        rows = rank.sort([demand("a", "x", cluster_id="c:gone")])
        got = cluster.group(rows, [], [])
        self.assertEqual(got, rows)

    def test_rep_is_best_in_view_not_stored_rep(self):
        """周报只含最近几条：代表从视图内选，簇里更早的代表只作为链接出现。"""
        rows = rank.sort([demand("b", "包裹物流一处看", cluster_id="c:a")])
        cl = [{"_id": "c:a", "rep_id": "a", "members": ["a", "b"], "sources": ["hackernews"], "size": 2}]
        got = cluster.group(rows, cl, [demand("a", "快递追踪", score=99)])
        self.assertEqual(got[0]["_id"], "b")
        self.assertEqual([m["_id"] for m in got[0]["cluster_members"]], ["a"])


class ClusterRankTest(unittest.TestCase):
    def test_log_bonus(self):
        self.assertEqual(rank.cluster_score(10.0, 1), 10.0)
        self.assertAlmostEqual(rank.cluster_score(10.0, 5), 10.0 + 0.5 * 1.6094, places=3)
        self.assertEqual(rank.cluster_score(10.0, 5, rank.weights({"cluster_size": 0})), 10.0)

    def test_default_weights_still_apply_to_single_docs(self):
        self.assertEqual(rank.score({"clarity": 3, "intensity": 3, "feasibility": 3}), 3 + 4.5 + 3)


class ReportClusterTest(unittest.TestCase):
    def _out(self):
        return os.path.join(tempfile.mkdtemp(), "o.md")

    def _rep(self):
        return {**demand("a", "一个快递追踪工具", score=10), "cluster_size": 3,
                "cluster_sources": ["hackernews", "reddit/software"],
                "cluster_members": [demand("b", "x", source="reddit/software"), demand("x", "y")]}

    def test_zh_cluster_line_and_member_links(self):
        p = self._out()
        report.write_markdown([self._rep()], p, "zh")
        s = open(p, encoding="utf-8").read()
        self.assertIn("## 1. 一个快递追踪工具", s)
        self.assertIn("- 来自 2 个来源，共 3 条\n  - [reddit/software](https://x/b) 帖子 b\n  - [hackernews](https://x/x) 帖子 x", s)
        self.assertNotIn("## 2.", s)

    def test_total_heat_shown_only_when_nonzero(self):
        p = self._out()
        report.write_markdown([{**self._rep(), "cluster_heat": 15}], p, "zh")
        self.assertIn("- 来自 2 个来源，共 3 条，总热度 15\n", open(p, encoding="utf-8").read())
        report.write_markdown([{**self._rep(), "cluster_heat": 15}], p, "en")
        self.assertIn("- From 2 sources, 3 posts, total heat 15\n", open(p, encoding="utf-8").read())

    def test_en_digest_cluster_line(self):
        p = self._out()
        report.write_digest([self._rep()], p, 7, "en")
        s = open(p, encoding="utf-8").read()
        self.assertIn("- From 2 sources, 3 posts", s)
        self.assertIn("  - [reddit/software](https://x/b) 帖子 b", s)

    def test_plain_rows_render_unchanged(self):
        p1, p2 = self._out(), self._out()
        report.write_markdown([demand("a", "一个工具")], p1, "zh")
        report.write_markdown([{**demand("a", "一个工具"), "cluster_size": 1, "cluster_members": []}], p2, "zh")
        s1, s2 = (open(p, encoding="utf-8").read() for p in (p1, p2))
        self.assertNotIn("来自", s1)
        self.assertEqual(s1.split("\n")[3:], s2.split("\n")[3:])  # 第 3 行是生成时间


class PairEvalTest(unittest.TestCase):
    PAIRS = [
        {"a": "快递追踪", "b": "包裹物流一处看", "same": True, "lang": "zh"},
        {"a": "密码管理器", "b": "自托管密码库", "same": True, "lang": "zh"},
        {"a": "快递追踪", "b": "密码管理器", "same": False, "lang": "zh"},
        {"a": "菜单栏监控", "b": "提醒吃药", "same": False, "lang": "zh"},
    ]

    def test_score_pairs_uses_one_embed_call(self):
        embed = FakeEmbed()
        sims = cluster.score_pairs(self.PAIRS, embed)
        self.assertEqual(len(embed.calls), 1)
        self.assertEqual(len(embed.calls[0]), 8)
        self.assertGreater(sims[0], 0.95)
        self.assertLess(sims[2], 0.1)

    def test_summarize_metrics(self):
        sims = [0.98, 0.99, 0.0, 0.7]
        r = cluster.summarize(self.PAIRS, sims, 0.85)
        self.assertEqual((r["tp"], r["fp"], r["fn"], r["tn"]), (2, 0, 0, 2))
        self.assertEqual((r["accuracy"], r["f1"]), (1.0, 1.0))
        self.assertEqual((r["min_same"], r["max_diff"]), (0.98, 0.7))
        # 阈值设得太高漏掉一对；最佳阈值扫描能找回 100%
        r = cluster.summarize(self.PAIRS, sims, 0.985)
        self.assertEqual((r["tp"], r["fn"], r["recall"]), (1, 1, 0.5))
        self.assertEqual(r["best_accuracy"], 1.0)
        self.assertLessEqual(r["best_threshold"], 0.98)

    def test_evaluate_prints_mistakes_and_per_language(self):
        out: list[str] = []
        path = os.path.join(tempfile.mkdtemp(), "pairs.json")
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.PAIRS + [{"a": "菜单栏监控", "b": "快递追踪", "same": True, "lang": "en"}], f)
        code = cluster.evaluate(path, FakeEmbed(), 0.85, out=out.append)
        text = "\n".join(out)
        self.assertEqual(code, 0)
        self.assertIn("准确率   : 4/5", text)
        self.assertIn("✗ 标注=同", text)
        self.assertIn("zh     : 4/4", text)
        self.assertIn("en     : 0/1", text)

    def test_shipped_pairs_file_is_well_formed(self):
        """eval/cluster_pairs.json：约 20 对、两类都有、每对都标了语言；评测本身要真实嵌入服务，不在这里跑。"""
        path = os.path.join(os.path.dirname(__file__), "..", "eval", "cluster_pairs.json")
        pairs = cluster.load_pairs(path)
        self.assertGreaterEqual(len(pairs), 20)
        same = [p for p in pairs if p["same"]]
        self.assertGreaterEqual(min(len(same), len(pairs) - len(same)), 8)  # 两类都够多，准确率才有意义
        for p in pairs:
            self.assertIn(p["lang"], ("zh", "en"))
            self.assertNotEqual(p["a"].strip(), p["b"].strip())
            self.assertTrue(p["a"].strip() and p["b"].strip())
        self.assertEqual(len({(p["a"], p["b"]) for p in pairs}), len(pairs))  # 不重复

    def test_load_pairs_rejects_missing_fields(self):
        path = os.path.join(tempfile.mkdtemp(), "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('[{"a": "x", "same": true}]')
        with self.assertRaises(ValueError):
            cluster.load_pairs(path)


if __name__ == "__main__":
    unittest.main()
