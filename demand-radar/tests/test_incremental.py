"""增量抓取的离线测试：各源怎么用游标拼请求、怎么推进；游标与失败重试的落库；run 命令的编排。"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from harness import demand, item, mongomock

from demand_radar import sources
from demand_radar.sources import cursor, github, hn_algolia, rss, softwarerecs

HERE = os.path.dirname(__file__)


def fixture(name: str) -> bytes:
    with open(os.path.join(HERE, "fixtures", name), "rb") as f:
        return f.read()


class CursorHelpersTest(unittest.TestCase):
    def test_advance_takes_newest_and_never_goes_back(self):
        cur = {"q": 50}
        cursor.advance(cur, "q", [item("a", 10), item("b", 99), item("c", 0)])
        self.assertEqual(cur["q"], 99)
        cursor.advance(cur, "q", [item("d", 20)])      # 旧内容不能把游标拉回去
        self.assertEqual(cur["q"], 99)
        cursor.advance(cur, "q", [])                    # 空批次不动
        self.assertEqual(cur["q"], 99)

    def test_advance_ignores_none_and_unknown_times(self):
        cursor.advance(None, "q", [item("a", 10)])      # 不记游标的模式
        cur = {}
        cursor.advance(cur, "q", [item("a", 0)])        # 源没给时间 → 不建游标
        self.assertEqual(cur, {})

    def test_epoch_parses_each_source_format(self):
        self.assertEqual(cursor.epoch("2024-05-01T12:34:56Z"), 1714566896)             # GitHub
        self.assertEqual(cursor.epoch("2026-10-18T13:27:47+00:00"), 1792330067)        # Atom
        self.assertEqual(cursor.epoch("Sun, 18 Oct 2026 08:51:45 +0000"), 1792313505)  # RSS 2.0
        self.assertEqual(cursor.epoch(None), 0)
        self.assertEqual(cursor.epoch("yesterday"), 0)

    def test_github_time_roundtrip(self):
        self.assertEqual(cursor.github_time(1714566896), "2024-05-01T12:34:56Z")


class SoftwareRecsCursorTest(unittest.TestCase):
    def test_first_run_has_no_fromdate_and_sets_cursor(self):
        cur = {}
        with mock.patch.object(softwarerecs, "get_json",
                               return_value=json.loads(fixture("softwarerecs.json"))) as g:
            items = softwarerecs.fetch(10, {"sites": ["softwarerecs"]}, cur)
        self.assertNotIn("fromdate", g.call_args[0][0])
        self.assertEqual(cur, {"softwarerecs": max(i.created_at for i in items)})
        self.assertEqual(cur["softwarerecs"], 1791985571)

    def test_second_run_filters_after_cursor(self):
        with mock.patch.object(softwarerecs, "get_json", return_value={"items": []}) as g:
            softwarerecs.fetch(10, {}, {"softwarerecs": 1000})
        self.assertIn("fromdate=1001", g.call_args[0][0])  # fromdate 含端点，+1 跳过已见的

    def test_without_cursors_behaves_as_before(self):
        with mock.patch.object(softwarerecs, "get_json", return_value={"items": []}) as g:
            softwarerecs.fetch(10, {})
        self.assertNotIn("fromdate", g.call_args[0][0])


class HnCursorTest(unittest.TestCase):
    def test_numeric_filter_and_advance(self):
        data = json.loads(fixture("hn_algolia.json"))
        cur = {"is there a tool": 5}
        with mock.patch.object(hn_algolia, "get_json", return_value=data) as g:
            hn_algolia.fetch(5, {"queries": ["is there a tool"]}, cur)
        self.assertIn("numericFilters=created_at_i%3E5", g.call_args[0][0])
        self.assertEqual(cur["is there a tool"], 1790969773)

    def test_each_query_has_its_own_cursor(self):
        cur = {"a": 7}
        with mock.patch.object(hn_algolia, "get_json", return_value={"hits": []}) as g:
            hn_algolia.fetch(5, {"queries": ["a", "b"]}, cur)
        urls = [c[0][0] for c in g.call_args_list]
        self.assertIn("created_at_i%3E7", urls[0])
        self.assertNotIn("numericFilters", urls[1])


class GithubCursorTest(unittest.TestCase):
    def test_cursor_ignored_always_top_by_reactions(self):
        """GitHub 是「当前最热」榜单而非时间流：有游标也不加 created:>，也不推进游标。"""
        data = json.loads(fixture("github_issues.json"))
        q = "is:issue reactions:>50"
        cur = {q: 1714566896}
        with mock.patch.object(github, "get_json", return_value=data) as g, \
             mock.patch.dict(os.environ, {}, clear=True):
            items = github.fetch(5, {"queries": [q]}, cur)
        url = g.call_args[0][0]
        self.assertNotIn("created", url)
        self.assertIn("sort=reactions", url)
        self.assertEqual(items[0].created_at, 1714566896)  # 发布时间仍解析，回填要用
        self.assertEqual(cur, {q: 1714566896})  # 游标原样不动


class RssCreatedAtTest(unittest.TestCase):
    def test_atom_and_rss2_times_parsed(self):
        atom = rss.parse("r", fixture("reddit.atom"), 10)
        self.assertTrue(all(i.created_at > 0 for i in atom))
        rss2 = rss.parse("a", fixture("appinn.rss"), 10)
        self.assertEqual(rss2[0].created_at, 1792313505)

    def test_cursors_are_ignored(self):
        cur = {}
        with mock.patch.object(rss, "get", return_value=fixture("reddit.atom")):
            rss.fetch(10, [{"name": "r", "url": "u"}], cur)
        self.assertEqual(cur, {})


class FetchAllCursorTest(unittest.TestCase):
    def test_per_source_dict_passed_and_advanced(self):
        seen = {}

        def fake(limit, conf, cursors):
            seen["got"] = cursors
            cursors["q"] = 42
            return [item("1")]

        cur = {"hn_algolia": {"q": 1}}
        with mock.patch.dict(sources.REGISTRY, {"hn_algolia": fake}):
            sources.fetch_all(["hn_algolia"], 5, {}, cur)
        self.assertIs(seen["got"], cur["hn_algolia"])
        self.assertEqual(cur, {"hn_algolia": {"q": 42}})

    def test_none_means_no_cursors(self):
        got = []
        with mock.patch.dict(sources.REGISTRY, {"github": lambda _limit, c, cur: got.append(cur) or []}):
            sources.fetch_all(["github"], 5, {})
        self.assertEqual(got, [None])

    def test_failed_source_keeps_its_cursor(self):
        def boom(limit, conf, cursors):
            raise OSError("down")

        cur = {"github": {"q": 9}}
        with mock.patch.dict(sources.REGISTRY, {"github": boom}):
            _, errors = sources.fetch_all(["github"], 5, {}, cur)
        self.assertIn("github", errors)
        self.assertEqual(cur["github"], {"q": 9})


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -r requirements-dev.txt")
class StoreCursorTest(unittest.TestCase):
    def setUp(self):
        from demand_radar import store
        self.store = store
        self.coll = mongomock.MongoClient().db.demands

    def test_roundtrip(self):
        self.assertEqual(self.store.load_cursors(self.coll), {})
        self.store.save_cursors(self.coll, {"hn_algolia": {"a": 1, "b": 2}, "rss": {}})
        self.assertEqual(self.store.load_cursors(self.coll), {"hn_algolia": {"a": 1, "b": 2}})
        self.store.save_cursors(self.coll, {"hn_algolia": {"a": 5}})
        self.assertEqual(self.store.load_cursors(self.coll)["hn_algolia"], {"a": 5, "b": 2})

    def test_created_at_stored_as_datetime(self):
        self.store.save(self.coll, item("1", created_at=1714566896), demand())
        doc = self.coll.find_one()
        self.assertEqual(doc["created_at"].year, 2024)
        self.store.save(self.coll, item("2"), demand())
        self.assertNotIn("created_at", self.coll.find_one({"external_id": "2"}))

    def test_retry_items_rebuilt_from_db(self):
        it = item("1", created_at=1714566896, text="original body")
        self.store.record_failure(self.coll, it, "boom")
        got = self.store.retry_items(self.coll)
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0].external_id, got[0].text, got[0].created_at),
                         ("1", "original body", 1714566896))

    def test_retry_items_skips_failed_and_legacy_docs(self):
        it = item("1")
        for _ in range(self.store.MAX_ATTEMPTS):
            self.store.record_failure(self.coll, it, "boom")   # 到上限 → failed
        self.coll.insert_one({"_id": "s:legacy", "source": "s", "external_id": "legacy",
                              "status": "retry", "attempts": 1})  # 旧记录没存正文
        self.assertEqual(self.store.retry_items(self.coll), [])


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -r requirements-dev.txt")
class RunCommandTest(unittest.TestCase):
    """run 的编排：游标在整批处理完后落库；没有新内容不算错；失败条目从库里重试。"""

    def setUp(self):
        from harness import FakeEnv
        self.env = FakeEnv()

    def test_cursor_saved_after_processing(self):
        from demand_radar import store
        self.env.run([item("1"), item("2")])
        self.assertEqual(sorted(self.env.calls), ["1", "2"])
        self.assertEqual(store.load_cursors(self.env.coll), {"softwarerecs": {"softwarerecs": 42}})
        self.assertTrue(os.path.exists(self.env.out))

    def test_no_new_items_is_not_an_error(self):
        out = self.env.run([])
        self.assertEqual(self.env.calls, [])
        self.assertIn("没有新内容", out)
        self.assertTrue(os.path.exists(self.env.out))  # 清单照常从库里重新生成

    def test_all_sources_failing_exits(self):
        with self.assertRaises(SystemExit):
            self.env.run([], errors={"softwarerecs": "down"})

    def test_failed_item_retried_from_db_without_refetch(self):
        from demand_radar import store

        def flaky(it):
            raise RuntimeError("model down")

        self.env.run([item("1")], extract=flaky)
        self.assertEqual(self.env.coll.find_one()["status"], "retry")
        self.env.calls.clear()
        self.env.run([])  # 第二次没抓到它，仍从库里重试
        self.assertEqual(self.env.calls, ["1"])
        self.assertEqual(self.env.coll.find_one()["status"], "extracted")
        self.assertEqual(store.retry_items(self.env.coll), [])


if __name__ == "__main__":
    unittest.main()
