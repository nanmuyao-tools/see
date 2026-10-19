"""运行记录（runs 集合）的离线测试。"""

from __future__ import annotations

import unittest

from harness import FakeEnv, item, mongomock


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -r requirements-dev.txt")
class StoreRunTest(unittest.TestCase):
    def setUp(self):
        from demand_radar import store
        self.store = store
        self.coll = mongomock.MongoClient().db.demands

    def test_start_then_finish(self):
        run_id = self.store.start_run(self.coll, "run", sources=["github"], limit=5)
        doc = self.coll.database["runs"].find_one({"_id": run_id})
        self.assertEqual((doc["status"], doc["command"], doc["sources"]), ("running", "run", ["github"]))
        self.assertNotIn("finished_at", doc)  # 中途死掉就停在这个样子，排查时能看出来

        took = self.store.finish_run(self.coll, run_id, extracted=3, failed=1)
        doc = self.coll.database["runs"].find_one({"_id": run_id})
        self.assertEqual(doc["status"], "done")
        self.assertEqual((doc["extracted"], doc["failed"]), (3, 1))
        self.assertGreaterEqual(doc["duration_s"], 0)
        self.assertEqual(took, doc["duration_s"])

    def test_finish_with_failed_status(self):
        run_id = self.store.start_run(self.coll, "run")
        self.store.finish_run(self.coll, run_id, status="failed", fetch_errors={"github": "down"})
        doc = self.coll.database["runs"].find_one({"_id": run_id})
        self.assertEqual((doc["status"], doc["fetch_errors"]), ("failed", {"github": "down"}))

    def test_source_counts_sorted_and_dot_safe(self):
        items = [item("1", source="a.com"), item("2", source="hn"), item("3", source="a.com")]
        self.assertEqual(self.store.source_counts(items),
                         [{"source": "a.com", "count": 2}, {"source": "hn", "count": 1}])


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -r requirements-dev.txt")
class RunRecordsTest(unittest.TestCase):
    def test_run_writes_one_record_with_counts(self):
        env = FakeEnv()

        def extract(it):
            if it.external_id == "bad":
                raise RuntimeError("model down")
            from harness import demand
            return demand()

        env.run([item("1", source="hn"), item("2", source="hn"), item("bad", source="gh"),
                 item("x", source="hn", text="", title="Help for my project")],  # 空正文且标题无求助词 → 规则过滤
                extract=extract)
        (doc,) = env.runs()
        self.assertEqual(doc["status"], "done")
        self.assertEqual(doc["fetched_total"], 4)
        self.assertEqual(doc["fetched"], [{"source": "hn", "count": 3}, {"source": "gh", "count": 1}])
        self.assertEqual((doc["todo"], doc["filtered"], doc["extracted"], doc["failed"]), (4, 1, 2, 1))
        self.assertEqual(doc["listed"], 2)
        self.assertEqual(doc["sources"], ["softwarerecs"])

    def test_all_sources_failing_recorded_as_failed(self):
        env = FakeEnv()
        with self.assertRaises(SystemExit):
            env.run([], errors={"softwarerecs": "down"})
        (doc,) = env.runs()
        self.assertEqual(doc["status"], "failed")
        self.assertEqual(doc["fetch_errors"], {"softwarerecs": "down"})


if __name__ == "__main__":
    unittest.main()
