"""入口与编排：抓取 → 抽取 → 存储 → 输出；另有只读库的 digest / serve。"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta

from . import cluster, filters, rank, report, sources, store
from .config import Config
from .extract import build_client, extract_many
from .models import SourceItem
from .sources.http import QuotaExhausted


def _reason(e: Exception) -> str:
    """一行可读的失败原因。instructor 的异常带完整重试记录，取最后一次的错误。"""
    s = str(e)
    if "<last_exception>" in s:
        s = s.split("<last_exception>", 1)[1].split("</last_exception>", 1)[0]
    lines = [ln.strip() for ln in s.splitlines() if ln.strip() and "errors.pydantic.dev" not in ln]
    return " / ".join(lines)[:200] or type(e).__name__


def _todo(coll, items: list[SourceItem]) -> list[SourceItem]:
    """这次要处理的：新抓到且没见过的 + 上次失败待重试的。两边可能重叠，以新抓到的为准。"""
    seen: dict[tuple[str, str], SourceItem] = {}
    for it in [it for it in items if store.should_process(coll, it)] + store.retry_items(coll):
        seen.setdefault((it.source, it.external_id), it)
    return list(seen.values())


def _process(coll, client, todo: list[SourceItem], cfg: Config, fconf: dict) -> dict[str, int]:
    """先过规则，再并发抽取、在主线程入库。单条失败不中断整批。返回各步计数。"""
    extracted = filtered = failed = 0
    to_extract: list[SourceItem] = []
    for item in todo:
        reason = filters.check(item, fconf)
        if reason:
            store.save_filtered(coll, item, reason)
            filtered += 1
        else:
            to_extract.append(item)
    for item, demand, err in extract_many(client, to_extract, cfg.output_language, cfg.workers):
        if err is not None:  # 记下来下次重试
            n = store.record_failure(coll, item, _reason(err))
            failed += 1
            print(f"      跳过 {item.external_id}（第 {n} 次失败）: {_reason(err)}", file=sys.stderr)
            continue
        store.save(coll, item, demand)
        extracted += 1
        if demand.is_request and demand.buildable:
            mark = "?" if demand.review_cue else "✓"  # ? 待人工确认
        else:
            mark = "·"
        print(f"      {mark} {(demand.need or item.title)[:50]}")
    print(f"      规则过滤 {filtered} 条，抽取 {extracted} 条，失败 {failed} 条")
    return {"filtered": filtered, "extracted": extracted, "failed": failed}


def _embedder(cfg: Config):
    return cluster.embedder(cfg.embed_base_url or cfg.ollama_base_url, cfg.embed_model)


def _cluster_step(coll, cfg: Config, w: dict[str, float]) -> dict:
    """run --cluster：抽取完顺手向量化 + 合并。嵌入服务挂了只告警不中断——清单照出，簇下次再补。
    返回写进运行记录的计数（加 cluster_ 前缀，和抽取的计数区分开）。"""
    print(f"      向量化并合并相似需求（{cfg.embed_model}，阈值 {cfg.cluster_threshold}）…")
    try:
        stats = cluster.sync(coll, _embedder(cfg), cfg.embed_model, cfg.cluster_threshold, w, cfg.min_confidence)
    except Exception as e:
        print(f"      ✗ 合并失败，清单按未合并输出：{_reason(e)}", file=sys.stderr)
        return {"cluster_error": _reason(e)}
    print(f"      向量化 {stats['embedded']} 条，归簇 {stats['assigned']} 条，新簇 {stats['new_clusters']} 个")
    return {f"cluster_{k}": v for k, v in stats.items()}


def _grouped(coll, rows: list[dict], w: dict[str, float]) -> list[dict]:
    """有簇信息就按簇折叠（每簇一条 + 其余成员的链接）；没跑过 cluster 的库一行都没有 cluster_id，原样返回。"""
    cids = {r.get("cluster_id") for r in rows} - {None}
    if not cids:
        return rows
    clusters = [c for c in store.load_clusters(coll) if c["_id"] in cids]
    ids = {i for c in clusters for i in c["members"]}
    return cluster.group(rows, clusters, store.member_docs(coll, ids), w)


def run(cfg: Config) -> None:
    conf = sources.load_config(cfg.sources_file)
    names = sources.enabled(conf) if cfg.source == "all" else cfg.source.split(",")
    for line in sources.notices(names):  # 用到了灰色或要自己判断的源，每次都提醒一句：这是用户自己开的
        print(f"[提示] {line}", file=sys.stderr)
    coll = store.connect(cfg.mongo_uri, cfg.db_name)
    run_id = store.start_run(coll, "run", sources=names, limit=cfg.limit, provider=cfg.provider,
                             language=cfg.output_language)
    cursors = store.load_cursors(coll)
    resumed = sum(len(cursors.get(n, {})) for n in names)
    print(f"[1/4] 抓取 {', '.join(names)}，每个源/查询最多 {cfg.limit} 条"
          + (f"，{resumed} 个查询从上次位置继续" if resumed else "") + " …")
    items, errors = sources.fetch_all(names, cfg.limit, conf, cursors)
    for name, err in errors.items():
        print(f"      ✗ {name} 抓取失败：{err}", file=sys.stderr)
    if names and len(errors) == len(names):
        store.finish_run(coll, run_id, status="failed", fetch_errors=errors)
        sys.exit("所有数据源都抓取失败，请检查网络或 sources.toml")
    # 增量抓取后「没有新内容」是平常事，不是错误；照常重试失败条目、重新出清单
    print(f"      抓到 {len(items)} 条" + ("" if items else "（上次之后没有新内容）"))

    client = build_client(cfg.provider, cfg.ollama_base_url)
    todo = _todo(coll, items)
    print(f"[2/4] 待处理 {len(todo)} 条（含上次失败待重试的），先过规则，"
          f"再用 {cfg.provider} 抽取（{cfg.workers} 个并发）…")
    stats = _process(coll, client, todo, cfg, conf.get("filters") or {})
    store.save_cursors(coll, cursors)  # 这批全处理完才记游标；中途崩溃就不记，下次重抓

    w = rank.weights(conf.get("ranking"))
    cstats = _cluster_step(coll, cfg, w) if cfg.cluster else {}
    print(f"[3/4] 筛选（可信度 ≥ {cfg.min_confidence}）并排序 …")
    rows = _grouped(coll, rank.sort(store.top_demands(coll, cfg.min_confidence), w), w)
    held = rank.sort(store.held_demands(coll, cfg.min_confidence), w)

    print(f"[4/4] 写出 {cfg.output_path}（{len(rows)} 条，另有 {len(held)} 条待人工确认）")
    report.write_markdown(rows, cfg.output_path, cfg.output_language, held)
    took = store.finish_run(coll, run_id, fetched=store.source_counts(items), fetched_total=len(items),
                            fetch_errors=errors, todo=len(todo), listed=len(rows), held=len(held),
                            **stats, **cstats)
    coll.database.client.close()
    print(f"完成，耗时 {took:.0f} 秒。运行记录在 MongoDB 的 runs 集合。")


def backfill(cfg: Config) -> None:
    """按页往回拉一个源的历史内容。每页处理完就记进度，中断后从上次位置继续。

    各查询轮流拉，--pages 是本次总预算：几个查询各拉几页就停，下次再接着拉。
    拉到空页视为到头；之后再运行会跳过它。
    """
    conf = sources.load_config(cfg.sources_file)
    try:
        mod = sources.backfill_module(cfg.source)
    except ValueError as e:
        sys.exit(str(e))
    sconf = conf.get(cfg.source) or {}
    coll = store.connect(cfg.mongo_uri, cfg.db_name)
    progress = {u: store.load_backfill(coll, cfg.source, u) for u in mod.units(sconf)}
    pending = [u for u, p in progress.items() if not p["done"]]
    if not pending:
        print(f"{cfg.source} 的全部查询都已回填到头。")
        coll.database.client.close()
        return

    client = build_client(cfg.provider, cfg.ollama_base_url)
    fconf = conf.get("filters") or {}
    run_id = store.start_run(coll, "backfill", sources=[cfg.source], limit=cfg.limit, pages=cfg.pages,
                             provider=cfg.provider, language=cfg.output_language)
    print(f"回填 {cfg.source}：{len(pending)} 个查询待拉，本次最多 {cfg.pages} 页、每页 {cfg.limit} 条 …")
    totals = {"pages": 0, "fetched": 0, "filtered": 0, "extracted": 0, "failed": 0}
    budget, status = cfg.pages, "done"
    try:
        while budget and pending:
            for unit in list(pending):
                if not budget:
                    break
                budget -= 1  # 失败的页也算预算，否则持续失败会死循环
                p = progress[unit]
                try:
                    items = mod.page_before(sconf, unit, p["oldest"] or None, cfg.limit)
                except QuotaExhausted as e:
                    print(f"      ✗ {e}", file=sys.stderr)
                    status, pending = "quota", []
                    break
                except Exception as e:  # 网络等问题：这一页下次再拉，先换下一个查询
                    print(f"      ✗ {unit[:40]} 抓取失败：{_reason(e)}", file=sys.stderr)
                    continue
                totals["pages"] += 1
                oldest = min((it.created_at for it in items if it.created_at), default=0)
                if not oldest:  # 空页 = 到头；源没给发布时间也没法再往回翻
                    p["done"] = True
                    pending.remove(unit)
                    store.save_backfill(coll, cfg.source, unit, p)
                    print(f"      {unit[:40]}：到头了，共 {p['pages']} 页")
                    continue
                todo = [it for it in items if store.should_process(coll, it)]
                print(f"      {unit[:40]} 第 {p['pages'] + 1} 页：{len(items)} 条，新 {len(todo)} 条，"
                      f"最早到 {datetime.fromtimestamp(oldest, tz=UTC):%Y-%m-%d}")
                stats = _process(coll, client, todo, cfg, fconf)
                p.update(oldest=oldest, pages=p["pages"] + 1)
                store.save_backfill(coll, cfg.source, unit, p)
                totals["fetched"] += len(items)
                for k, v in stats.items():
                    totals[k] += v
                if budget and pending:
                    time.sleep(mod.interval())
    except KeyboardInterrupt:
        status = "interrupted"
        print("\n已中断。拉完的页都已记录，再运行一次从上次位置继续。")
    took = store.finish_run(coll, run_id, status=status, **totals)
    left = [u for u, p in progress.items() if not p["done"]]
    print(f"本次拉了 {totals['pages']} 页 {totals['fetched']} 条，抽取 {totals['extracted']} 条，"
          f"耗时 {took:.0f} 秒。" + (f"还有 {len(left)} 个查询没到头，再运行一次继续。" if left else "全部到头。"))
    coll.database.client.close()


def digest(cfg: Config) -> None:
    """汇总最近 N 天新增的需求，按领域分组写成周报。不调模型。"""
    conf = sources.load_config(cfg.sources_file)
    coll = store.connect(cfg.mongo_uri, cfg.db_name)
    since = datetime.now(UTC) - timedelta(days=cfg.days)
    w = rank.weights(conf.get("ranking"))
    rows = _grouped(coll, rank.sort(store.recent_demands(coll, cfg.min_confidence, since), w), w)
    held = rank.sort(store.held_demands(coll, cfg.min_confidence, since), w)
    path = cfg.digest_output or f"out/digest-{datetime.now():%Y-%m-%d}.md"
    report.write_digest(rows, path, cfg.days, cfg.output_language, held)
    coll.database.client.close()
    print(f"最近 {cfg.days} 天新增 {len(rows)} 条需求，另有 {len(held)} 条待人工确认，已写出 {path}")


def cluster_demands(cfg: Config) -> None:
    """向量化 + 合并相似需求，不调抽取模型；合并完重写清单，效果立刻能看。
    --eval 时不动库，只在标注对上评测「是否同一需求」的判断（需要真实嵌入服务）。"""
    embed = _embedder(cfg)
    if cfg.cluster_eval:
        try:
            code = cluster.evaluate(cfg.eval_pairs, embed, cfg.cluster_threshold, cfg.verbose)
        except OSError as e:
            sys.exit(f"读取标注对失败：{e}")
        raise SystemExit(code)

    conf = sources.load_config(cfg.sources_file)
    coll = store.connect(cfg.mongo_uri, cfg.db_name)
    w = rank.weights(conf.get("ranking"))
    run_id = store.start_run(coll, "cluster", embed_model=cfg.embed_model, threshold=cfg.cluster_threshold,
                             rebuild=cfg.rebuild)
    print(f"[1/2] 向量化并合并相似需求（{cfg.embed_model}，阈值 {cfg.cluster_threshold}"
          + ("，重建全部簇" if cfg.rebuild else "") + "）…")
    try:
        stats = cluster.sync(coll, embed, cfg.embed_model, cfg.cluster_threshold, w, cfg.min_confidence,
                             cfg.rebuild)
    except Exception as e:
        store.finish_run(coll, run_id, status="failed", error=_reason(e))
        sys.exit(f"合并失败：{_reason(e)}\n嵌入服务 {cfg.embed_base_url or cfg.ollama_base_url} 可达吗？"
                 f"模型 {cfg.embed_model} 拉了吗（ollama pull {cfg.embed_model}）？")
    rows = _grouped(coll, rank.sort(store.top_demands(coll, cfg.min_confidence), w), w)
    held = rank.sort(store.held_demands(coll, cfg.min_confidence), w)
    print(f"[2/2] 写出 {cfg.output_path}（{len(rows)} 条，另有 {len(held)} 条待人工确认）")
    report.write_markdown(rows, cfg.output_path, cfg.output_language, held)
    total = len(store.load_clusters(coll))
    took = store.finish_run(coll, run_id, listed=len(rows), clusters=total, **stats)
    coll.database.client.close()
    print(f"向量化 {stats['embedded']} 条，归簇 {stats['assigned']} 条，新簇 {stats['new_clusters']} 个"
          + ("（已重建）" if stats["rebuilt"] else "") + f"，库里共 {total} 个簇，耗时 {took:.0f} 秒。")


def _connect_patiently(cfg: Config, attempts: int = 30):
    """compose 里 web 和 mongo 一起起，mongo 还没就绪就连会失败；等它几十秒，不靠容器反复重启。"""
    for i in range(attempts):
        try:
            return store.connect(cfg.mongo_uri, cfg.db_name)
        except Exception as e:  # pymongo 的 ServerSelectionTimeoutError 等；缺依赖是 SystemExit，照常抛出
            if i == attempts - 1:
                raise
            print(f"等待 MongoDB {cfg.mongo_uri} …（{type(e).__name__}）", file=sys.stderr, flush=True)
            time.sleep(2)


def serve(cfg: Config) -> None:
    """起 Web 看板：直接读库，不调模型。排序权重和出清单一样取自 sources.toml。"""
    import uvicorn

    from .api import create_app

    conf = sources.load_config(cfg.sources_file)
    coll = _connect_patiently(cfg)
    app = create_app(coll, rank.weights(conf.get("ranking")), cfg.min_confidence)
    shown = "localhost" if cfg.host in ("0.0.0.0", "::") else cfg.host
    print(f"看板已启动：http://{shown}:{cfg.port}/ （英文界面加 ?lang=en；接口文档 /api/docs）", flush=True)
    uvicorn.run(app, host=cfg.host, port=cfg.port)


RISK_LABEL = {"open": "开放", "terms": "有条件", "gray": "灰色", "varies": "看订阅"}


def list_sources(cfg: Config) -> None:
    """列出所有数据源。开关在 sources.toml：整源用 enabled，RSS 每个订阅单独用 enabled。"""
    conf = sources.load_config(cfg.sources_file)
    print(f"数据源配置：{cfg.sources_file}（想开关哪个就改它的 enabled）\n")
    for r in sources.describe(conf):
        state = "启用" if r["enabled"] else "关闭"
        print(f"  [{state}] {r['name']:<13} 风险：{RISK_LABEL[r['risk']]}  {r['note']}")
        if r["terms"]:
            print(f"{'':25}条款：{r['terms']}")
        for name, on in r.get("feeds", []):
            print(f"{'':25}- {name}  {'启用' if on else '关闭'}")
    print("\n风险档是我们对条款的理解，不是法律意见；灰色的源默认关闭，开不开由你自己决定。")


def main(argv: list[str] | None = None) -> None:
    cfg = Config.from_args(argv)
    if cfg.command == "sources":
        list_sources(cfg)
        return
    if cfg.command == "eval":
        from . import evaluate
        raise SystemExit(evaluate.run(cfg))
    if cfg.command == "digest":
        digest(cfg)
        return
    if cfg.command == "backfill":
        backfill(cfg)
        return
    if cfg.command == "schedule":
        from . import schedule
        schedule.main_loop(cfg, run, digest)
        return
    if cfg.command == "cluster":
        cluster_demands(cfg)
        return
    if cfg.command == "recommend":
        from . import recommend
        raise SystemExit(recommend.run(cfg))
    if cfg.command == "serve":
        serve(cfg)
        return
    run(cfg)


if __name__ == "__main__":
    main()
