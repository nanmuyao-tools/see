"""入口与编排：抓取 → 抽取 → 存储 → 输出。"""

from __future__ import annotations

import sys

from . import sources, store
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

    print(f"[2/4] 用 {cfg.provider} 抽取 …")
    new = 0
    for item in items:
        if store.already_seen(coll, item.source, item.external_id):
            continue
        try:
            demand = extract(client, item)
        except Exception as e:  # 单条失败不中断整批
            print(f"      跳过 {item.external_id}: {_reason(e)}", file=sys.stderr)
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
