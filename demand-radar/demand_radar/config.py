"""运行配置：命令行参数解析与默认值集中在此。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass
class Config:
    source: str = "hackernews"
    limit: int = 30
    # Instructor 的 provider 标识；换本地/云只改这一处
    provider: str = "ollama/qwen2.5:7b"
    min_confidence: float = 0.5
    db_path: str = "data/demands.db"
    output_path: str = "out/demands.md"

    @staticmethod
    def from_args(argv: list[str] | None = None) -> "Config":
        p = argparse.ArgumentParser(prog="demand_radar")
        sub = p.add_subparsers(dest="command", required=True)

        run = sub.add_parser("run", help="抓取 → 抽取 → 存储 → 输出清单")
        run.add_argument("--source", default=Config.source)
        run.add_argument("--limit", type=int, default=Config.limit)
        run.add_argument("--provider", default=Config.provider)
        run.add_argument("--min-confidence", type=float, default=Config.min_confidence)
        run.add_argument("--db", dest="db_path", default=Config.db_path)
        run.add_argument("--output", dest="output_path", default=Config.output_path)

        ns = p.parse_args(argv)
        return Config(
            source=ns.source,
            limit=ns.limit,
            provider=ns.provider,
            min_confidence=ns.min_confidence,
            db_path=ns.db_path,
            output_path=ns.output_path,
        )
