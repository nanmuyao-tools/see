"""并发抽取的离线测试：线程池确实并发、异常逐条返回、写库仍在主线程；--workers 参数。"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from demand_radar import extract as extract_mod
from demand_radar.config import Config
from harness import FakeEnv, demand, item, mongomock


class ExtractManyTest(unittest.TestCase):
    def _concurrency_probe(self, delay=0.05):
        """记录同时在跑的调用数峰值。"""
        state = {"running": 0, "peak": 0}
        lock = threading.Lock()

        def fake(client, it, lang):
            with lock:
                state["running"] += 1
                state["peak"] = max(state["peak"], state["running"])
            time.sleep(delay)
            with lock:
                state["running"] -= 1
            return demand(need=it.external_id)

        return fake, state

    def test_runs_in_parallel_with_workers(self):
        fake, state = self._concurrency_probe()
        items = [item(str(i)) for i in range(4)]
        with mock.patch.object(extract_mod, "extract", side_effect=fake):
            results = list(extract_mod.extract_many(None, items, "zh", workers=4))
        self.assertEqual(state["peak"], 4)
        self.assertEqual(sorted(r[1].need for r in results), ["0", "1", "2", "3"])
        self.assertTrue(all(r[2] is None for r in results))

    def test_single_worker_is_sequential(self):
        fake, state = self._concurrency_probe(delay=0.01)
        with mock.patch.object(extract_mod, "extract", side_effect=fake):
            list(extract_mod.extract_many(None, [item(str(i)) for i in range(3)], "zh", workers=1))
        self.assertEqual(state["peak"], 1)

    def test_exception_returned_per_item_not_raised(self):
        def flaky(client, it, lang):
            if it.external_id == "bad":
                raise RuntimeError("model down")
            return demand()

        with mock.patch.object(extract_mod, "extract", side_effect=flaky):
            results = {r[0].external_id: r for r in extract_mod.extract_many(None, [item("ok"), item("bad")])}
        self.assertIsNone(results["ok"][2])
        self.assertIsNone(results["bad"][1])
        self.assertIsInstance(results["bad"][2], RuntimeError)

    def test_empty_input(self):
        self.assertEqual(list(extract_mod.extract_many(None, [], "zh", 2)), [])


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -r requirements-dev.txt")
class RunWithWorkersTest(unittest.TestCase):
    def test_all_items_stored_from_main_thread(self):
        env = FakeEnv(workers=3)
        main = threading.get_ident()
        writer_threads = set()
        real_save = env.coll.replace_one

        def spy(*a, **kw):
            writer_threads.add(threading.get_ident())
            return real_save(*a, **kw)

        def extract(it):
            if it.external_id == "bad":
                raise RuntimeError("boom")
            return demand()

        with mock.patch.object(env.coll, "replace_one", side_effect=spy):
            env.run([item(str(i)) for i in range(5)] + [item("bad")], extract=extract)
        self.assertEqual(writer_threads, {main})
        self.assertEqual(env.coll.count_documents({"status": "extracted"}), 5)
        self.assertEqual(env.coll.count_documents({"status": "retry"}), 1)
        (run,) = env.runs()
        self.assertEqual((run["extracted"], run["failed"]), (5, 1))


class WorkersConfigTest(unittest.TestCase):
    def test_flag_and_env(self):
        self.assertEqual(Config.from_args(["run"]).workers, 2)
        self.assertEqual(Config.from_args(["run", "--workers", "4"]).workers, 4)
        with mock.patch.dict("os.environ", {"DR_WORKERS": "3"}):
            self.assertEqual(Config.from_args(["run"]).workers, 3)
            self.assertEqual(Config.from_args(["run", "--workers", "1"]).workers, 1)


if __name__ == "__main__":
    unittest.main()
