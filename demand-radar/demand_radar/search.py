"""语义检索：用一句话在已入库的需求里找最像的，给 agent 当 RAG 的检索层。

不需要向量数据库：向量已经存在 demands 文档的 embedding 字段（见 cluster.py），几千条 × 1024 维
在内存里暴力算余弦只要几毫秒。查询用和库里同一个嵌入模型向量化——模型不同，分数没有意义，
所以只在 embedding_model 相同的文档里找。

只读：检索不会为了搜索去给没有向量的需求补向量（那要写库，由 cluster 命令负责）。
入选但还没有向量的条数会随结果返回，让 agent 知道「没搜到」不等于「库里没有」。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import cluster, rank, store

BOARD_NAME = {"wish": "愿望榜", "project": "功能请求榜"}

# 相似度档位，按 2026-10-28 在 47 条带向量的真实数据、5 条查询上看出来的：库里有真正匹配的需求时，它排第 1、
# 分数 0.72–0.91；没有匹配时最高也只有 0.55–0.58；前 10 名的底部约 0.45–0.53。样本很小，数据多了要重新量。
# 嵌入模型不同、语言混用时分数分布会变，这几个数只是经验值，所以结果里同时给原始分数。
# 已知会误报：泛泛的短查询（如「自托管的密码管理器」）会把只是沾边的需求打到 0.7 以上（实测 0.706），
# 所以标签叫「较相关」而不是「强相关」，agent 必须读内容确认
STRONG, WEAK = 0.7, 0.55


def strength(similarity: float) -> str:
    """把相似度翻成 agent 一眼能用的话：疑似无关不要当证据，较相关也要读内容确认。"""
    return "较相关" if similarity >= STRONG else "弱相关" if similarity >= WEAK else "疑似无关"


def search(coll, embed, model: str, query: str, *, k: int = 5, board: str | None = None,
           domain: str | None = None, source: str | None = None, days: int | None = None,
           min_confidence: float = 0.5, min_similarity: float = 0.0) -> dict:
    """返回 {"results": [...], "missing_embeddings": N, "searched": M}。

    结果按相似度降序；同一簇只留最像的一条（带上簇的规模），否则同一个需求会占满前几名。"""
    if not query.strip():
        raise ValueError("查询不能为空")
    since = datetime.now(UTC) - timedelta(days=days) if days else None
    docs, missing = store.search_candidates(coll, model, min_confidence, domain=domain, source=source, since=since)
    if board:
        docs = [d for d in docs if rank.board(d) == board]
    qv = cluster.unit(embed([query])[0])
    scored = sorted(((cluster._dot(qv, cluster.unit(d["embedding"])), d) for d in docs),
                    key=lambda t: t[0], reverse=True)
    sizes = {c["_id"]: c["size"] for c in store.load_clusters(coll)}
    out, seen = [], set()
    for sim, d in scored:
        if sim < min_similarity or len(out) >= k:
            break
        cid = d.get("cluster_id")
        if cid and cid in seen:
            continue
        seen.add(cid)
        out.append(_item(d, sim, sizes.get(cid, 1)))
    return {"results": out, "missing_embeddings": missing, "searched": len(docs)}


def _item(d: dict, similarity: float | None = None, cluster_size: int = 1) -> dict:
    item = {"id": d["_id"], "need": d.get("need") or d.get("title", ""), "domain": d.get("domain", ""),
            "pain_point": d.get("pain_point", ""), "excerpt": d.get("excerpt", ""),
            "source": d.get("source", ""), "url": d.get("url", ""), "title": d.get("title", ""),
            "heat": d.get("score") or 0, "board": rank.board(d), "cluster_size": cluster_size}
    if similarity is not None:
        item["similarity"] = round(similarity, 3)
    return item


def render(item: dict, n: int | None = None) -> str:
    """一条结果渲染成给人 / agent 读的 Markdown：要点 + 原话 + 链接，方便核对和引用。"""
    head = f"{n}. " if n else ""
    meta = [BOARD_NAME[item["board"]], item["source"]]
    if "similarity" in item:
        meta.insert(0, f"{strength(item['similarity'])} 相似度 {item['similarity']}")
    if item["heat"]:
        meta.append(f"热度 {item['heat']}")
    if item["cluster_size"] > 1:
        meta.append(f"同一需求共 {item['cluster_size']} 条")
    lines = [f"{head}**{item['need']}**（{' · '.join(meta)}）"]
    if item["domain"]:
        lines.append(f"   - 领域：{item['domain']}")
    if item["pain_point"]:
        lines.append(f"   - 痛点：{item['pain_point']}")
    if item["excerpt"]:
        lines.append(f"   - 原话：“{item['excerpt']}”")
    lines.append(f"   - 来源：[{item['title'] or item['source']}]({item['url']})  id: `{item['id']}`")
    return "\n".join(lines)


def render_results(res: dict) -> str:
    if not res["results"]:
        body = "没有找到足够相似的需求。"
    else:
        body = "\n\n".join(render(it, i) for i, it in enumerate(res["results"], 1))
    if res["missing_embeddings"]:
        body += (f"\n\n（注意：还有 {res['missing_embeddings']} 条入选需求没有向量，不在检索范围内；"
                 f"运行 `demand-radar cluster` 补齐后再搜。本次检索了 {res['searched']} 条。）")
    return body
