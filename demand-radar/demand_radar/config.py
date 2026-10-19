"""运行配置：命令行参数 > 环境变量 > 默认值。

环境变量便于 Docker 注入；命令行便于本地覆盖。
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


@dataclass
class Config:
    command: str = "run"  # run 抓取出清单；eval 在标注集上评测抽取质量
    source: str = "all"  # all = sources.toml 里启用的全部源；也可写 github,rss
    sources_file: str = "sources.toml"
    limit: int = 10  # 每个源（RSS 按每个订阅、HN/GitHub 按每个查询）最多抓多少条
    # Instructor 的 provider 标识；换本地/云只改这一处
    provider: str = "ollama/qwen2.5:7b"
    # 本地模型服务地址；容器里指向 ollama 服务
    ollama_base_url: str = "http://localhost:11434/v1"
    min_confidence: float = 0.5
    mongo_uri: str = "mongodb://localhost:27017"
    db_name: str = "demand_radar"
    output_path: str = "out/demands.md"
    eval_set: str = "eval/eval_set.json"  # eval 命令用的标注集
    verbose: bool = False  # eval 命令：逐条打印

    @staticmethod
    def from_args(argv: list[str] | None = None) -> "Config":
        # 默认值先吃环境变量，再被命令行覆盖
        d = Config(
            source=_env("DR_SOURCE", Config.source),
            sources_file=_env("DR_SOURCES_FILE", Config.sources_file),
            limit=int(_env("DR_LIMIT", str(Config.limit))),
            provider=_env("DR_PROVIDER", Config.provider),
            ollama_base_url=_env("OLLAMA_BASE_URL", Config.ollama_base_url),
            min_confidence=float(_env("DR_MIN_CONFIDENCE", str(Config.min_confidence))),
            mongo_uri=_env("MONGO_URI", Config.mongo_uri),
            db_name=_env("DR_DB_NAME", Config.db_name),
            output_path=_env("DR_OUTPUT", Config.output_path),
            eval_set=_env("DR_EVAL_SET", Config.eval_set),
        )

        p = argparse.ArgumentParser(prog="demand_radar")
        sub = p.add_subparsers(dest="command", required=True)

        run = sub.add_parser("run", help="抓取 → 抽取 → 存储 → 输出清单")
        run.add_argument("--source", default=d.source)
        run.add_argument("--sources-file", dest="sources_file", default=d.sources_file)
        run.add_argument("--limit", type=int, default=d.limit)
        run.add_argument("--min-confidence", type=float, default=d.min_confidence)
        run.add_argument("--mongo-uri", dest="mongo_uri", default=d.mongo_uri)
        run.add_argument("--db-name", dest="db_name", default=d.db_name)
        run.add_argument("--output", dest="output_path", default=d.output_path)

        ev = sub.add_parser("eval", help="在标注集上评测抽取质量，不写库")
        ev.add_argument("--eval-set", dest="eval_set", default=d.eval_set)
        ev.add_argument("--min-confidence", type=float, default=d.min_confidence)
        ev.add_argument("--verbose", "-v", action="store_true", default=d.verbose)

        # 两个命令共用的模型参数
        for sp in (run, ev):
            sp.add_argument("--provider", default=d.provider)
            sp.add_argument("--ollama-base-url", dest="ollama_base_url", default=d.ollama_base_url)

        ns = p.parse_args(argv)
        return Config(
            command=ns.command,
            source=getattr(ns, "source", d.source),
            sources_file=getattr(ns, "sources_file", d.sources_file),
            limit=getattr(ns, "limit", d.limit),
            provider=ns.provider,
            ollama_base_url=ns.ollama_base_url,
            min_confidence=getattr(ns, "min_confidence", d.min_confidence),
            mongo_uri=getattr(ns, "mongo_uri", d.mongo_uri),
            db_name=getattr(ns, "db_name", d.db_name),
            output_path=getattr(ns, "output_path", d.output_path),
            eval_set=getattr(ns, "eval_set", d.eval_set),
            verbose=getattr(ns, "verbose", d.verbose),
        )
