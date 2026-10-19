"""相似需求合并：向量化 + 余弦相似度阈值归簇。

同一个需求会在不同来源、不同帖子里反复出现（HN 评论、softwarerecs 提问、Reddit 帖子各说一遍），
清单里应合成一条，标明来自几个来源、热度合计多少。

做法（v0.3 先用最简单的，不够再上 UMAP + HDBSCAN）：
  1. 向量化：need（为空则 title）经 OpenAI 兼容的 /v1/embeddings 取向量，存回 demands 文档的
     embedding / embedding_model 字段。模型相同的不重算；换了模型整体重算、簇也重建。
  2. 归簇：增量「领头法」——按排序分从高到低处理还没归簇的条目，和已有簇的代表向量比余弦相似度，
     够像就归入最像的那个簇，否则自己成为新簇的代表。先处理高分条目，新簇的代表自然就是好的那条。
  3. 簇的统计（代表、来源、总热度、最高排序分、成员数）每次合并后重算，写进 clusters 集合，
     demands 文档记 cluster_id。

几千条规模全在内存里算：不需要向量数据库，也不依赖 numpy。向量归一化后点积就是余弦相似度，
Python 3.12 的 math.sumprod 是 C 实现，几千条 × 几百维也只要毫秒级；3.11 退回 sum(map(mul))。
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from datetime import datetime, timezone
from operator import mul

from . import rank, store

# 嵌入模型要懂中文（默认输出就是中文）。2026-10-20 在 23 对标注样本上实测（容器 Ollama）：
#   nomic-embed-text（274MB）  阈值 0.85 准确率 43.5%，中文 16 对只对 5 对，「自托管的密码管理器」和「自托管的照片备份服务」相似度 0.999——不懂中文，不能用
#   bge-m3（1.2GB，MIT）        阈值 0.85 为 60.9%，最佳阈值 0.770 时 87.0%
#   qwen3-embedding:0.6b（640MB，Apache-2.0）阈值 0.85 为 60.9%，最佳阈值 0.767 时 87.0%
# 后两者打平，取更小、协议更宽松的 qwen3-embedding。阈值取 0.78：比「不同需求的最高相似度」0.767 留一点余量，
# 宁可少合并（只是多列一条）也不错合并（会把一条需求藏进别的簇里）。样本太少，换数据后用 cluster --eval 复核
DEFAULT_MODEL = "qwen3-embedding:0.6b"
DEFAULT_THRESHOLD = 0.78
BATCH = 32  # 每次请求送多少条文本；每批存一次，中断了下次从没存的继续

Embed = Callable[[list[str]], list[list[float]]]

_dot = getattr(math, "sumprod", lambda a, b: sum(map(mul, a, b)))


# ---- 向量 ----

def embedder(base_url: str, model: str, timeout: float = 120) -> Embed:
    """返回「文本列表 → 向量列表」的函数，走 OpenAI 兼容的 /v1/embeddings。

    直接用 openai 库（instructor 已依赖它）：Ollama、LM Studio、llama.cpp 和云端 OpenAI 都是这一个接口。
    本地服务不校验 key，随便给一个；设了 OPENAI_API_KEY 则用它，云端嵌入也能接。
    """
    try:
        from openai import OpenAI
    except ImportError as e:  # pragma: no cover
        raise SystemExit("缺少 openai 依赖，请先 `pip install -r requirements.txt`") from e
    client = OpenAI(base_url=base_url, api_key=os.getenv("OPENAI_API_KEY") or "local", timeout=timeout)

    def embed(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = client.embeddings.create(model=model, input=texts)
        # 按 index 排回去：规范上顺序和输入一致，但不赌实现
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]

    return embed


def text_of(doc: dict) -> str:
    """拿来向量化的文本：模型归纳出的 need 一句话；旧数据没有就用标题。"""
    return (doc.get("need") or doc.get("title") or "").strip()


def unit(v: list[float]) -> list[float]:
    n = math.sqrt(_dot(v, v))
    return [x / n for x in v] if n else list(v)


def cosine(a: list[float], b: list[float]) -> float:
    return _dot(unit(a), unit(b))


# ---- 归簇（纯内存，便于测试）----

def cluster_id_for(seed_id: str) -> str:
    """簇 id 由第一个成员（种子）的 id 派生：可读、确定、不用再生成随机 id。成员只增不减，种子不会变。"""
    return f"c:{seed_id}"


def assign(new: list[tuple[str, list[float]]], reps: list[tuple[str, list[float]]],
           threshold: float) -> dict[str, str]:
    """增量归簇。new 是 (文档 id, 单位向量)，按排序分降序；reps 是 (簇 id, 代表的单位向量)。

    每条新文档和全部代表比，最像的那个超过阈值就归入；否则以自己为代表开一个新簇，
    后面的文档也能归进来。返回 文档 id → 簇 id。reps 会被原地追加新簇，调用方不要复用。
    """
    out: dict[str, str] = {}
    for doc_id, vec in new:
        best_id, best = None, threshold
        for cid, rep in reps:
            s = _dot(vec, rep)
            if s >= best:
                best_id, best = cid, s
        if best_id is None:
            best_id = cluster_id_for(doc_id)
            reps.append((best_id, vec))
        out[doc_id] = best_id
    return out


# ---- 和库打交道 ----

def _refresh(coll, cid: str, members: list[str], model: str, w: dict[str, float]) -> dict:
    """重算一个簇的统计并写库。成员按排序分降序，第一条就是代表。"""
    docs = rank.sort(store.member_docs(coll, members), w)
    rep = docs[0]
    doc = {
        "_id": cid,
        "rep_id": rep["_id"],
        "need": text_of(rep),
        "domain": rep.get("domain", ""),
        "members": [d["_id"] for d in docs],
        "sources": sorted({d["source"] for d in docs}),
        "total_score": sum(max(d.get("score", 0), 0) for d in docs),
        "best_rank_score": rep["rank_score"],
        "size": len(docs),
        "model": model,
        "updated_at": datetime.now(timezone.utc),
    }
    store.save_cluster(coll, doc)
    return doc


def sync(coll, embed: Embed, model: str, threshold: float, w: dict[str, float],
         min_confidence: float, rebuild: bool = False, log=print) -> dict:
    """向量化缺向量的条目，再把没归簇的条目增量归簇。返回各步计数。

    换了嵌入模型时旧簇是按别的空间算的，不能混用，整体重建；--rebuild 也走这条路（比如调了阈值）。
    """
    stats = {"embedded": 0, "assigned": 0, "new_clusters": 0, "rebuilt": False}
    clusters = store.load_clusters(coll)
    if clusters and (rebuild or any(c.get("model") != model for c in clusters)):
        store.clear_clusters(coll)
        clusters, stats["rebuilt"] = [], True

    todo = [d for d in store.to_embed(coll, model, min_confidence) if text_of(d)]
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        for d, v in zip(batch, embed([text_of(d) for d in batch])):
            store.save_embedding(coll, d["_id"], v, model)
        stats["embedded"] += len(batch)
        log(f"      向量化 {stats['embedded']}/{len(todo)}")

    pending = rank.sort(store.unclustered(coll, model, min_confidence), w)
    rep_vecs = store.rep_vectors(coll, clusters)
    reps = [(c["_id"], unit(rep_vecs[c["_id"]])) for c in clusters if c["_id"] in rep_vecs]
    assigned = assign([(d["_id"], unit(d["embedding"])) for d in pending], reps, threshold)

    members = {c["_id"]: list(c["members"]) for c in clusters}
    touched: dict[str, list[str]] = {}
    for doc_id, cid in assigned.items():
        touched.setdefault(cid, []).append(doc_id)
    for cid, ids in touched.items():
        store.set_cluster(coll, ids, cid)
        if cid not in members:
            stats["new_clusters"] += 1
        members[cid] = members.get(cid, []) + ids
        _refresh(coll, cid, members[cid], model, w)
    stats["assigned"] = len(assigned)
    return stats


def group(rows: list[dict], clusters: list[dict], members: list[dict],
          w: dict[str, float] | None = None) -> list[dict]:
    """把已排序的 rows 按簇折叠：每簇只留视图内排序分最高的一条作代表，其余成员作原帖链接挂在它下面。

    来源数、成员数按整个簇算（周报只看最近几天，但「这需求之前出现过 5 次」正是有价值的信息）；
    代表从视图内选，周报里出现的才是最近的。没有 cluster_id、或簇已不存在的行原样保留，
    旧数据照常显示。返回按簇排序分降序。
    """
    by_c = {c["_id"]: c for c in clusters}
    mem = {m["_id"]: m for m in members}
    buckets: dict[str, list[dict]] = {}
    out: list[dict] = []
    for r in rows:
        cid = r.get("cluster_id")
        if cid in by_c:
            buckets.setdefault(cid, []).append(r)
        else:
            out.append(r)
    for cid, items in buckets.items():
        c = by_c[cid]
        for r in items:
            mem.setdefault(r["_id"], r)
        # 分数相同时让库里记的代表赢：成员是按完成顺序入库的，不然同分的代表每次运行可能不一样
        rep = max(items, key=lambda r: (r.get("rank_score", rank.score(r, w)), r["_id"] == c.get("rep_id")))
        others = [mem[i] for i in c["members"] if i != rep["_id"] and i in mem]
        others.sort(key=lambda d: d.get("rank_score", rank.score(d, w)), reverse=True)
        out.append({**rep, "cluster_size": c["size"], "cluster_sources": c["sources"],
                    "cluster_members": others,
                    "rank_score": round(rank.cluster_score(rep.get("rank_score", rank.score(rep, w)),
                                                           c["size"], w), 2)})
    return sorted(out, key=lambda r: r.get("rank_score", 0), reverse=True)


# ---- 评测 ----

def load_pairs(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        pairs = json.load(f)
    for p in pairs:
        if not all(k in p for k in ("a", "b", "same")):
            raise ValueError(f"标注对缺字段（要有 a、b、same）：{p}")
    return pairs


def score_pairs(pairs: list[dict], embed: Embed) -> list[float]:
    """每对的余弦相似度，和 pairs 顺序一致。所有文本一次送出，和 sync 用同一个 embed 函数。"""
    vecs = embed([p["a"] for p in pairs] + [p["b"] for p in pairs])
    n = len(pairs)
    return [_dot(unit(vecs[i]), unit(vecs[n + i])) for i in range(n)]


def summarize(pairs: list[dict], sims: list[float], threshold: float) -> dict:
    """按阈值判「是否同一需求」，和标注比：准确率、正类的精确率 / 召回率 / F1，
    以及这个集合上让准确率最高的阈值（给调参参考，别直接当真——样本太少）。"""
    tp = fp = fn = tn = 0
    for p, s in zip(pairs, sims):
        pred, gold = s >= threshold, bool(p["same"])
        tp += pred and gold
        fp += pred and not gold
        fn += (not pred) and gold
        tn += (not pred) and not gold
    n = len(pairs)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    best_t, best_acc = threshold, (tp + tn) / n if n else 0.0
    for t in sorted({round(s, 4) for s in sims}):
        acc = sum((s >= t) == bool(p["same"]) for p, s in zip(pairs, sims)) / n
        if acc > best_acc:
            best_t, best_acc = t, acc
    return {"n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "accuracy": (tp + tn) / n if n else 0.0,
            "precision": prec, "recall": rec, "f1": f1, "best_threshold": best_t, "best_accuracy": best_acc,
            "min_same": min((s for p, s in zip(pairs, sims) if p["same"]), default=0.0),
            "max_diff": max((s for p, s in zip(pairs, sims) if not p["same"]), default=0.0)}


def evaluate(path: str, embed: Embed, threshold: float, verbose: bool = False, out=print) -> int:
    """在标注对上评测合并判断。需要真实嵌入服务；不写库。返回退出码。"""
    pairs = load_pairs(path)
    sims = score_pairs(pairs, embed)
    out(f"在 {len(pairs)} 对标注样本上评测（阈值 {threshold}）…\n")
    lines = []
    for p, s in zip(pairs, sims):
        hit = (s >= threshold) == bool(p["same"])
        if verbose or not hit:
            lines.append(f"  {'✓' if hit else '✗'} 标注={'同' if p['same'] else '异'} 相似度={s:.3f}  "
                         f"{p['a'][:36]} ⟷ {p['b'][:36]}")
    if lines:
        out("逐对（只列判错的，-v 列全部）：")
        out("\n".join(lines) + "\n")
    out("=" * 48)
    r = summarize(pairs, sims, threshold)
    out(f"准确率   : {r['tp'] + r['tn']}/{r['n']} = {r['accuracy'] * 100:4.1f}%")
    out(f"同一需求 : 精确率 {r['precision'] * 100:4.1f}%  召回率 {r['recall'] * 100:4.1f}%  F1 {r['f1'] * 100:4.1f}%")
    for lang in sorted({p.get("lang", "") for p in pairs} - {""}):
        sub = [(p, s) for p, s in zip(pairs, sims) if p.get("lang") == lang]
        rl = summarize([p for p, _ in sub], [s for _, s in sub], threshold)
        out(f"  {lang:<6} : {rl['tp'] + rl['tn']}/{rl['n']} = {rl['accuracy'] * 100:4.1f}%")
    out(f"相似度   : 同一需求最低 {r['min_same']:.3f}，不同需求最高 {r['max_diff']:.3f}"
        + ("（两者有重叠，这个模型在此阈值附近分不开）" if r["max_diff"] >= r["min_same"] else ""))
    out(f"本集合上准确率最高的阈值 : {r['best_threshold']:.3f}（{r['best_accuracy'] * 100:4.1f}%）")
    out("=" * 48)
    return 0
