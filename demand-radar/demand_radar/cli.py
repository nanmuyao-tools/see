"""入口与编排：抓取 → 抽取 → 存储 → 输出。"""

from __future__ import annotations

import sys

from . import sources, store
from .config import Config
from .extract import build_client, extract


def run(cfg: Config) -> None:
    print(f"[1/4] 抓取 {cfg.source}，最多 {cfg.limit} 条 …")
    items = sources.fetch(cfg.source, cfg.limit)
    print(f"      抓到 {len(items)} 条")

    coll = store.connect(cfg.mongo_uri, cfg.db_name)
    client = build_client(cfg.provider, cfg.ollama_base_url)

    print(f"[2/4] 用 {cfg.provider} 抽取 …")
    new = 0
    for item in items:
        if store.already_seen(coll, item.source, item.external_id):
            continue
        try:
            demand = extract(client, item)
        except Exception as e:  # 单条失败不中断整批
            print(f"      跳过 {item.external_id}: {e}", file=sys.stderr)
            continue
        store.save(coll, item, demand)
        new += 1
        mark = "✓" if (demand.is_request and demand.buildable) else "·"
        print(f"      {mark} {(demand.need or item.title)[:50]}")
    print(f"      新增 {new} 条")

    print(f"[3/4] 过滤（可信度 ≥ {cfg.min_confidence}）…")
    rows = store.top_demands(coll, cfg.min_confidence)

    print(f"[4/4] 写出 {cfg.output_path}（{len(rows)} 条）")
    from .report import write_markdown
    write_markdown(rows, cfg.output_path)
    coll.database.client.close()
    print("完成。")


def main(argv: list[str] | None = None) -> None:
    cfg = Config.from_args(argv)
    run(cfg)


if __name__ == "__main__":
    main()
