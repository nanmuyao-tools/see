"""历史回填的离线测试：各源按时间往回翻页的请求、限额处理；进度落库；backfill 命令可中断续跑。"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from harness import FakeEnv, demand, item, mongomock

from demand_radar import sources
from demand_radar.config import Config
from demand_radar.sources import github, hn_algolia, softwarerecs
from demand_radar.sources.http import QuotaExhausted

HERE = os.path.dirname(__file__)


def fixture(name: str) -> bytes:
    with open(os.path.join(HERE, "fixtures", name), "rb") as f:
        return f.read()


class SoftwareRecsPageTest(unittest.TestCase):
    def test_first_page_then_before(self):
        data = json.loads(fixture("softwarerecs.json"))
        with mock.patch.object(softwarerecs, "get_json", return_value=data) as g, \
             mock.patch.object(softwarerecs.time, "sleep") as sleep:
            items = softwarerecs.page_before({}, "softwarerecs", None, 100)
            self.assertNotIn("todate", g.call_args[0][0])
            self.assertEqual(len(items), 2)
            softwarerecs.page_before({}, "softwarerecs", 1000, 100)
            self.assertIn("todate=999", g.call_args[0][0])  # todate 含端点，-1
            self.assertIn("pagesize=100", g.call_args[0][0])
        sleep.assert_not_called()

    def test_backoff_is_honored(self):
        with mock.patch.object(softwarerecs, "get_json", return_value={"items": [], "backoff": 7}), \
             mock.patch.object(softwarerecs.time, "sleep") as sleep:
            softwarerecs.page_before({}, "softwarerecs", None, 100)
        sleep.assert_called_once_with(7)

    def test_quota_reserve_stops_backfill(self):
        low = {"items": [{"question_id": 1, "title": "t"}], "quota_remaining": softwarerecs.QUOTA_RESERVE}
        with mock.patch.object(softwarerecs, "get_json", return_value=low), \
             self.assertRaises(QuotaExhausted):
            softwarerecs.page_before({}, "softwarerecs", None, 100)

    def test_units(self):
        self.assertEqual(softwarerecs.units({}), ["softwarerecs"])
        self.assertEqual(softwarerecs.units({"sites": ["a", "b"]}), ["a", "b"])


class HnPageTest(unittest.TestCase):
    def test_before_filter_and_page_cap(self):
        with mock.patch.object(hn_algolia, "get_json", return_value={"hits": []}) as g:
            hn_algolia.page_before({}, "is there a tool", None, 5000)
            self.assertNotIn("numericFilters", g.call_args[0][0])
            self.assertIn("hitsPerPage=1000", g.call_args[0][0])
            hn_algolia.page_before({}, "is there a tool", 123, 100)
            self.assertIn("numericFilters=created_at_i%3C123", g.call_args[0][0])
        self.assertEqual(hn_algolia.units({"queries": ["a"]}), ["a"])


class GithubPageTest(unittest.TestCase):
    def test_sorted_by_created_with_before(self):
        with mock.patch.object(github, "get_json", return_value={"items": []}) as g, \
             mock.patch.dict(os.environ, {}, clear=True):
            github.page_before({}, "is:issue", None, 100)
            url = g.call_args[0][0]
            self.assertIn("sort=created", url)
            self.assertNotIn("created%3A%3C", url)
            github.page_before({}, "is:issue", 1714566896, 100)
            self.assertIn("q=is%3Aissue+created%3A%3C2024-05-01T12%3A34%3A56Z", g.call_args[0][0])

    def test_interval_depends_on_token(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            slow = github.interval()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            fast = github.interval()
        self.assertGreater(slow, fast)
        self.assertGreaterEqual(slow, 6)  # 不带 token 每分钟 10 次


class RegistryTest(unittest.TestCase):
    def test_rss_not_backfillable(self):
        with self.assertRaises(ValueError):
            sources.backfill_module("rss")
        self.assertIs(sources.backfill_module("hn_algolia"), hn_algolia)

    def test_backfill_args(self):
        cfg = Config.from_args(["backfill", "--source", "github", "--pages", "3"])
        self.assertEqual((cfg.command, cfg.source, cfg.pages, cfg.limit), ("backfill", "github", 3, 100))
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            Config.from_args(["backfill"])  # --source 必填


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -r requirements-dev.txt")
class StoreBackfillTest(unittest.TestCase):
    def test_roundtrip_and_defaults(self):
        from demand_radar import store
        coll = mongomock.MongoClient().db.demands
        self.assertEqual(store.load_backfill(coll, "github", "q"), {"oldest": 0, "pages": 0, "done": False})
        store.save_backfill(coll, "github", "q", {"oldest": 500, "pages": 2, "done": False})
        self.assertEqual(store.load_backfill(coll, "github", "q"), {"oldest": 500, "pages": 2, "done": False})
        self.assertEqual(store.load_cursors(coll), {})  # 和增量游标互不干扰


def fake_source(history: dict[str, list], calls: list):
    """模拟一个按时间倒序分页的源：每个查询有一串历史内容，page_before 给 before 之前最新的几条。"""
    class Mod:
        @staticmethod
        def units(conf):
            return list(history)

        @staticmethod
        def interval():
            return 0

        @staticmethod
        def page_before(conf, unit, before, pagesize):
            calls.append((unit, before))
            pool = [it for it in history[unit] if before is None or it.created_at < before]
            return sorted(pool, key=lambda i: -i.created_at)[:pagesize]

    return Mod


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -r requirements-dev.txt")
class BackfillCommandTest(unittest.TestCase):
    def setUp(self):
        from demand_radar import store
        self.store = store
        # 查询 a 有 5 条、b 有 2 条，发布时间 1000、2000…
        self.history = {
            "a": [item(f"a{i}", created_at=i * 1000) for i in range(1, 6)],
            "b": [item(f"b{i}", created_at=i * 1000) for i in range(1, 3)],
        }
        self.calls: list = []

    def _env(self, pages, limit=2):
        return FakeEnv(command="backfill", pages=pages, limit=limit)

    def test_round_robin_within_budget_and_progress_saved(self):
        env = self._env(pages=3)
        env.backfill(fake_source(self.history, self.calls))
        # 轮流：a 第 1 页、b 第 1 页、a 第 2 页
        self.assertEqual(self.calls, [("a", None), ("b", None), ("a", 4000)])
        self.assertEqual(sorted(env.calls), ["a2", "a3", "a4", "a5", "b1", "b2"])
        self.assertEqual(self.store.load_backfill(env.coll, "softwarerecs", "a"),
                         {"oldest": 2000, "pages": 2, "done": False})
        self.assertEqual(self.store.load_backfill(env.coll, "softwarerecs", "b"),
                         {"oldest": 1000, "pages": 1, "done": False})
        (run,) = env.runs()
        self.assertEqual((run["command"], run["status"], run["pages"], run["extracted"]),
                         ("backfill", "done", 3, 6))

    def test_resume_continues_from_oldest_and_marks_done(self):
        env = self._env(pages=3)
        env.backfill(fake_source(self.history, self.calls))
        self.calls.clear()
        env2 = FakeEnv(command="backfill", pages=10, limit=2)
        env2.coll = env.coll   # 同一个库，模拟第二次运行
        env2.backfill(fake_source(self.history, self.calls))
        # a 从 2000 之前继续拿到 a1，再翻一页是空 → 到头；b 从 1000 之前是空 → 到头
        self.assertEqual(self.calls, [("a", 2000), ("b", 1000), ("a", 1000)])
        self.assertEqual(env2.calls, ["a1"])
        self.assertTrue(self.store.load_backfill(env.coll, "softwarerecs", "a")["done"])
        self.assertTrue(self.store.load_backfill(env.coll, "softwarerecs", "b")["done"])
        self.calls.clear()
        out = env2.backfill(fake_source(self.history, self.calls))  # 第三次：没事可做
        self.assertEqual(self.calls, [])
        self.assertIn("已回填到头", out)

    def test_already_stored_items_not_extracted_again(self):
        env = self._env(pages=1)
        self.store.save(env.coll, self.history["a"][4], demand())  # a5 已在库里
        env.backfill(fake_source(self.history, self.calls))
        self.assertEqual(env.calls, ["a4"])
        self.assertEqual(env.coll.count_documents({}), 2)

    def test_quota_exhausted_stops_but_keeps_progress(self):
        env = self._env(pages=5)
        mod = fake_source(self.history, self.calls)
        real = mod.page_before

        def flaky(conf, unit, before, pagesize):
            if len(self.calls) >= 1:
                raise QuotaExhausted("配额用完")
            return real(conf, unit, before, pagesize)

        mod.page_before = staticmethod(flaky)
        out = env.backfill(mod)
        self.assertEqual(self.store.load_backfill(env.coll, "softwarerecs", "a")["pages"], 1)
        self.assertIn("配额用完", out)
        (run,) = env.runs()
        self.assertEqual(run["status"], "quota")

    def test_transient_error_consumes_budget_and_moves_on(self):
        env = self._env(pages=2)
        mod = fake_source(self.history, self.calls)
        real = mod.page_before

        def flaky(conf, unit, before, pagesize):
            if unit == "a":
                self.calls.append((unit, before))
                raise OSError("down")
            return real(conf, unit, before, pagesize)

        mod.page_before = staticmethod(flaky)
        env.backfill(mod)   # a 失败、b 成功，预算用完，不会死循环
        self.assertEqual(self.calls, [("a", None), ("b", None)])
        self.assertEqual(sorted(env.calls), ["b1", "b2"])
        self.assertEqual(self.store.load_backfill(env.coll, "softwarerecs", "a")["pages"], 0)

    def test_unsupported_source_exits(self):
        env = FakeEnv(command="backfill", source="rss")
        with env.patched(), self.assertRaises(SystemExit):
            from demand_radar import cli
            cli.backfill(env.cfg)


if __name__ == "__main__":
    unittest.main()
