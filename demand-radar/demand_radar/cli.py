"""入口与编排：抓取 → 抽取 → 存储 → 输出。"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

from . import filters, rank, report, sources, store
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
        mark = "✓" if (demand.is_request and demand.buildable) else "·"
        print(f"      {mark} {(demand.need or item.title)[:50]}")
    print(f"      规则过滤 {filtered} 条，抽取 {extracted} 条，失败 {failed} 条")
    return {"filtered": filtered, "extracted": extracted, "failed": failed}


def run(cfg: Config) -> None:
    conf = sources.load_config(cfg.sources_file)
    names = sources.enabled(conf) if cfg.source == "all" else cfg.source.split(",")
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

    print(f"[3/4] 筛选（可信度 ≥ {cfg.min_confidence}）并排序 …")
    rows = rank.sort(store.top_demands(coll, cfg.min_confidence), rank.weights(conf.get("ranking")))

    print(f"[4/4] 写出 {cfg.output_path}（{len(rows)} 条）")
    report.write_markdown(rows, cfg.output_path, cfg.output_language)
    took = store.finish_run(coll, run_id, fetched=store.source_counts(items), fetched_total=len(items),
                            fetch_errors=errors, todo=len(todo), listed=len(rows), **stats)
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
                      f"最早到 {datetime.fromtimestamp(oldest, tz=timezone.utc):%Y-%m-%d}")
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
    since = datetime.now(timezone.utc) - timedelta(days=cfg.days)
    rows = rank.sort(store.recent_demands(coll, cfg.min_confidence, since),
                     rank.weights(conf.get("ranking")))
    path = cfg.digest_output or f"out/digest-{datetime.now():%Y-%m-%d}.md"
    report.write_digest(rows, path, cfg.days, cfg.output_language)
    coll.database.client.close()
    print(f"最近 {cfg.days} 天新增 {len(rows)} 条需求，已写出 {path}")


def main(argv: list[str] | None = None) -> None:
    cfg = Config.from_args(argv)
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
    run(cfg)


if __name__ == "__main__":
    main()
