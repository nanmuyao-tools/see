"""App Store / Google Play 评论源的离线测试：解析、星级过滤、空 feed、游标、限流、注册，
以及评论专用的过滤阈值、摘录和提示词。

fixture 取自 2026-10-20 的真实响应裁剪，评论人昵称和头像已去掉。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from demand_radar import excerpt, extract, filters, sources
from demand_radar.models import SourceItem
from demand_radar.sources import appstore, googleplay, http

HERE = os.path.dirname(__file__)
SHARK = {"name": "鲨鱼记账", "id": 1079718756, "countries": ["cn"]}
NOTION_GP = {"name": "Notion", "package": "notion.id", "locales": ["en-US"]}


def fixture(name: str) -> bytes:
    with open(os.path.join(HERE, "fixtures", name), "rb") as f:
        return f.read()


EMPTY_JSON = b'{"feed": {"id": {"label": "x"}}}'
EMPTY_XML = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'


class AppStoreParseTest(unittest.TestCase):
    def test_json_skips_app_metadata(self):
        rows = appstore.parse_json(fixture("appstore.json"))
        self.assertEqual([r["rating"] for r in rows], [1, 3, 5])
        self.assertTrue(all(r["id"] and r["updated"] for r in rows))

    def test_xml_reads_text_content_only(self):
        rows = appstore.parse_xml(fixture("appstore.xml"))
        self.assertEqual(len(rows), 2)
        self.assertNotIn("<table", rows[0]["text"])  # html 那份 content 不要

    def test_single_entry_is_dict(self):
        one = b'{"feed": {"entry": {"id": {"label": "1"}, "im:rating": {"label": "2"}, "title": {"label": "t"}, ' \
              b'"content": {"label": "c"}, "updated": {"label": "2026-10-01T00:00:00-07:00"}}}}'
        self.assertEqual(len(appstore.parse_json(one)), 1)

    def test_items_low_rating_only_and_no_author(self):
        items = appstore.to_items(SHARK, "cn", appstore.parse_json(fixture("appstore.json")), 3)
        self.assertEqual(len(items), 2)
        it = items[0]
        self.assertEqual(it.source, "appstore/cn/鲨鱼记账")
        self.assertEqual(it.kind, "review")
        self.assertTrue(it.title.startswith("鲨鱼记账（App Store 1 星）"))
        self.assertEqual(it.url, "https://apps.apple.com/cn/app/id1079718756")
        self.assertGreater(it.created_at, 0)


class AppStoreFetchTest(unittest.TestCase):
    def test_empty_json_falls_back_to_xml_once(self):
        with mock.patch.object(appstore, "get", side_effect=[EMPTY_JSON, fixture("appstore.xml")]) as g:
            rows = appstore._page("us", 1232780281, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(g.call_count, 2)

    def test_empty_feed_skips_app_and_keeps_cursor(self):
        cur = {"cn/1079718756": 5}
        with mock.patch.object(appstore, "get", side_effect=[EMPTY_JSON, EMPTY_XML]) as g:
            items = appstore.fetch(10, {"apps": [SHARK]}, cur)
        self.assertEqual((items, cur, g.call_count), ([], {"cn/1079718756": 5}, 2))

    def test_cursor_advances_to_newest_seen_even_if_high_rating(self):
        cur: dict[str, int] = {}
        with mock.patch.object(appstore, "get", return_value=fixture("appstore.json")):
            items = appstore.fetch(10, {"apps": [SHARK], "max_pages": 3}, cur)
        rows = appstore.parse_json(fixture("appstore.json"))
        newest = max(appstore.cursor.epoch(r["updated"]) for r in rows)
        self.assertEqual(cur["cn/1079718756"], newest)
        self.assertEqual(len(items), 2)  # 不到 50 条说明到底了，只请求一页

    def test_seen_reviews_not_returned_again(self):
        rows = appstore.parse_json(fixture("appstore.json"))
        newest = max(appstore.cursor.epoch(r["updated"]) for r in rows)
        with mock.patch.object(appstore, "get", return_value=fixture("appstore.json")):
            self.assertEqual(appstore.fetch(10, {"apps": [SHARK]}, {"cn/1079718756": newest}), [])

    def test_max_pages_capped_at_10(self):
        page = appstore.parse_json(fixture("appstore.json"))[0]
        full = [dict(page, id=str(i)) for i in range(appstore.PAGE_SIZE)]
        with mock.patch.object(appstore, "_page", return_value=full) as p:
            appstore.fetch(10_000, {"apps": [SHARK], "max_pages": 50, "max_rating": 0}, {})
        self.assertEqual(p.call_count, appstore.MAX_PAGES)

    def test_one_app_failing_does_not_stop_others(self):
        other = {"name": "Notion", "id": 1232780281, "countries": ["us"]}

        def fake(url, headers=None):
            if "1079718756" in url:
                raise OSError("boom")
            return fixture("appstore.json")
        with mock.patch.object(appstore, "get", side_effect=fake):
            items = appstore.fetch(10, {"apps": [SHARK, other]}, {})
        self.assertTrue(items)

    def test_all_failing_raises(self):
        with mock.patch.object(appstore, "get", side_effect=OSError("boom")), self.assertRaises(RuntimeError):
            appstore.fetch(10, {"apps": [SHARK]}, {})


class GooglePlayTest(unittest.TestCase):
    def test_parse(self):
        rows, token = googleplay.parse(fixture("googleplay.txt"))
        self.assertEqual([r["rating"] for r in rows], [2, 1, 5])
        self.assertTrue(token)
        self.assertGreater(rows[0]["created_at"], 1_700_000_000)  # 直接是 Unix 秒

    def test_items(self):
        rows, _ = googleplay.parse(fixture("googleplay.txt"))
        items = googleplay.to_items(NOTION_GP, "en-US", "us", rows, 3)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source, "googleplay/us/Notion")
        self.assertEqual(items[0].title, "Notion（Google Play 2 星）")
        self.assertIn("id=notion.id", items[0].url)
        self.assertEqual(items[0].kind, "review")

    def test_wrong_package_raises_instead_of_empty(self):
        shell = b')]}\'\n\n[["wrb.fr","oCPfdb","[null,null,[[null,[[1800],null,[72000]]]]]",null,null,null,"generic"]]'
        with self.assertRaises(googleplay.PlayFormatError):
            googleplay.parse(shell)

    def test_body_matches_web_format(self):
        body = googleplay._body("notion.id", None).decode()
        self.assertTrue(body.startswith("f.req=%5B%5B%5B%22oCPfdb%22"))
        self.assertIn("notion.id", body.replace("%5C%22", ""))
        self.assertIn("tok123", googleplay._body("notion.id", "tok123").decode())

    def test_locale(self):
        self.assertEqual(googleplay._locale("zh-TW"), ("zh-TW", "tw"))
        self.assertEqual(googleplay._locale("en"), ("en", "us"))

    def test_rate_limited_cools_down_whole_host_and_stops(self):
        apps = {"apps": [NOTION_GP, {"name": "YNAB", "package": "x", "locales": ["en-US"]}]}
        limited = b")]}'\n\ncom.google.play.gateway.proto.PlayGatewayError"
        with mock.patch.object(http, "post", return_value=limited) as p, \
                mock.patch.object(http.limiter, "cooldown") as cd, self.assertRaises(RuntimeError):
            googleplay.fetch(10, apps, {})
        self.assertEqual(p.call_count, 1)  # 第二个 app 不再请求
        cd.assert_called_once_with("play.google.com", googleplay.COOLDOWN)

    def test_cursor_and_paging_stop_at_seen(self):
        raw = fixture("googleplay.txt")
        rows, _ = googleplay.parse(raw)
        cur: dict[str, int] = {}
        with mock.patch.object(http, "post", return_value=raw) as p:
            googleplay.fetch(10, {"apps": [NOTION_GP], "max_pages": 3}, cur)
        self.assertEqual(cur["us/notion.id"], max(r["created_at"] for r in rows))
        self.assertEqual(p.call_count, 3)  # 有 token、没够 limit：翻满 3 页
        with mock.patch.object(http, "post", return_value=raw) as p:
            self.assertEqual(googleplay.fetch(10, {"apps": [NOTION_GP], "max_pages": 3}, cur), [])
        self.assertEqual(p.call_count, 1)  # 第一页全是看过的，不再往后翻

    def test_empty_later_page_is_end_not_error(self):
        shell = b')]}\'\n\n[["wrb.fr","oCPfdb","[null,null,[[null,[[1800],null,[72000]]]]]",null,null,null,"generic"]]'
        with mock.patch.object(http, "post", side_effect=[fixture("googleplay.txt"), shell]):
            items = googleplay.fetch(10, {"apps": [NOTION_GP], "max_pages": 3}, {})
        self.assertEqual(len(items), 2)


class RateLimitTest(unittest.TestCase):
    def test_store_hosts_have_conservative_interval(self):
        lim = http.RateLimiter()
        self.assertGreaterEqual(lim.interval("itunes.apple.com"), 5.0)
        self.assertGreaterEqual(lim.interval("play.google.com"), 5.0)

    def test_post_goes_through_limiter(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b"ok"
        with mock.patch.object(http.limiter, "wait") as w, \
                mock.patch("urllib.request.urlopen", return_value=resp) as u:
            self.assertEqual(http.post("https://play.google.com/_/x", b"a=1"), b"ok")
        w.assert_called_once_with("play.google.com")
        self.assertEqual(u.call_args[0][0].get_method(), "POST")


class RegistryTest(unittest.TestCase):
    def test_registered_and_off_by_default(self):
        self.assertIn("appstore", sources.REGISTRY)
        self.assertIn("googleplay", sources.REGISTRY)
        conf = sources.load_config(os.path.join(HERE, "..", "sources.toml"))
        on = sources.enabled(conf)
        self.assertNotIn("appstore", on)
        self.assertNotIn("googleplay", on)
        self.assertTrue(conf["appstore"]["apps"] and conf["googleplay"]["apps"])

    def test_no_backfill(self):
        with self.assertRaises(ValueError):
            sources.backfill_module("appstore")


def review(text: str, title: str = "某 app（App Store 1 星）") -> SourceItem:
    return SourceItem("appstore/cn/x", "1", title, text, "", kind="review")


class ReviewPipelineTest(unittest.TestCase):
    def test_short_chinese_review_not_filtered(self):
        self.assertIsNone(filters.check(review("为什么不可以多人共享旧帐本")))
        self.assertEqual(filters.check(review("垃圾")), "empty_body")
        # App Store 评论人自己写的标题也算正文
        self.assertIsNone(filters.check(review("广告多", "随手记（App Store 1 星）：开屏广告")))
        self.assertEqual(filters.check(review("good", "Notion（Google Play 3 星）")), "empty_body")

    def test_post_threshold_unchanged(self):
        post = SourceItem("x", "1", "Hello", "为什么不可以多人共享旧帐本", "")
        self.assertEqual(filters.check(post), "empty_body")

    def test_short_review_excerpt_is_whole_text(self):
        self.assertEqual(excerpt.pick(review("ipad冇中文")), "ipad冇中文")

    def test_review_uses_review_prompt(self):
        client = mock.MagicMock()
        extract.extract_raw(client, review("广告太多"), "zh")
        system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIs(system, extract._REVIEW_SYSTEM["zh"])
        extract.extract_raw(client, SourceItem("x", "1", "t", "body", ""), "en")
        system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIs(system, extract._SYSTEM["en"])


if __name__ == "__main__":
    unittest.main()
