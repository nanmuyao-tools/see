"""Arctic Shift（Reddit）数据源的离线测试：解析、本地关键词过滤、增量游标、回填翻页、限流与超时重试、注册。

fixture 取自 2026-10-19 的真实响应裁剪（转帖、普通自发帖、被删帖各一条）。
"""

from __future__ import annotations

import email.message
import json
import os
import unittest
import urllib.error
from unittest import mock

from demand_radar import sources
from demand_radar.models import SourceItem
from demand_radar.sources import arctic_shift

HERE = os.path.dirname(__file__)


def fixture() -> dict:
    with open(os.path.join(HERE, "fixtures", "arctic_shift.json"), "rb") as f:
        return json.load(f)


def http_error(code: int, reset: str | None = None) -> urllib.error.HTTPError:
    h = email.message.Message()
    if reset:
        h["X-RateLimit-Reset"] = reset
    return urllib.error.HTTPError("u", code, "x", h, None)


class ParseTest(unittest.TestCase):
    def test_ids_and_source_match_rss(self):
        _, post, _ = arctic_shift.parse(fixture())
        self.assertEqual(post.source, "reddit/SomebodyMakeThis")
        self.assertEqual(post.external_id, "t3_1x4195m")  # 和 RSS 的 <id> 一样，两边入库去重
        self.assertEqual(post.url, "https://www.reddit.com/r/SomebodyMakeThis/comments/1x4195m/")
        self.assertEqual((post.score, post.created_at, post.kind), (0, 1791810092, "post"))
        self.assertTrue(post.title.startswith("Building a social app"))
        self.assertIn("Trofex", post.text)

    def test_crosspost_and_removed_have_empty_text(self):
        crosspost, _, removed = arctic_shift.parse(fixture())
        self.assertEqual(crosspost.text, "")     # 父帖内容拿不到
        self.assertEqual(crosspost.score, 1)
        self.assertEqual(removed.text, "")       # "[removed]" 占位不当正文
        self.assertTrue(removed.title)

    def test_nsfw_skipped(self):
        row = {**fixture()["data"][1], "over_18": True}
        self.assertEqual(arctic_shift.parse({"data": [row]}), [])

    def test_empty_or_null_data(self):
        self.assertEqual(arctic_shift.parse({"data": None}), [])
        self.assertEqual(arctic_shift.parse({}), [])


class KeywordTest(unittest.TestCase):
    def _item(self, title: str, text: str = "") -> SourceItem:
        return SourceItem("reddit/x", "t3_1", title, text, "u")

    def test_no_keywords_keeps_all(self):
        self.assertTrue(arctic_shift.matches(self._item("anything"), None))
        self.assertTrue(arctic_shift.matches(self._item("anything"), []))

    def test_case_insensitive_title_or_body(self):
        self.assertTrue(arctic_shift.matches(self._item("Is There An App for this?"), ["is there an app"]))
        self.assertTrue(arctic_shift.matches(self._item("help", "I'm LOOKING FOR a tool"), ["looking for"]))
        self.assertFalse(arctic_shift.matches(self._item("Vinted for charity shops"), ["is there an app", "looking for"]))


