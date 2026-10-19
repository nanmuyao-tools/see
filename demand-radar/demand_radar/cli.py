"""入口与编排：抓取 → 抽取 → 存储 → 输出。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from . import filters, rank, report, sources, store
from .config import Config
from .extract import build_client, extract


def _reason(e: Exception) -> str:
    """一行可读的失败原因。instructor 的异常带完整重试记录，取最后一次的错误。"""
    s = str(e)
    if "<last_exception>" in s:
        s = s.split("<last_exception>", 1)[1].split("</last_exception>", 1)[0]
    lines = [ln.strip() for ln in s.splitlines() if ln.strip() and "errors.pydantic.dev" not in ln]
    return " / ".join(lines)[:200] or type(e).__name__


def run(cfg: Config) -> None:
    conf = sources.load_config(cfg.sources_file)
    names = sources.enabled(conf) if cfg.source == "all" else cfg.source.split(",")
    print(f"[1/4] 抓取 {', '.join(names)}，每个源/查询最多 {cfg.limit} 条 …")
    items, errors = sources.fetch_all(names, cfg.limit, conf)
    for name, err in errors.items():
        print(f"      ✗ {name} 抓取失败：{err}", file=sys.stderr)
    if not items:
        sys.exit("所有数据源都没抓到内容，请检查网络或 sources.toml")
    print(f"      抓到 {len(items)} 条")

    coll = store.connect(cfg.mongo_uri, cfg.db_name)
    client = build_client(cfg.provider, cfg.ollama_base_url)
    fconf = conf.get("filters") or {}

    todo = [it for it in items if store.should_process(coll, it)]
    print(f"[2/4] 新内容 {len(todo)} 条，先过规则，再用 {cfg.provider} 抽取 …")
    new = filtered = failed = 0
    for item in todo:
        reason = filters.check(item, fconf)
        if reason:
            store.save_filtered(coll, item, reason)
            filtered += 1
            continue
        try:
            demand = extract(client, item, cfg.output_language)
        except Exception as e:  # 单条失败不中断整批，记下来下次重试
            n = store.record_failure(coll, item, _reason(e))
            failed += 1
            print(f"      跳过 {item.external_id}（第 {n} 次失败）: {_reason(e)}", file=sys.stderr)
            continue
        store.save(coll, item, demand)
        new += 1
        mark = "✓" if (demand.is_request and demand.buildable and not demand.harmful) else "·"
        print(f"      {mark} {(demand.need or item.title)[:50]}")
    print(f"      规则过滤 {filtered} 条，抽取 {new} 条，失败 {failed} 条")

    print(f"[3/4] 筛选（可信度 ≥ {cfg.min_confidence}）并排序 …")
    rows = rank.sort(store.top_demands(coll, cfg.min_confidence), rank.weights(conf.get("ranking")))

    print(f"[4/4] 写出 {cfg.output_path}（{len(rows)} 条）")
    report.write_markdown(rows, cfg.output_path, cfg.output_language)
    coll.database.client.close()
    print("完成。")


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
    run(cfg)


if __name__ == "__main__":
    main()
