"""看板接口的离线测试：mongomock 当库，TestClient 直接调应用，不起服务、不碰网络。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from demand_radar import rank, store

try:
    import mongomock
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    mongomock = TestClient = None

NOW = datetime.now(timezone.utc)


def doc(id_, need="一个工具", domain="效率", source="hackernews", days_ago=1, clarity=3, intensity=3,
        feasibility=3, score=0, confidence=0.9, review_cue="", title="Is there a tool", **extra):
    return {"_id": id_, "need": need, "domain": domain, "pain_point": "现有的不好用", "excerpt": "原话",
            "clarity": clarity, "intensity": intensity, "feasibility": feasibility, "score": score,
            "confidence": confidence, "is_request": True, "buildable": True, "review_cue": review_cue,
            "source": source, "url": f"https://x/{id_}", "title": title, "status": "extracted",
            "kind": "post", "extracted_at": NOW - timedelta(days=days_ago), **extra}


@unittest.skipIf(mongomock is None or TestClient is None,
                 "需要 mongomock、fastapi、httpx：pip install -r requirements-dev.txt")
class ApiTest(unittest.TestCase):
    def setUp(self):
        from demand_radar.api import create_app
        self.coll = mongomock.MongoClient().db.demands
        self.client = TestClient(create_app(self.coll, {"heat": 1.0}, 0.5))

    def get(self, path, **params):
        r = self.client.get(path, params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def ids(self, path, **params):
        return [it["id"] for it in self.get(path, **params)["items"]]

    # ---- 空库 ----

    def test_empty_db(self):
        self.assertEqual(self.get("/api/demands"), {"total": 0, "offset": 0, "limit": 50, "items": []})
        self.assertEqual(self.get("/api/held")["items"], [])
        self.assertEqual(self.get("/api/domains"), [])
        self.assertEqual(self.get("/api/sources"), [])
        self.assertEqual(self.get("/api/runs"), [])
        s = self.get("/api/stats")
        self.assertEqual((s["total"], s["listed"], s["held"], s["last_run"]), (0, 0, 0, None))

    # ---- 条目内容 ----

    def test_item_fields_match_report(self):
        self.coll.insert_one(doc("a", score=10))
        (it,) = self.get("/api/demands")["items"]
        for k in ("need", "domain", "pain_point", "excerpt", "clarity", "intensity", "feasibility",
                  "rank_score", "score", "source", "url", "title", "extracted_at", "confidence"):
            self.assertIn(k, it)
        self.assertNotIn("_id", it)
        self.assertEqual(it["id"], "a")
        self.assertEqual(it["rank_score"], round(rank.score(doc("a", score=10)), 2))
        self.assertTrue(it["extracted_at"].endswith("+00:00"), it["extracted_at"])  # 带时区的 ISO，前端换算本地时间

    def test_legacy_doc_without_scores_or_cue(self):
        legacy = {"_id": "old", "need": "旧数据", "is_request": True, "buildable": True, "confidence": 0.9,
                  "source": "s", "url": "u", "title": "t", "score": 3}
        self.coll.insert_one(legacy)  # 没有分项打分、review_cue、extracted_at
        (it,) = self.get("/api/demands")["items"]
        self.assertGreater(it["rank_score"], 0)
        self.assertIsNone(it["extracted_at"])
        self.assertEqual(self.ids("/api/demands", days=7), [])  # 没有抽取时间的不算「最近」

    # ---- 筛选 ----

    def test_min_confidence_default_and_override(self):
        self.coll.insert_many([doc("lo", confidence=0.4), doc("hi", confidence=0.6)])
        self.assertEqual(self.ids("/api/demands"), ["hi"])  # 默认 0.5 来自 create_app
        self.assertEqual(sorted(self.ids("/api/demands", min_confidence=0.3)), ["hi", "lo"])
        self.assertEqual(self.get("/api/stats", min_confidence=0.3)["listed"], 2)
        self.assertEqual(self.client.get("/api/demands", params={"min_confidence": 2}).status_code, 422)

    def test_filter_domain_source_q(self):
        self.coll.insert_many([
            doc("a", need="截图工具", domain="截图", source="github", title="Need a Screenshot app"),
            doc("b", need="待办", domain="效率", source="hackernews", title="todo"),
            doc("c", need="c++ 编译器", domain="开发工具", source="hackernews", title="compiler"),
        ])
        self.assertEqual(self.ids("/api/demands", domain="截图"), ["a"])
        self.assertEqual(sorted(self.ids("/api/demands", source="hackernews")), ["b", "c"])
        self.assertEqual(self.ids("/api/demands", q="screenshot"), ["a"])  # 标题里、不分大小写
        self.assertEqual(self.ids("/api/demands", q="截图"), ["a"])        # 需求描述里
        self.assertEqual(self.ids("/api/demands", q="c++"), ["c"])        # 正则特殊字符按字面匹配
        self.assertEqual(self.ids("/api/demands", q="   "), ["a", "b", "c"])  # 空白等于不筛
        self.assertEqual(self.ids("/api/demands", domain="截图", source="hackernews"), [])

    def test_filter_days_by_extracted_at(self):
        self.coll.insert_many([doc("new", days_ago=1), doc("old", days_ago=30)])
        self.assertEqual(self.ids("/api/demands", days=7), ["new"])
        self.assertEqual(sorted(self.ids("/api/demands")), ["new", "old"])
        self.assertEqual(self.client.get("/api/demands", params={"days": 0}).status_code, 422)

    def test_excludes_not_request_not_buildable_filtered(self):
        self.coll.insert_many([
            doc("ok"),
            doc("vent", is_request=False),
            doc("hard", buildable=False),
            {"_id": "junk", "status": "filtered", "filter_reason": "empty_body", "source": "s"},
        ])
        self.assertEqual(self.ids("/api/demands"), ["ok"])
        self.assertEqual(self.get("/api/stats")["total"], 4)  # 库里总条数照实报

    # ---- 排序与分页 ----

    def _three(self):
        self.coll.insert_many([
            doc("clear", clarity=5, intensity=5, feasibility=5, score=0, days_ago=3),
            doc("hot", clarity=1, intensity=1, feasibility=1, score=1000, days_ago=2),
            doc("mid", clarity=3, intensity=3, feasibility=3, score=10, days_ago=1),
        ])

    def test_sort_rank_is_default(self):
        self._three()
        self.assertEqual(self.ids("/api/demands"), ["clear", "mid", "hot"])
        self.assertEqual(self.ids("/api/demands", sort="rank"), ["clear", "mid", "hot"])

    def test_sort_heat_and_time_keep_rank_score(self):
        self._three()
        self.assertEqual(self.ids("/api/demands", sort="heat"), ["hot", "mid", "clear"])
        self.assertEqual(self.ids("/api/demands", sort="time"), ["mid", "hot", "clear"])
        for it in self.get("/api/demands", sort="heat")["items"]:
            self.assertIn("rank_score", it)  # 不管按什么排，排序分都要给前端展示
        self.assertEqual(self.client.get("/api/demands", params={"sort": "random"}).status_code, 422)

    def test_pagination(self):
        self.coll.insert_many([doc(str(i), clarity=5 - i) for i in range(5)])  # 排序分递减：0,1,2,3,4
        page = self.get("/api/demands", limit=2, offset=0)
        self.assertEqual((page["total"], [it["id"] for it in page["items"]]), (5, ["0", "1"]))
        page = self.get("/api/demands", limit=2, offset=4)
        self.assertEqual((page["total"], [it["id"] for it in page["items"]]), (5, ["4"]))
        self.assertEqual(self.get("/api/demands", limit=2, offset=10)["items"], [])
        self.assertEqual(self.client.get("/api/demands", params={"limit": 0}).status_code, 422)

    # ---- 待人工确认 ----

    def test_held_separated_from_listed(self):
        self.coll.insert_many([
            doc("clean"),
            doc("cued", review_cue="makes public", domain="监控"),
            doc("cued_low", review_cue="track", confidence=0.2),
            doc("cued_not_buildable", review_cue="track", buildable=False),
        ])
        self.assertEqual(self.ids("/api/demands"), ["clean"])
        held = self.get("/api/held")
        self.assertEqual([it["id"] for it in held["items"]], ["cued"])
        self.assertEqual(held["items"][0]["review_cue"], "makes public")
        self.assertEqual(self.ids("/api/held", domain="效率"), [])  # 同样的筛选参数
        self.assertEqual(self.ids("/api/held", min_confidence=0.1), ["cued", "cued_low"])

    # ---- 领域 / 来源 / 运行记录 / 统计 ----

    def test_domains_sorted_by_count_and_honor_filters(self):
        self.coll.insert_many([doc("a", domain="效率"), doc("b", domain="效率"), doc("c", domain="截图"),
                               doc("d", domain="", days_ago=30), doc("h", domain="效率", review_cue="x")])
        got = self.get("/api/domains")
        self.assertEqual(got[0], {"domain": "效率", "count": 2})  # 待确认的不算
        self.assertEqual({d["domain"]: d["count"] for d in got}, {"效率": 2, "截图": 1, "": 1})
        self.assertEqual({d["domain"] for d in self.get("/api/domains", days=7)}, {"效率", "截图"})
        self.assertEqual(self.get("/api/domains", held="true"), [{"domain": "效率", "count": 1}])

    def test_sources_counts(self):
        self.coll.insert_many([doc("a", source="github"), doc("b", source="github"), doc("c", source="reddit/x")])
        self.assertEqual(self.get("/api/sources"),
                         [{"source": "github", "count": 2}, {"source": "reddit/x", "count": 1}])

    def test_runs_newest_first_with_counts(self):
        first = store.start_run(self.coll, "run", sources=["github"], limit=5)
        store.finish_run(self.coll, first, fetched_total=4, filtered=1, extracted=2, failed=1, listed=2, held=0)
        second = store.start_run(self.coll, "backfill", sources=["hn_algolia"])
        runs = self.get("/api/runs")
        self.assertEqual([r["command"] for r in runs], ["backfill", "run"])
        self.assertEqual(runs[1]["id"], str(first))
        self.assertEqual((runs[1]["status"], runs[1]["extracted"], runs[1]["failed"]), ("done", 2, 1))
        self.assertIsInstance(runs[1]["duration_s"], float)
        self.assertTrue(runs[1]["started_at"].endswith("+00:00"))
        self.assertEqual(runs[0]["status"], "running")  # 没结束的也能看见，排查进程中途死掉时有用
        self.assertNotIn("finished_at", runs[0])
        self.assertEqual(len(self.get("/api/runs", limit=1)), 1)
        self.assertEqual(self.get("/api/stats")["last_run"]["id"], str(second))

    def test_stats_counts(self):
        self.coll.insert_many([doc("a"), doc("b", review_cue="x"), doc("c", confidence=0.1),
                               {"_id": "f", "status": "filtered", "source": "s"},
                               {"_id": "r", "status": "retry", "attempts": 1, "source": "s"}])
        s = self.get("/api/stats")
        self.assertEqual((s["total"], s["listed"], s["held"]), (5, 1, 1))
        self.assertEqual(s["by_status"], {"extracted": 3, "filtered": 1, "retry": 1, "failed": 0})
        self.assertEqual(s["min_confidence"], 0.5)


if __name__ == "__main__":
    unittest.main()