class FetchCursorTest(unittest.TestCase):
    def test_first_run_no_after_and_sets_cursor(self):
        cur: dict = {}
        with mock.patch.object(arctic_shift, "get_json", return_value=fixture()) as g, \
             mock.patch.object(arctic_shift.time, "sleep"):
            items = arctic_shift.fetch(10, {"subreddits": ["SomebodyMakeThis"]}, cur)
        url = g.call_args[0][0]
        self.assertIn("subreddit=SomebodyMakeThis", url)
        self.assertIn("limit=10", url)
        self.assertIn("sort=desc", url)
        self.assertIn("fields=id%2C", url)          # 裁字段，响应小、不易超时
        self.assertNotIn("after=", url)
        self.assertEqual(len(items), 3)
        self.assertEqual(cur, {"SomebodyMakeThis": 1791810520})

    def test_second_run_uses_after_per_subreddit(self):
        cur = {"macapps": 1000}
        with mock.patch.object(arctic_shift, "get_json", return_value={"data": []}) as g, \
             mock.patch.object(arctic_shift.time, "sleep") as sleep:
            arctic_shift.fetch(10, {"subreddits": ["macapps", "software"]}, cur)
        urls = [c[0][0] for c in g.call_args_list]
        self.assertIn("after=1000", urls[0])     # after 不含端点，不用 +1
        self.assertNotIn("after=", urls[1])
        sleep.assert_not_called()  # 版块之间的间隔交给 http 的统一限速，源里不再自己睡
        self.assertEqual(cur, {"macapps": 1000})  # 空批次游标不动

    def test_keywords_filter_items_but_cursor_sees_all(self):
        conf = {"subreddits": ["SomebodyMakeThis"], "keywords": {"SomebodyMakeThis": ["looking to learn"]}}
        cur: dict = {}
        with mock.patch.object(arctic_shift, "get_json", return_value=fixture()), \
             mock.patch.object(arctic_shift.time, "sleep"):
            items = arctic_shift.fetch(10, conf, cur)
        self.assertEqual([i.external_id for i in items], ["t3_1x41f09"])
        self.assertEqual(cur["SomebodyMakeThis"], 1791810520)  # 没命中的也算见过

    def test_without_cursors_behaves_as_first_run(self):
        with mock.patch.object(arctic_shift, "get_json", return_value={"data": []}) as g:
            arctic_shift.fetch(10, {"subreddits": ["macapps"]})
        self.assertNotIn("after=", g.call_args[0][0])

    def test_page_size_capped_at_api_max(self):
        with mock.patch.object(arctic_shift, "get_json", return_value={"data": []}) as g:
            arctic_shift.fetch(500, {"subreddits": ["macapps"]})
        self.assertIn("limit=100", g.call_args[0][0])


class RetryTest(unittest.TestCase):
    def _fetch(self, side_effect):
        with mock.patch.object(arctic_shift, "get_json", side_effect=side_effect) as g, \
             mock.patch.object(arctic_shift.time, "sleep") as sleep:
            items = arctic_shift.fetch(10, {"subreddits": ["SomebodyMakeThis"]})
        return items, g, sleep

    def test_429_waits_for_reset_header_then_succeeds(self):
        items, g, sleep = self._fetch([http_error(429, "7"), fixture()])
        self.assertEqual(len(items), 3)
        self.assertEqual(g.call_count, 2)
        sleep.assert_called_once_with(7)

    def test_429_without_header_uses_default_wait(self):
        _, _, sleep = self._fetch([http_error(429), {"data": []}])
        sleep.assert_called_once_with(arctic_shift.RATE_WAIT_DEFAULT)

    def test_reset_wait_is_capped(self):
        _, _, sleep = self._fetch([http_error(429, "9999"), {"data": []}])
        sleep.assert_called_once_with(arctic_shift.RATE_WAIT_MAX)

    def test_422_timeout_retried_with_backoff(self):
        # 实测同一查询连续 422 之后原样重发就能过，所以退避重试三次
        items, g, sleep = self._fetch([http_error(422), http_error(422), http_error(422), fixture()])
        self.assertEqual(len(items), 3)
        self.assertEqual(g.call_count, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], list(arctic_shift.TIMEOUT_WAITS))

    def test_one_subreddit_failing_does_not_kill_the_others(self):
        # 第一个版块重试耗尽仍 422：跳过它、游标不动，第二个版块照抓
        cursors: dict[str, int] = {}
        errors = [http_error(422)] * (len(arctic_shift.TIMEOUT_WAITS) + 1)
        with mock.patch.object(arctic_shift, "get_json", side_effect=[*errors, fixture()]), \
             mock.patch.object(arctic_shift.time, "sleep"), \
             mock.patch.object(arctic_shift.sys, "stderr"):
            items = arctic_shift.fetch(10, {"subreddits": ["SomebodyMakeThis", "macapps"]}, cursors)
        self.assertEqual(len(items), 3)
        self.assertEqual(set(cursors), {"macapps"})

    def test_gives_up_when_every_subreddit_fails(self):
        errors = [http_error(422)] * (len(arctic_shift.TIMEOUT_WAITS) + 1)
        with mock.patch.object(arctic_shift.sys, "stderr"), self.assertRaises(urllib.error.HTTPError):
            self._fetch(errors)

    def test_other_errors_not_retried(self):
        with mock.patch.object(arctic_shift, "get_json", side_effect=[http_error(400)]) as g, \
             mock.patch.object(arctic_shift.time, "sleep") as sleep, \
             self.assertRaises(urllib.error.HTTPError):
            arctic_shift.fetch(10, {"subreddits": ["macapps"]})
        self.assertEqual(g.call_count, 1)
        sleep.assert_not_called()


