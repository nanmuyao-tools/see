"""规则预过滤、摘录校验、条目状态的离线测试。"""

from __future__ import annotations

import json
import os
import unittest

from demand_radar import filters
from demand_radar.extract import excerpt_in_source
from demand_radar.models import Demand, SourceItem

HERE = os.path.dirname(__file__)


def it(title, text="a body that is long enough to count as content", kind="post",
       source="reddit/x", external_id="t3_1"):
    return SourceItem(source=source, external_id=external_id, title=title, text=text,
                      url="u", kind=kind)


class RuleTest(unittest.TestCase):
    def test_empty_body_dropped_when_title_has_no_request_cue(self):
        self.assertEqual(filters.check(it("Help for my prodject",
                                          "submitted by /u/abc [link] [comments]")), "empty_body")

    def test_empty_body_kept_when_title_looks_like_a_request(self):
        # 只有标题也能看出是需求，不能丢
        self.assertIsNone(filters.check(it("Car Information App",
                                           "submitted by /u/abc [link] [comments]")))
        self.assertIsNone(filters.check(it("有没有好用的截图工具", "")))

    def test_show_hn_post_dropped_but_comment_kept(self):
        self.assertEqual(filters.check(it("Show HN: My new app", kind="post")), "show_hn")
        # Show HN 帖子下的评论常是真需求
        self.assertIsNone(filters.check(it("Show HN: Audiomass", "I need an audio tool to remaster old songs",
                                           kind="comment")))

    def test_self_promo_only_on_post_titles(self):
        self.assertEqual(filters.check(it("I made a tiny open-source app firewall")), "self_promo")
        self.assertEqual(filters.check(it("I'm building free tools to solve real problems")), "self_promo")
        self.assertIsNone(filters.check(it("I made a mistake, is there a tool to undo it?", kind="comment")))
        self.assertIsNone(filters.check(it("Is there an app I can use?",
                                           "I built a spreadsheet but it is too slow")))

    def test_bot_json(self):
        self.assertEqual(filters.check(it("Rep Radar digest", '{"part":2,"totalParts":5}')), "bot_json")

    def test_subreddit_listing(self):
        self.assertEqual(filters.check(it("androidapps", external_id="t5_2reen")), "subreddit_listing")

    def test_supply_source_configurable(self):
        item = it("Inbox Zero", "Open-source Superhuman alternative", source="producthunt")
        self.assertIsNone(filters.check(item, {}))
        self.assertEqual(filters.check(item, {"supply_sources": ["producthunt"]}), "supply_source")

    def test_rules_can_be_disabled(self):
        self.assertIsNone(filters.check(it("Show HN: x"), {"disabled": ["show_hn"]}))

    def test_eval_set_has_no_false_kills(self):
        """评测集里所有「应入选」的样本都不能被规则挡掉。"""
        data = json.load(open(os.path.join(HERE, "..", "eval", "eval_set.json"), encoding="utf-8"))
        conf = {"supply_sources": ["producthunt"]}
        for d in data:
            item = SourceItem(**{k: d[k] for k in
                                 ("source", "external_id", "title", "text", "url", "score", "kind")})
            if d["label"]["is_request"] and d["label"]["buildable"]:
                self.assertIsNone(filters.check(item, conf), d["title"])


class ExcerptTest(unittest.TestCase):
    item = it("Is there an app for this?",
              "I’ve tried everything.  I want a tool that saves my thoughts while reading.")

    def test_exact_and_normalized_match(self):
        self.assertTrue(excerpt_in_source("I want a tool that saves my thoughts while reading.", self.item))
        # 大小写、弯引号、多余空白、首尾引号和省略号不影响
        self.assertTrue(excerpt_in_source("“i've tried everything. i want a tool…”", self.item))
        self.assertTrue(excerpt_in_source("Is there an app for this?", self.item))  # 来自标题

    def test_invented_excerpt_rejected(self):
        self.assertFalse(excerpt_in_source("I need a note-taking app for books", self.item))
        self.assertFalse(excerpt_in_source("", self.item))
        self.assertFalse(excerpt_in_source("...", self.item))


def demand(**kw):
    base = dict(is_request=True, need="n", domain="d", pain_point="", excerpt="", buildable=True,
                clarity=3, intensity=3, feasibility=3, confidence=0.9)
    return Demand(**{**base, **kw})


try:
    import mongomock
except ImportError:  # pragma: no cover
    mongomock = None


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -r requirements-dev.txt")
class StoreStatusTest(unittest.TestCase):
    def setUp(self):
        from demand_radar import store
        self.store = store
        self.coll = mongomock.MongoClient().db.demands

    def test_filtered_items_not_processed_again(self):
        item = it("Help")
        self.assertTrue(self.store.should_process(self.coll, item))
        self.store.save_filtered(self.coll, item, "empty_body")
        self.assertFalse(self.store.should_process(self.coll, item))
        self.assertEqual(self.coll.find_one()["filter_reason"], "empty_body")

    def test_failures_retry_until_limit(self):
        item = it("x")
        for n in range(1, self.store.MAX_ATTEMPTS):
            self.assertEqual(self.store.record_failure(self.coll, item, "boom"), n)
            self.assertTrue(self.store.should_process(self.coll, item))
        self.store.record_failure(self.coll, item, "boom")
        self.assertEqual(self.coll.find_one()["status"], "failed")
        self.assertFalse(self.store.should_process(self.coll, item))

    def test_success_after_failure_overwrites(self):
        item = it("x")
        self.store.record_failure(self.coll, item, "boom")
        self.store.save(self.coll, item, demand())
        doc = self.coll.find_one()
        self.assertEqual(doc["status"], "extracted")
        self.assertNotIn("last_error", doc)
        self.assertFalse(self.store.should_process(self.coll, item))


if __name__ == "__main__":
    unittest.main()
