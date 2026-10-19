"""定时运行：每天固定时刻跑一次 run，每周固定的一天跑完 run 再出周报。

自己睡到点再跑，不依赖 cron：容器里没有 cron，用户也不用再装一个调度器。
单次 run / digest 的异常都接住，调度循环不能因为一次失败就退出。
时刻按本机时钟算（容器内默认 UTC）。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from .config import Config


def parse_at(at: str) -> tuple[int, int]:
    """HH:MM → (时, 分)。写错了立刻报，不要睡到明天才发现。"""
    try:
        h, m = (int(x) for x in at.split(":"))
    except ValueError:
        raise ValueError(f"--run-at 要写成 HH:MM，收到 {at!r}") from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"--run-at 时刻不合法：{at!r}")
    return h, m


def next_run(now: datetime, at: str) -> datetime:
    """下一个 HH:MM：今天还没到就今天，到了就明天。"""
    h, m = parse_at(at)
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def _guarded(label: str, fn: Callable[[Config], None], cfg: Config) -> None:
    try:
        fn(cfg)
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit) as e:  # run 在全部源抓取失败时会 sys.exit，也不能让循环退出
        print(f"[schedule] {label} 失败：{e}", file=sys.stderr, flush=True)


def main_loop(cfg: Config, run: Callable[[Config], None], digest: Callable[[Config], None], *,
              sleep: Callable[[float], None] = time.sleep,
              clock: Callable[[], datetime] = datetime.now,
              max_cycles: int | None = None) -> None:
    """run_on_start 先跑一次；之后每天 run_at 跑 run，那天是 digest_weekday 就再出周报。

    sleep / clock / max_cycles 只为测试注入，正常运行一直循环到进程被停掉。
    """
    parse_at(cfg.run_at)
    if cfg.run_on_start:
        _guarded("run", run, cfg)
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        nxt = next_run(clock(), cfg.run_at)
        with_digest = nxt.isoweekday() == cfg.digest_weekday
        print(f"[schedule] 下次运行 {nxt:%Y-%m-%d %H:%M}" + ("，并出周报" if with_digest else ""), flush=True)
        sleep(max(0.0, (nxt - clock()).total_seconds()))
        _guarded("run", run, cfg)
        if with_digest:
            _guarded("digest", digest, cfg)
        cycles += 1
