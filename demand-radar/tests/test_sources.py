"""数据源解析与编排的离线测试。fixture 取自各源的真实返回（GitHub 按官方文档构造）。

运行：python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import unittest
import urllib.error
from unittest import mock

from demand_radar import sources
from demand_radar.models import SourceItem
from demand_radar.sources import github, hn_algolia, rss, softwarerecs
from demand_radar.sources.http import MAX_TEXT, clean

HERE = os.path.dirname(__file__)


def fixture(name: str) -> bytes:
    with open(os.path.join(HERE, "fixtures", name), "rb") as f:
        return f.read()


class CleanTest(unittest.TestCase):
    def test_strips_tags_entities_and_whitespace(self):
        self.assertEqual(clean("<p>I wish   there was</p>\n<i>an app</i> &amp; more"),
                         "I wish there was an app & more")

    def test_truncates_and_handles_none(self):
        self.assertEqual(len(clean("x" * (MAX_TEXT + 50))), MAX_TEXT)
        self.assertEqual(clean(None), "")


class RssTest(unittest.TestCase):
    def test_atom_reddit(self):
        items = rss.parse("reddit/SomebodyMakeThis", fixture("reddit.atom"), limit=10)
        self.assertEqual(len(items), 2)
        it = items[0]
        self.assertEqual(it.source, "reddit/SomebodyMakeThis")
        self.assertTrue(it.external_id.startswith("t3_"))
        self.assertTrue(it.url.startswith("https://www.reddit.com/r/SomebodyMakeThis/"))
        self.assertTrue(it.title)
        self.assertNotIn("<", it.text)

    def test_rss2(self):
        items = rss.parse("appinn", fixture("appinn.rss"), limit=10)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i.url.startswith("http") and i.title for i in items))

    def test_limit_per_feed(self):
        self.assertEqual(len(rss.parse("x", fixture("reddit.atom"), limit=1)), 1)

    def test_one_feed_failure_does_not_stop_others(self):
        feeds = [{"name": "bad", "url": "u1"}, {"name": "good", "url": "u2"}]

        def fake_get(url):
            if url == "u1":
                raise OSError("HTTP Error 429")
            return fixture("reddit.atom")

        with mock.patch.object(rss, "get", side_effect=fake_get), \
                          mock.patch("sys.stderr"):
            items = rss.fetch(10, feeds)
        self.assertEqual({i.source for i in items}, {"good"})

    def test_all_feeds_failing_raises(self):
        with mock.patch.object(rss, "get", side_effect=OSError("down")), \
                          mock.patch("sys.stderr"), \
             self.assertRaises(RuntimeError):
            rss.fetch(10, [{"name": "a", "url": "u"}])


class RetryTest(unittest.TestCase):
    def _http_error(self, code, retry_after=None):
        import email.message
        import urllib.error
        h = email.message.Message()
        if retry_after:
            h["Retry-After"] = retry_after
        return urllib.error.HTTPError("u", code, "x", h, None)

    def _ok(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b"ok"
        return resp

    def test_retries_on_429_then_succeeds(self):
        from demand_radar.sources import http
        slept = []
        lim = http.RateLimiter(clock=lambda: 0.0, sleep=slept.append)  # 时钟不走，只看要等多久
        with mock.patch.object(http.urllib.request, "urlopen",
                               side_effect=[self._http_error(429, "3"), self._ok()]), \
             mock.patch.object(http, "limiter", lim):
            self.assertEqual(http.get("http://example.test/"), b"ok")
        self.assertEqual(slept, [3])   # 遵守 Retry-After：整个域名冷却 3 秒后重试

    def test_does_not_retry_on_403(self):
        from demand_radar.sources import http
        with mock.patch.object(http.urllib.request, "urlopen",
                               side_effect=[self._http_error(403)]) as op, \
             mock.patch.object(http.time, "sleep"), \
             self.assertRaises(urllib.error.HTTPError):
            http.get("http://example.test/")
        self.assertEqual(op.call_count, 1)

    def test_gives_up_after_retries(self):
        from demand_radar.sources import http
        with mock.patch.object(http.urllib.request, "urlopen",
                               side_effect=[self._http_error(503)] * 5) as op, \
             mock.patch.object(http.time, "sleep"), \
             self.assertRaises(urllib.error.HTTPError):
            http.get("http://example.test/")
        self.assertEqual(op.call_count, http.RETRIES + 1)


class SoftwareRecsTest(unittest.TestCase):
    def test_parse(self):
        items = softwarerecs.parse("softwarerecs", json.loads(fixture("softwarerecs.json")))
        self.assertEqual(len(items), 2)
        it = items[0]
        self.assertEqual(it.source, "stackexchange/softwarerecs")
        self.assertTrue(it.external_id.isdigit())
        self.assertIn("softwarerecs.stackexchange.com", it.url)
        self.assertNotIn("<p>", it.text)


class HnAlgoliaTest(unittest.TestCase):
    def test_story_and_comment(self):
        story, comment = hn_algolia.parse(json.loads(fixture("hn_algolia.json")))
        self.assertTrue(story.title)
        self.assertEqual(story.url, f"https://news.ycombinator.com/item?id={story.external_id}")
        self.assertTrue(comment.text)          # 评论正文进 text
        self.assertTrue(comment.title)         # 标题取所在帖子的 story_title

    def test_query_is_exact_phrase(self):
        with mock.patch.object(hn_algolia, "get_json", return_value={"hits": []}) as g:
            hn_algolia.fetch(5, {"queries": ["is there a tool"]})
        url = g.call_args[0][0]
        self.assertIn("query=%22is+there+a+tool%22", url)
        self.assertIn("hitsPerPage=5", url)


class GithubTest(unittest.TestCase):
    def test_parse(self):
        a, b = github.parse(json.loads(fixture("github_issues.json")))
        self.assertEqual(a.external_id, "example/app#1234")
        self.assertEqual(a.score, 321)
        self.assertTrue(a.title.startswith("[example/app] "))
        self.assertNotIn("<b>", a.text)
        self.assertEqual(b.text, "")            # body 为 null

    def test_token_is_optional(self):
        with mock.patch.object(github, "get_json", return_value={"items": []}) as g, \
             mock.patch.dict(os.environ, {}, clear=True):
            github.fetch(5, {"queries": ["is:issue"]})
        self.assertNotIn("Authorization", g.call_args[0][1])
        with mock.patch.object(github, "get_json", return_value={"items": []}) as g, \
             mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            github.fetch(5, {"queries": ["is:issue"]})
        self.assertEqual(g.call_args[0][1]["Authorization"], "Bearer t")


class DemandModelTest(unittest.TestCase):
    """小模型的常见输出偏差，实测中出现过。"""

    def test_null_text_fields_become_empty(self):
        from demand_radar.models import Demand
        d = Demand.model_validate({"is_request": False, "need": None, "domain": None,
                                   "pain_point": None, "excerpt": None, "buildable": False,
                                   "clarity": 1, "intensity": 1, "feasibility": 1, "confidence": 0.1})
        self.assertEqual((d.need, d.domain, d.pain_point, d.excerpt), ("", "", "", ""))

    def test_percent_confidence_is_normalized(self):
        from demand_radar.models import Demand
        d = Demand.model_validate({"is_request": True, "need": "x", "domain": "x",
                                   "pain_point": "", "excerpt": "", "buildable": True,
                                   "clarity": 3, "intensity": 3, "feasibility": 3, "confidence": 80})
        self.assertAlmostEqual(d.confidence, 0.8)

    def test_scores_clamped_to_1_5(self):
        from demand_radar.models import Demand
        d = Demand.model_validate({"is_request": True, "need": "x", "domain": "x",
                                   "pain_point": "", "excerpt": "", "buildable": True,
                                   "clarity": 0, "intensity": 7, "feasibility": None, "confidence": 0.5})
        self.assertEqual((d.clarity, d.intensity, d.feasibility), (1, 5, 1))


class ReasonTest(unittest.TestCase):
    def test_extracts_last_instructor_error(self):
        from demand_radar.cli import _reason
        e = Exception("<failed_attempts>\n<generation>x</generation>\n</failed_attempts>\n"
                      "<last_exception>\n  1 validation error for Demand\nneed\n"
                      "  Input should be a valid string\n"
                      "    For further information visit https://errors.pydantic.dev/x\n"
                      "</last_exception>")
        self.assertEqual(_reason(e),
                         "1 validation error for Demand / need / Input should be a valid string")

    def test_plain_error(self):
        from demand_radar.cli import _reason
        self.assertEqual(_reason(OSError("timed out")), "timed out")


class BuildClientTest(unittest.TestCase):
    def test_local_prefix_uses_openai_compatible_server(self):
        import instructor

        from demand_radar.extract import build_client
        with mock.patch.object(instructor, "from_provider") as fp:
            build_client("local/mlx-community/Qwen3-4B", "http://localhost:8080/v1")
        fp.assert_called_once_with("ollama/mlx-community/Qwen3-4B", mode=instructor.Mode.JSON,
                                   base_url="http://localhost:8080/v1")

    def test_base_url_flag(self):
        from demand_radar.config import Config
        cfg = Config.from_args(["eval", "--provider", "local/m", "--base-url", "http://h:8080/v1"])
        self.assertEqual(cfg.ollama_base_url, "http://h:8080/v1")


class FetchAllTest(unittest.TestCase):
    def test_one_source_failure_does_not_stop_others(self):
        ok = [SourceItem("s", "1", "t", "", "u"), SourceItem("s", "1", "t", "", "u")]  # 重复
        with mock.patch.dict(sources.REGISTRY, {
            "softwarerecs": lambda limit, conf, cursors: ok,
            "github": lambda limit, conf, cursors: (_ for _ in ()).throw(OSError("rate limited")),
        }):
            items, errors = sources.fetch_all(["softwarerecs", "github"], 5, {})
        self.assertEqual(len(items), 1)         # 去重
        self.assertIn("rate limited", errors["github"])

    def test_unknown_source(self):
        with self.assertRaises(ValueError):
            sources.fetch_all(["weibo"], 5, {})

    def test_default_config_enables_all_but_app_stores_and_reddit(self):
        conf = sources.load_config(os.path.join(HERE, "..", "sources.toml"))
        # 应用商店评论默认关闭（条款原因）；Reddit（arctic_shift + Reddit RSS）暂时关闭，见 sources.toml 注释
        self.assertEqual(set(sources.enabled(conf)),
                         set(sources.REGISTRY) - {"appstore", "googleplay", "arctic_shift", "rss"})
        self.assertTrue(all(f["name"] and f["url"].startswith("https://") for f in conf.get("rss", [])))

    def test_disabled_source(self):
        self.assertEqual(sources.enabled({"github": {"enabled": False}, "rss": []}), [])


if __name__ == "__main__":
    unittest.main()