class BackfillTest(unittest.TestCase):
    def test_page_before_params(self):
        with mock.patch.object(arctic_shift, "get_json", return_value={"data": []}) as g:
            arctic_shift.page_before({}, "macapps", None, 5000)
            url = g.call_args[0][0]
            self.assertIn("limit=100", url)
            self.assertIn("sort=desc", url)
            self.assertNotIn("before=", url)
            arctic_shift.page_before({}, "macapps", 1791810520, 50)
            url = g.call_args[0][0]
            self.assertIn("before=1791810520", url)  # before 不含端点，不用 -1
            self.assertIn("limit=50", url)

    def test_keywords_not_applied_in_backfill(self):
        conf = {"subreddits": ["SomebodyMakeThis"], "keywords": {"SomebodyMakeThis": ["nothing matches this"]}}
        with mock.patch.object(arctic_shift, "get_json", return_value=fixture()):
            items = arctic_shift.page_before(conf, "SomebodyMakeThis", None, 100)
        self.assertEqual(len(items), 3)  # 整页筛空会被当成「到头」，回填拉整个版块

    def test_units_and_interval(self):
        self.assertEqual(arctic_shift.units({"subreddits": ["a", "b"]}), ["a", "b"])
        self.assertEqual(arctic_shift.units({}), [])
        self.assertGreaterEqual(arctic_shift.interval(), 2)


class RegistryTest(unittest.TestCase):
    def test_registered_and_backfillable(self):
        self.assertIs(sources.REGISTRY["arctic_shift"], arctic_shift.fetch)
        self.assertIs(sources.backfill_module("arctic_shift"), arctic_shift)

    def test_fetched_before_rss_so_richer_record_wins(self):
        names = list(sources.REGISTRY)
        self.assertLess(names.index("arctic_shift"), names.index("rss"))
        # 同一帖子两边都抓到：fetch_all 按 (source, external_id) 去重，先到的 Arctic Shift 版本（带 score）胜出
        a = SourceItem("reddit/macapps", "t3_1", "t", "real body", "u", score=5)
        r = SourceItem("reddit/macapps", "t3_1", "t", "submitted by /u/x [link] [comments]", "u")
        with mock.patch.dict(sources.REGISTRY, {"arctic_shift": lambda _limit, c, cur: [a],
                                                "rss": lambda _limit, c, cur: [r]}):
            items, errors = sources.fetch_all(["arctic_shift", "rss"], 5, {})
        self.assertEqual(errors, {})
        self.assertEqual([(i.external_id, i.score) for i in items], [("t3_1", 5)])

    def test_default_config_keeps_reddit_off_but_ready(self):
        conf = sources.load_config(os.path.join(HERE, "..", "sources.toml"))
        # 灰色来源默认关闭，由用户自己打开；版块和关键词配置保留，打开即用
        self.assertNotIn("arctic_shift", sources.enabled(conf))
        rss_subs = {f["name"] for f in conf["rss"] if f["name"].startswith("reddit/")}
        self.assertEqual({f"reddit/{s}" for s in conf["arctic_shift"]["subreddits"]}, rss_subs)  # 互为兜底
        self.assertTrue(all(f.get("enabled") is False for f in conf["rss"] if f["name"].startswith("reddit/")))
        self.assertTrue(set(conf["arctic_shift"]["keywords"]) <= set(conf["arctic_shift"]["subreddits"]))

    def test_disabled(self):
        self.assertNotIn("arctic_shift", sources.enabled({"arctic_shift": {"enabled": False}, "rss": []}))


if __name__ == "__main__":
    unittest.main()
