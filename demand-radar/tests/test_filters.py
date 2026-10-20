"""规则预过滤、摘录校验、条目状态的离线测试。"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from demand_radar import excerpt, filters
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
        data = json.loads(Path(os.path.join(HERE, "..", "eval", "eval_set.json")).read_text(encoding="utf-8"))
        conf = {"supply_sources": ["producthunt"]}
        for d in data:
            item = SourceItem(**{k: d[k] for k in
                                 ("source", "external_id", "title", "text", "url", "score", "kind")})
            if d["label"]["is_request"] and d["label"]["buildable"]:
                self.assertIsNone(filters.check(item, conf), d["title"])


class ExcerptTest(unittest.TestCase):
    """摘录由程序从原文挑选，必须是原文原话。"""

    def test_post_title_with_cue_preferred(self):
        item = it("Is there a program to retrieve the focused window?",
                  "I'd like to spend less than $100. I've written most of it already.")
        self.assertEqual(excerpt.pick(item), "Is there a program to retrieve the focused window?")

    def test_first_body_sentence_with_cue(self):
        item = it("Web push notifications server",
                  "Browsers support web push. I need a self-hosted server for it. Is there one?")
        self.assertEqual(excerpt.pick(item), "I need a self-hosted server for it.")

    def test_bare_question_is_not_a_cue(self):
        # 「Does this exist?」这类泛泛的问句不该被选中，回退到标题
        item = it("Voting bot that changes flairs", "Use case: people vote on posts. Does this exist?")
        self.assertEqual(excerpt.pick(item), "Voting bot that changes flairs")

    def test_comment_never_uses_story_title(self):
        # 评论的标题是所在帖子的标题，不是评论者的话
        item = it("Show HN: Audiomass – an audio editor",
                  "Great work. I need an audio tool to remaster old songs.", kind="comment")
        self.assertEqual(excerpt.pick(item), "I need an audio tool to remaster old songs.")
        plain = it("Show HN: Is there a tool", "Nice project, congrats on shipping it.", kind="comment")
        self.assertEqual(excerpt.pick(plain), "Nice project, congrats on shipping it.")

    def test_chinese_cue(self):
        item = it("截图工具", "现在用的太卡了。有没有好用的开源截图工具？")
        self.assertEqual(excerpt.pick(item), "有没有好用的开源截图工具？")

    def test_excerpt_is_always_verbatim(self):
        data = json.loads(Path(os.path.join(HERE, "..", "eval", "eval_set.json")).read_text(encoding="utf-8"))
        for d in data:
            item = SourceItem(**{k: d[k] for k in
                                 ("source", "external_id", "title", "text", "url", "score", "kind")})
            e = excerpt.pick(item)
            self.assertIn(e, f"{item.title}\n{item.text}", d["title"])

    def test_long_sentence_trimmed_to_prefix(self):
        long = "I need a tool that " + "does many things " * 40
        e = excerpt.pick(it("x", long))
        self.assertLessEqual(len(e), excerpt.MAX_LEN)
        self.assertTrue(long.startswith(e))


def demand(**kw):
    base = dict(is_request=True, need="n", domain="d", pain_point="", excerpt="", buildable=True,
                clarity=3, intensity=3, feasibility=3, confidence=0.9)
    return Demand(**{**base, **kw})


class SafetyTest(unittest.TestCase):
    """可能有害的需求由规则标记、转人工确认，不交给模型判断。"""

    def test_cues(self):
        from demand_radar import safety
        hit = ["Is there an app to see where my girlfriend is?", "find someone's home address",
               "bot to mass report an account", "tracks and makes public movement of council members",
               "SMS that looks like a bank", "hundreds of different 5-star Google reviews",
               "怎么偷偷看对象的聊天记录", "我对象的手机能不能远程看",
               # 凭照片 / 头像找出特定的人
               "Software to find a LinkedIn/Xing profile using name, age, and a screenshot of the avatar",
               "identify who this person is from a photo", "reverse image search to find out who someone is",
               "用头像找出这个人是谁"]
        miss = ["Editor with doxygen preview", "Track all my packages in one place",
                "Self-hosted password manager", "App to track a loan", "Web push notifications server",
                # 编程里的「对象」不是伴侣；和图片相关的普通需求不算找人
                "JavaScript 对象的原型链详解", "find the best profile picture size for LinkedIn",
                "photo editor to find and remove people from the background"]
        for t in hit:
            self.assertTrue(safety.needs_check(it(t)), t)
        for t in miss:
            self.assertFalse(safety.needs_check(it(t)), t)

    def test_extract_records_cue_with_one_model_call(self):
        from unittest import mock

        from demand_radar.extract import extract
        client = mock.MagicMock()
        client.chat.completions.create.return_value = demand()
        d = extract(client, it("Bot to mass report an account"))
        self.assertEqual(d.review_cue.lower(), "mass report")
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_internal_fields_not_in_extraction_schema(self):
        props = Demand.model_json_schema()["properties"]
        self.assertNotIn("review_cue", props)
        self.assertNotIn("excerpt", props)


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

    def test_flagged_demands_are_held_not_listed(self):
        self.store.save(self.coll, it("ok", external_id="t3_ok"), demand())
        self.store.save(self.coll, it("bad", external_id="t3_bad"), demand(review_cue="mass report"))
        self.coll.insert_one({"_id": "legacy", "is_request": True, "buildable": True,
                              "confidence": 0.9})  # 旧数据没有 review_cue 字段
        got = {d["_id"] for d in self.store.top_demands(self.coll, 0.5)}
        self.assertEqual(got, {"reddit/x:t3_ok", "legacy"})
        held = {d["_id"] for d in self.store.held_demands(self.coll, 0.5)}
        self.assertEqual(held, {"reddit/x:t3_bad"})

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
