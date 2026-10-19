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
    provider: str = "ollama/qwen2.5:3b"
    # 本地模型服务地址；容器里指向 ollama 服务
    ollama_base_url: str = "http://localhost:11434/v1"
    min_confidence: float = 0.5
    workers: int = 2  # 同时发给模型的请求数；Ollama 默认串行处理，要配合 OLLAMA_NUM_PARALLEL 才有效果
    mongo_uri: str = "mongodb://localhost:27017"
    db_name: str = "demand_radar"
    output_path: str = "out/demands.md"
    eval_set: str = "eval/eval_set.json"  # eval 命令用的标注集
    output_language: str = "zh"  # 需求描述用的语言：zh 中文 / en 英文；摘录始终保留原文
    verbose: bool = False  # eval 命令：逐条打印
    days: int = 7  # digest 命令：汇总最近多少天新增的需求
    digest_output: str = ""  # digest 命令输出路径；空则为 out/digest-<日期>.md
    pages: int = 10  # backfill 命令：本次最多往回拉多少页（各查询轮流）
    run_at: str = "03:00"  # schedule 命令：每天几点跑 run，按本机 / 容器时钟（容器默认 UTC）
    digest_weekday: int = 1  # schedule 命令：ISO 周几出周报，1 = 周一 … 7 = 周日，0 = 不出
    run_on_start: bool = True  # schedule 命令：启动时先跑一次，不等到点

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
            workers=int(_env("DR_WORKERS", str(Config.workers))),
            mongo_uri=_env("MONGO_URI", Config.mongo_uri),
            db_name=_env("DR_DB_NAME", Config.db_name),
            output_path=_env("DR_OUTPUT", Config.output_path),
            eval_set=_env("DR_EVAL_SET", Config.eval_set),
            output_language=_env("DR_OUTPUT_LANGUAGE", Config.output_language),
            run_at=_env("DR_RUN_AT", Config.run_at),
            digest_weekday=int(_env("DR_DIGEST_WEEKDAY", str(Config.digest_weekday))),
            run_on_start=_env("DR_RUN_ON_START", "1").lower() in ("1", "true", "yes"),
        )

        p = argparse.ArgumentParser(prog="demand_radar")
        sub = p.add_subparsers(dest="command", required=True)

        run = sub.add_parser("run", help="抓取 → 抽取 → 存储 → 输出清单")
        ev = sub.add_parser("eval", help="在标注集上评测抽取质量，不写库")
        dg = sub.add_parser("digest", help="汇总最近 N 天新增的需求，按领域分组输出周报")
        bf = sub.add_parser("backfill", help="按页往回拉一个源的历史内容，可中断后继续")
        sc = sub.add_parser("schedule", help="常驻：每天定时 run，每周定时 digest")

        for sp in (run, sc):
            sp.add_argument("--source", default=d.source)
            sp.add_argument("--limit", type=int, default=d.limit)
            sp.add_argument("--output", dest="output_path", default=d.output_path)
        for sp in (run, bf, sc):
            sp.add_argument("--workers", type=int, default=d.workers, help="并发调用模型的线程数")
        for sp in (run, ev, dg, sc):
            sp.add_argument("--min-confidence", type=float, default=d.min_confidence)

        ev.add_argument("--eval-set", dest="eval_set", default=d.eval_set)
        ev.add_argument("--verbose", "-v", action="store_true", default=d.verbose)

        for sp in (dg, sc):
            sp.add_argument("--days", type=int, default=d.days, help="周报汇总最近多少天")
        dg.add_argument("--output", dest="digest_output", default=d.digest_output)

        bf.add_argument("--source", required=True, help="一次一个源：softwarerecs / hn_algolia / github")
        bf.add_argument("--pages", type=int, default=d.pages, help="本次最多拉多少页，各查询轮流")
        bf.add_argument("--limit", type=int, default=100,
                        help="每页条数（StackExchange、GitHub 上限 100，HN 1000）")

        sc.add_argument("--run-at", dest="run_at", default=d.run_at, help="每天几点跑 run，HH:MM")
        sc.add_argument("--digest-weekday", dest="digest_weekday", type=int, default=d.digest_weekday,
                        help="ISO 周几出周报：1 周一 … 7 周日，0 不出")
        sc.add_argument("--no-run-on-start", dest="run_on_start", action="store_false",
                        default=d.run_on_start, help="启动时不先跑一次，等到点再跑")

        # 调模型的命令共用
        for sp in (run, ev, bf, sc):
            sp.add_argument("--provider", default=d.provider)
            sp.add_argument("--ollama-base-url", dest="ollama_base_url", default=d.ollama_base_url)
        # 读写数据库的命令共用
        for sp in (run, dg, bf, sc):
            sp.add_argument("--mongo-uri", dest="mongo_uri", default=d.mongo_uri)
            sp.add_argument("--db-name", dest="db_name", default=d.db_name)
        for sp in (run, ev, dg, bf, sc):
            sp.add_argument("--sources-file", dest="sources_file", default=d.sources_file)
            sp.add_argument("--lang", dest="output_language", choices=("zh", "en"),
                            default=d.output_language, help="输出语言")

        ns = p.parse_args(argv)
        return Config(
            command=ns.command,
            source=getattr(ns, "source", d.source),
            sources_file=getattr(ns, "sources_file", d.sources_file),
            limit=getattr(ns, "limit", d.limit),
            provider=getattr(ns, "provider", d.provider),
            ollama_base_url=getattr(ns, "ollama_base_url", d.ollama_base_url),
            min_confidence=getattr(ns, "min_confidence", d.min_confidence),
            workers=getattr(ns, "workers", d.workers),
            mongo_uri=getattr(ns, "mongo_uri", d.mongo_uri),
            db_name=getattr(ns, "db_name", d.db_name),
            output_path=getattr(ns, "output_path", d.output_path),
            eval_set=getattr(ns, "eval_set", d.eval_set),
            verbose=getattr(ns, "verbose", d.verbose),
            output_language=getattr(ns, "output_language", d.output_language),
            days=getattr(ns, "days", d.days),
            digest_output=getattr(ns, "digest_output", d.digest_output),
            pages=getattr(ns, "pages", d.pages),
            run_at=getattr(ns, "run_at", d.run_at),
            digest_weekday=getattr(ns, "digest_weekday", d.digest_weekday),
            run_on_start=getattr(ns, "run_on_start", d.run_on_start),
        )
