"""统一限速的离线测试：假时钟，不真等、不碰网络。"""

from __future__ import annotations

import email.message
import os
import unittest
import urllib.error
from unittest import mock

from demand_radar import sources
from demand_radar.sources import http


class FakeClock:
    def __init__(self):
        self.t = 100.0
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def limiter():
    c = FakeClock()
    return http.RateLimiter(clock=c, sleep=c.sleep), c


class RateLimiterTest(unittest.TestCase):
    def test_same_host_spaced_by_interval(self):
        lim, c = limiter()
        lim.overrides = {"a.com": 2.0}
        for _ in range(3):
            lim.wait("a.com")
        self.assertEqual(c.slept, [2.0, 2.0])

    def test_hosts_do_not_block_each_other(self):
        lim, c = limiter()
        lim.wait("a.com")
        lim.wait("b.com")
        self.assertEqual(c.slept, [])

    def test_no_wait_if_enough_time_passed(self):
        lim, c = limiter()
        lim.wait("a.com")
        c.t += 5
        lim.wait("a.com")
        self.assertEqual(c.slept, [])

    def test_cooldown_delays_all_later_requests(self):
        lim, c = limiter()
        lim.wait("a.com")
        lim.cooldown("a.com", 30)
        lim.wait("a.com")
        self.assertEqual(c.slept, [30.0])

    def test_known_hosts_and_github_token(self):
        lim, _ = limiter()
        self.assertEqual(lim.interval("www.reddit.com"), 3.0)
        self.assertEqual(lim.interval("unknown.example"), http.DEFAULT_INTERVAL)
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            self.assertEqual(lim.interval("api.github.com"), 6.5)
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x"}):
            self.assertEqual(lim.interval("api.github.com"), 2.5)


class ConfigureTest(unittest.TestCase):
    def tearDown(self):
        http.configure(None)

    def test_sources_toml_http_section(self):
        conf = sources.load_config(os.path.join(os.path.dirname(__file__), "..", "sources.toml"))
        self.assertIn("http", conf)
        self.assertEqual(http.limiter.default, conf["http"]["min_interval"])

    def test_overrides(self):
        http.configure({"min_interval": 3, "hosts": {"blog.example.com": 10}})
        self.assertEqual(http.limiter.interval("blog.example.com"), 10.0)
        self.assertEqual(http.limiter.interval("other.example"), 3.0)
        self.assertEqual(http.limiter.interval("www.reddit.com"), 3.0)  # 内置值不受 min_interval 影响


def http_error(code, retry_after=None):
    h = email.message.Message()
    if retry_after:
        h["Retry-After"] = retry_after
    return urllib.error.HTTPError("u", code, "x", h, None)


class GetTest(unittest.TestCase):
    def setUp(self):
        self.lim, self.clock = limiter()
        self.lim.overrides = {"x.com": 1.0}
        p = mock.patch.object(http, "limiter", self.lim)
        p.start()
        self.addCleanup(p.stop)

    def _resp(self, body=b"ok"):
        r = mock.MagicMock()
        r.__enter__.return_value.read.return_value = body
        return r

    def test_every_request_goes_through_limiter(self):
        with mock.patch.object(http.urllib.request, "urlopen", return_value=self._resp()):
            http.get("https://x.com/a")
            http.get("https://x.com/b")
        self.assertEqual(self.clock.slept, [1.0])

    def test_429_cools_down_host_then_retries(self):
        with mock.patch.object(http.urllib.request, "urlopen",
                               side_effect=[http_error(429, "20"), self._resp()]):
            self.assertEqual(http.get("https://x.com/a"), b"ok")
        self.assertEqual(self.clock.slept, [20.0])

    def test_final_429_still_cools_down_for_next_query(self):
        errs = [http_error(429, "15")] * (http.RETRIES + 1)
        with mock.patch.object(http.urllib.request, "urlopen", side_effect=errs), \
             self.assertRaises(urllib.error.HTTPError):
            http.get("https://x.com/a")
        before = len(self.clock.slept)
        with mock.patch.object(http.urllib.request, "urlopen", return_value=self._resp()):
            http.get("https://x.com/b")
        self.assertEqual(self.clock.slept[before:], [15.0])  # 下一个查询也要等冷却结束

    def test_user_agent_has_contact(self):
        with mock.patch.object(http.urllib.request, "urlopen", return_value=self._resp()) as u:
            http.get("https://x.com/a")
        self.assertIn("github.com/nanmuyao/see", u.call_args[0][0].get_header("User-agent"))


if __name__ == "__main__":
    unittest.main()
