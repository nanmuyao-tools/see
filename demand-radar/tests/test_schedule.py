"""定时运行的离线测试：下次时刻的计算、每天 run / 每周 digest 的循环、单次失败不退出。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest import mock

from demand_radar import schedule
from demand_radar.config import Config


class NextRunTest(unittest.TestCase):
    def test_today_if_not_yet_passed(self):
        now = datetime(2026, 10, 19, 2, 0)
        self.assertEqual(schedule.next_run(now, "03:00"), datetime(2026, 10, 19, 3, 0))

    def test_tomorrow_if_passed_or_exact(self):
        self.assertEqual(schedule.next_run(datetime(2026, 10, 19, 4, 0), "03:00"), datetime(2026, 10, 20, 3, 0))
        self.assertEqual(schedule.next_run(datetime(2026, 10, 19, 3, 0), "03:00"), datetime(2026, 10, 20, 3, 0))

    def test_bad_time_rejected(self):
        for bad in ("3", "25:00", "03:60", "abc"):
            with self.assertRaises(ValueError):
                schedule.parse_at(bad)


class Clock:
    """假时钟：sleep 直接把时间拨过去。"""

    def __init__(self, start: datetime):
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


class MainLoopTest(unittest.TestCase):
    def _loop(self, cfg, clock, cycles, run=None, digest=None):
        runs, digests = [], []
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            schedule.main_loop(cfg, run or (lambda c: runs.append(clock.now)),
                               digest or (lambda c: digests.append(clock.now)),
                               sleep=clock.sleep, clock=clock, max_cycles=cycles)
        return runs, digests

    def test_daily_run_and_weekly_digest(self):
        clock = Clock(datetime(2026, 10, 18, 2, 30))  # 周日
        cfg = Config(command="schedule", run_at="03:00", digest_weekday=1, run_on_start=True)
        runs, digests = self._loop(cfg, clock, cycles=2)
        self.assertEqual(runs, [datetime(2026, 10, 18, 2, 30),   # 启动先跑一次
                                datetime(2026, 10, 18, 3, 0),
                                datetime(2026, 10, 19, 3, 0)])
        self.assertEqual(digests, [datetime(2026, 10, 19, 3, 0)])  # 只在周一
        self.assertEqual(clock.slept, [1800.0, 86400.0])

    def test_no_run_on_start_and_no_digest(self):
        clock = Clock(datetime(2026, 10, 19, 2, 30))  # 周一
        cfg = Config(command="schedule", run_at="03:00", digest_weekday=0, run_on_start=False)
        runs, digests = self._loop(cfg, clock, cycles=1)
        self.assertEqual(runs, [datetime(2026, 10, 19, 3, 0)])
        self.assertEqual(digests, [])

    def test_failures_do_not_stop_the_loop(self):
        clock = Clock(datetime(2026, 10, 19, 2, 30))
        cfg = Config(command="schedule", run_at="03:00", digest_weekday=1, run_on_start=False)
        attempts = []

        def flaky(c):
            attempts.append(clock.now)
            raise (SystemExit("所有数据源都抓取失败") if len(attempts) == 1 else RuntimeError("boom"))

        runs, digests = self._loop(cfg, clock, cycles=2, run=flaky)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(digests), 1)  # run 失败了周报照出

    def test_bad_run_at_fails_fast(self):
        cfg = Config(command="schedule", run_at="3am")
        with self.assertRaises(ValueError):
            schedule.main_loop(cfg, lambda c: None, lambda c: None, sleep=lambda s: None, max_cycles=0)


class ScheduleConfigTest(unittest.TestCase):
    def test_flags_and_env(self):
        cfg = Config.from_args(["schedule", "--run-at", "18:30", "--digest-weekday", "5",
                                "--no-run-on-start", "--limit", "20", "--days", "14"])
        self.assertEqual((cfg.command, cfg.run_at, cfg.digest_weekday, cfg.run_on_start, cfg.limit, cfg.days),
                         ("schedule", "18:30", 5, False, 20, 14))
        with mock.patch.dict("os.environ", {"DR_RUN_AT": "06:00", "DR_DIGEST_WEEKDAY": "7",
                                            "DR_RUN_ON_START": "0"}):
            cfg = Config.from_args(["schedule"])
        self.assertEqual((cfg.run_at, cfg.digest_weekday, cfg.run_on_start), ("06:00", 7, False))
        self.assertTrue(Config.from_args(["schedule"]).run_on_start)


if __name__ == "__main__":
    unittest.main()
