"""运行配置：命令行参数 > 环境变量 > 默认值。

环境变量便于 Docker 注入；命令行便于本地覆盖。
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from importlib import resources


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _bundled(path: str, default: str) -> str:
    """默认的数据文件（sources.toml、评测集）先在当前目录找，找不到再用安装包自带的那份。

    在仓库里或 Docker 里运行时当前目录就有这些文件，行为不变；pip 安装后在任意目录运行，
    用的是打包进去的默认配置。用户显式指定的路径（和默认值不同）不做回退，找不到就照常报错。
    """
    if path != default or os.path.exists(path):
        return path
    packaged = resources.files("demand_radar") / "defaults" / os.path.basename(default)
    return str(packaged) if packaged.is_file() else path


@dataclass
class Config:
    command: str = "run"  # run 抓取出清单；eval 在标注集上评测抽取质量
    source: str = "all"  # all = sources.toml 里启用的全部源；也可写 github,rss
    sources_file: str = "sources.toml"
    limit: int = 10  # 每个源（RSS 按每个订阅、HN/GitHub 按每个查询）最多抓多少条
    # Instructor 的 provider 标识；换本地/云只改这一处
    provider: str = "ollama/qwen3:4b-instruct-2507-q4_K_M"
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
    # 相似需求合并（v0.3，见 cluster.py）
    cluster: bool = False  # run / schedule：抽取完顺手向量化并合并相似需求
    embed_model: str = "qwen3-embedding:0.6b"  # 嵌入模型名，走 /v1/embeddings；Ollama 可直接 pull。要懂中文，选型见 cluster.py
    embed_base_url: str = ""  # 嵌入服务地址；空 = 同 ollama_base_url。Mac GPU 路线 mlx-lm 不提供嵌入接口，可单独指向 Ollama
    cluster_threshold: float = 0.78  # 余弦相似度 ≥ 此值视为同一需求（按 23 对标注定的，见 cluster.py）
    rebuild: bool = False  # cluster 命令：丢掉已有簇重新归（调阈值后用）
    cluster_eval: bool = False  # cluster 命令：不动库，在标注对上评测合并判断
    eval_pairs: str = "eval/cluster_pairs.json"  # cluster --eval 用的标注对

    # recommend 命令：按本机配置从档位表推荐模型
    models_file: str = "models.toml"
    tier: str = ""  # 手动指定档位 id
    list_tiers: bool = False
    as_json: bool = False
    write_env: bool = False
    env_file: str = ".env"
    hw_apple: bool | None = None  # 覆盖检测结果：在 Docker 里看不到 Mac 的 GPU 时用
    hw_ram_gb: float = 0
    hw_vram_gb: float | None = None

    host: str = "0.0.0.0"  # serve 命令：监听地址；容器里要 0.0.0.0 才能从宿主机访问
    port: int = 8501  # serve 命令：看板端口

    @staticmethod
    def from_args(argv: list[str] | None = None) -> Config:
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
            cluster=_env("DR_CLUSTER", "0").lower() in ("1", "true", "yes"),
            embed_model=_env("DR_EMBED_MODEL", Config.embed_model),
            embed_base_url=_env("DR_EMBED_BASE_URL", Config.embed_base_url),
            cluster_threshold=float(_env("DR_CLUSTER_THRESHOLD", str(Config.cluster_threshold))),
            eval_pairs=_env("DR_EVAL_PAIRS", Config.eval_pairs),

            host=_env("DR_HOST", Config.host),
            port=int(_env("DR_PORT", str(Config.port))),
        )

        p = argparse.ArgumentParser(prog="demand_radar")
        from . import __version__
        p.add_argument("--version", action="version", version=f"demand-radar {__version__}")
        sub = p.add_subparsers(dest="command", required=True)

        run = sub.add_parser("run", help="抓取 → 抽取 → 存储 → 输出清单")
        ev = sub.add_parser("eval", help="在标注集上评测抽取质量，不写库")
        dg = sub.add_parser("digest", help="汇总最近 N 天新增的需求，按领域分组输出周报")
        bf = sub.add_parser("backfill", help="按页往回拉一个源的历史内容，可中断后继续")
        sc = sub.add_parser("schedule", help="常驻：每天定时 run，每周定时 digest")
        cl = sub.add_parser("cluster", help="向量化并合并相似需求，重写清单；不调抽取模型")

        sv = sub.add_parser("serve", help="常驻：Web 看板，直接读库，不调模型")
        rc = sub.add_parser("recommend", help="检测本机配置，推荐抽取模型和嵌入模型，可写进 .env")

        for sp in (run, sc):
            sp.add_argument("--source", default=d.source)
            sp.add_argument("--limit", type=int, default=d.limit)
            sp.add_argument("--output", dest="output_path", default=d.output_path)
        for sp in (run, bf, sc):
            sp.add_argument("--workers", type=int, default=d.workers, help="并发调用模型的线程数")
        for sp in (run, ev, dg, sc, cl):
            sp.add_argument("--min-confidence", type=float, default=d.min_confidence)

        # 相似需求合并：run / schedule 用 --cluster 开启，cluster 命令单独跑
        for sp in (run, sc):
            sp.add_argument("--cluster", action="store_true", default=d.cluster,
                            help="抽取完顺手向量化并合并相似需求（需要嵌入模型）")
        for sp in (run, sc, cl):
            sp.add_argument("--embed-model", dest="embed_model", default=d.embed_model,
                            help="嵌入模型名，走 /v1/embeddings")
            sp.add_argument("--embed-base-url", dest="embed_base_url", default=d.embed_base_url,
                            help="嵌入服务地址，默认同 --base-url")
            sp.add_argument("--threshold", dest="cluster_threshold", type=float, default=d.cluster_threshold,
                            help="余弦相似度 ≥ 此值视为同一需求")
        cl.add_argument("--output", dest="output_path", default=d.output_path)
        cl.add_argument("--rebuild", action="store_true", default=False, help="丢掉已有簇重新归")
        cl.add_argument("--eval", dest="cluster_eval", action="store_true", default=False,
                        help="不动库，在标注对上评测「是否同一需求」的判断")
        cl.add_argument("--pairs", dest="eval_pairs", default=d.eval_pairs, help="--eval 用的标注对")
        cl.add_argument("--verbose", "-v", action="store_true", default=d.verbose)

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

        sv.add_argument("--host", default=d.host, help="监听地址，容器里保持 0.0.0.0")
        sv.add_argument("--port", type=int, default=d.port, help="看板端口")
        # 看板只读库：数据库、筛选阈值、权重文件三项和 digest 一样，没有模型参数
        sv.add_argument("--min-confidence", type=float, default=d.min_confidence)
        sv.add_argument("--mongo-uri", dest="mongo_uri", default=d.mongo_uri)
        sv.add_argument("--db-name", dest="db_name", default=d.db_name)
        sv.add_argument("--sources-file", dest="sources_file", default=d.sources_file)

        # 调模型的命令共用
        for sp in (run, ev, bf, sc):
            sp.add_argument("--provider", default=d.provider)
        for sp in (run, ev, bf, sc, cl):
            sp.add_argument("--base-url", "--ollama-base-url", dest="ollama_base_url",
                            default=d.ollama_base_url,
                            help="本地模型服务地址（ollama/ 与 local/ 两种 provider 用）")
        # 读写数据库的命令共用
        for sp in (run, dg, bf, sc, cl):
            sp.add_argument("--mongo-uri", dest="mongo_uri", default=d.mongo_uri)
            sp.add_argument("--db-name", dest="db_name", default=d.db_name)
        for sp in (run, ev, dg, bf, sc, cl):
            sp.add_argument("--sources-file", dest="sources_file", default=d.sources_file)
            sp.add_argument("--lang", dest="output_language", choices=("zh", "en"),
                            default=d.output_language, help="输出语言")

        rc.add_argument("--models-file", dest="models_file", default=d.models_file, help="模型档位表")
        rc.add_argument("--tier", default="", help="不按检测结果，直接用这个档位")
        rc.add_argument("--list", dest="list_tiers", action="store_true", help="列出全部档位，标出本机满足哪些")
        rc.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON")
        rc.add_argument("--write-env", dest="write_env", action="store_true",
                        help="把推荐的模型写进 .env，之后 docker compose up 自动使用")
        rc.add_argument("--env-file", dest="env_file", default=d.env_file)
        apple = rc.add_mutually_exclusive_group()
        apple.add_argument("--apple", dest="hw_apple", action="store_true", default=None,
                           help="按 Apple 芯片的 Mac 推荐（在 Docker 里检测不到时用）")
        apple.add_argument("--no-apple", dest="hw_apple", action="store_false")
        rc.add_argument("--ram-gb", dest="hw_ram_gb", type=float, default=0, help="按这么多内存推荐")
        rc.add_argument("--vram-gb", dest="hw_vram_gb", type=float, default=None,
                        help="按这么多 NVIDIA 显存推荐，0 表示没有")

        ns = p.parse_args(argv)
        return Config(
            command=ns.command,
            source=getattr(ns, "source", d.source),
            sources_file=_bundled(getattr(ns, "sources_file", d.sources_file), Config.sources_file),
            limit=getattr(ns, "limit", d.limit),
            provider=getattr(ns, "provider", d.provider),
            ollama_base_url=getattr(ns, "ollama_base_url", d.ollama_base_url),
            min_confidence=getattr(ns, "min_confidence", d.min_confidence),
            workers=getattr(ns, "workers", d.workers),
            mongo_uri=getattr(ns, "mongo_uri", d.mongo_uri),
            db_name=getattr(ns, "db_name", d.db_name),
            output_path=getattr(ns, "output_path", d.output_path),
            eval_set=_bundled(getattr(ns, "eval_set", d.eval_set), Config.eval_set),
            verbose=getattr(ns, "verbose", d.verbose),
            output_language=getattr(ns, "output_language", d.output_language),
            days=getattr(ns, "days", d.days),
            digest_output=getattr(ns, "digest_output", d.digest_output),
            pages=getattr(ns, "pages", d.pages),
            run_at=getattr(ns, "run_at", d.run_at),
            digest_weekday=getattr(ns, "digest_weekday", d.digest_weekday),
            run_on_start=getattr(ns, "run_on_start", d.run_on_start),
            cluster=getattr(ns, "cluster", d.cluster),
            embed_model=getattr(ns, "embed_model", d.embed_model),
            embed_base_url=getattr(ns, "embed_base_url", d.embed_base_url),
            cluster_threshold=getattr(ns, "cluster_threshold", d.cluster_threshold),
            rebuild=getattr(ns, "rebuild", False),
            cluster_eval=getattr(ns, "cluster_eval", False),
            eval_pairs=_bundled(getattr(ns, "eval_pairs", d.eval_pairs), Config.eval_pairs),

            models_file=_bundled(getattr(ns, "models_file", d.models_file), Config.models_file),
            tier=getattr(ns, "tier", ""),
            list_tiers=getattr(ns, "list_tiers", False),
            as_json=getattr(ns, "as_json", False),
            write_env=getattr(ns, "write_env", False),
            env_file=getattr(ns, "env_file", d.env_file),
            hw_apple=getattr(ns, "hw_apple", None),
            hw_ram_gb=getattr(ns, "hw_ram_gb", 0),
            hw_vram_gb=getattr(ns, "hw_vram_gb", None),

            host=getattr(ns, "host", d.host),
            port=getattr(ns, "port", d.port),
        )
