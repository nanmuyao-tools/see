"""检索第二课的基准：暴力检索要到多大的数据量才不够用？Qdrant 的 HNSW 用多少召回率换多少延迟？

对每个数据量 N（默认 1k / 10k / 100k，维度 1024，和 qwen3-embedding:0.6b 一致）比较三种做法：
  python   本项目现在的做法：纯 Python 逐条算余弦（demand_radar.cluster）——第一课的实现
  numpy    精确检索，一次矩阵乘法——「暴力检索」优化到头是什么样
  qdrant   HNSW 近似索引；另测 exact=true（同一个服务做全量扫描）看索引本身带来多少
并算 HNSW 的 recall@k（和精确结果的重合率）。数据是合成的高斯混合（模拟嵌入的聚簇结构），不是真实嵌入：
绝对数字只在这台机器上有意义，要看的是趋势和交叉点。

需要：Qdrant 服务在跑（docker compose --profile qdrant up -d qdrant）、numpy 和 qdrant-client
（pip install 'demand-radar[qdrant]'）。数据只进 bench_ 开头的临时集合，跑完自动删。

    python bench/vector_bench.py                       # 1k / 10k / 100k
    python bench/vector_bench.py --sizes 1000,10000    # 只跑小的
    python bench/vector_bench.py --sizes 1000000 --allow-large   # 100 万条要约 4GB 内存，先想清楚

注意计时口径：qdrant 一栏有两个数——「往返」是客户端看到的总耗时（含 HTTP 和 JSON），「服务端」是 Qdrant
自己报的处理时间；小数据量时往返几乎全是网络开销，直接和 numpy 比会冤枉它。
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import httpx
    import numpy as np
    from qdrant_client import QdrantClient, models
except ImportError:
    sys.exit("缺少依赖：pip install 'demand-radar[qdrant]'（需要 numpy、httpx、qdrant-client）")

from demand_radar import cluster  # noqa: E402  纯 Python 暴力检索用的就是项目里那套

MAX_SAFE = 200_000
PY_CAP = 20_000  # 纯 Python 最多在这么多条上实测：1 亿个浮点数转成 Python 对象要 3GB 多内存，更大的按线性外推


def make_data(n: int, dim: int, seed: int, centers: int = 64, spread: float = 0.45):
    """高斯混合：先选 centers 个簇心，每个点 = 簇心 + 噪声，再归一化。比均匀随机更像真实的嵌入。"""
    rng = np.random.default_rng(seed)
    c = rng.normal(size=(centers, dim)).astype(np.float32)
    c /= np.linalg.norm(c, axis=1, keepdims=True)

    def draw(m):
        x = c[rng.integers(0, centers, m)] + spread * rng.normal(size=(m, dim)).astype(np.float32) / math.sqrt(dim) * 4
        return x / np.linalg.norm(x, axis=1, keepdims=True)

    return draw(n), draw


def timeit(fn, reps: int) -> list[float]:
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return out


def fmt(ms: list[float]) -> str:
    return f"{statistics.median(ms):.2f}" if ms else "—"


def wait_ready(base: str, name: str, timeout: int = 600) -> dict:
    """等集合 green：索引是异步建的，没等完就计时，量到的是「正在建索引」的状态。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = httpx.get(f"{base}/collections/{name}", timeout=30).json()["result"]
        if r["status"] == "green":
            return r
        time.sleep(1)
    raise TimeoutError(f"{name} 在 {timeout}s 内没变 green")


def bench_one(n, args, base, client) -> dict:
    X, draw = make_data(n, args.dim, args.seed)
    Q = draw(args.queries)
    k = args.k
    truth = []
    t_np = []
    for q in Q:
        t = time.perf_counter()
        s = X @ q
        top = np.argpartition(-s, k)[:k]
        top = top[np.argsort(-s[top])]
        t_np.append((time.perf_counter() - t) * 1000)
        truth.append(set(top.tolist()))

    # 纯 Python：只抽几条查询；N 超过 PY_CAP 就在前 PY_CAP 条上实测、再按线性外推（暴力检索本来就是线性的，
    # 但这是估算，表里会标出来）
    py_reps = max(1, min(args.queries, 5))
    m = min(n, PY_CAP)
    Xl, Ql = X[:m].tolist(), Q[:py_reps].tolist()
    t_py = []
    for q in Ql:
        t = time.perf_counter()
        qv = cluster.unit(q)
        sorted(((cluster._dot(qv, v), i) for i, v in enumerate(Xl)), reverse=True)[:k]
        t_py.append((time.perf_counter() - t) * 1000 * (n / m))
    del Xl

    name = f"bench_{n}_{uuid.uuid4().hex[:6]}"
    client.create_collection(name, vectors_config=models.VectorParams(size=args.dim, distance=models.Distance.COSINE))
    t0 = time.time()
    B = 1000
    for i in range(0, n, B):
        client.upsert(name, models.Batch(ids=list(range(i, min(i + B, n))), vectors=X[i:i + B].tolist()), wait=True)
    t_up = time.time() - t0
    info = wait_ready(base, name)
    indexed, total = info["indexed_vectors_count"], info["points_count"]

    def q_rest(q, exact):
        body = {"query": q.tolist(), "limit": k, "params": {"exact": exact}}
        r = httpx.post(f"{base}/collections/{name}/points/query", json=body, timeout=120)
        r.raise_for_status()
        return r.json()

    rtt = timeit(lambda: httpx.get(f"{base}/", timeout=30), 20)  # 空请求的往返：HTTP 本身的开销
    res = {"n": n, "np": t_np, "py": t_py, "py_reps": py_reps, "py_extrapolated": n > PY_CAP, "upload_s": t_up, "indexed": indexed, "total": total,
           "rtt": rtt}
    for label, exact in (("hnsw", False), ("exact", True)):
        wall, srv, rec = [], [], []
        for i, q in enumerate(Q):
            t = time.perf_counter()
            j = q_rest(q, exact)
            wall.append((time.perf_counter() - t) * 1000)
            srv.append(j["time"] * 1000)
            rec.append(len({p["id"] for p in j["result"]["points"]} & truth[i]) / k)
        res[label] = {"wall": wall, "srv": srv, "recall": statistics.mean(rec)}
    client.delete_collection(name)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", default="1000,10000,100000", help="逗号分隔的数据量")
    ap.add_argument("--dim", type=int, default=1024)
    ap.add_argument("--queries", type=int, default=100)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--url", default="http://127.0.0.1:6333", help="Qdrant REST 地址")
    ap.add_argument("--grpc-port", type=int, default=6334, help="上传用的 gRPC 端口")
    ap.add_argument("--allow-large", action="store_true", help=f"允许超过 {MAX_SAFE} 条（1M×1024 约 4GB 内存）")
    ap.add_argument("--out", default="", help="把表格另存成 Markdown 文件")
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]
    if max(sizes) > MAX_SAFE and not args.allow_large:
        sys.exit(f"数据量超过 {MAX_SAFE}：要约 {max(sizes) * args.dim * 4 / 1e9:.1f}GB 内存，确认的话加 --allow-large")
    host = args.url.split("://", 1)[-1].rsplit(":", 1)[0]
    client = QdrantClient(host=host, grpc_port=args.grpc_port, prefer_grpc=True, timeout=120)
    version = httpx.get(args.url, timeout=10).json().get("version", "?")

    rows = []
    for n in sizes:
        print(f"… N={n:,}", file=sys.stderr, flush=True)
        rows.append(bench_one(n, args, args.url, client))

    lines = [f"Qdrant {version} · 维度 {args.dim} · k={args.k} · 每个数据量 {args.queries} 条查询 · 耗时单位 ms（中位数）",
             "", "| N | 纯 Python | numpy 精确 | Qdrant HNSW 往返 | HNSW 服务端 | Qdrant exact 服务端 | recall@k | 建了索引的向量 |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        py = ("≈" if r["py_extrapolated"] else "") + fmt(r["py"]) + ("（外推）" if r["py_extrapolated"] else "")
        lines.append(f"| {r['n']:,} | {py} | {fmt(r['np'])} | {fmt(r['hnsw']['wall'])} | {fmt(r['hnsw']['srv'])} "
                     f"| {fmt(r['exact']['srv'])} | {r['hnsw']['recall']:.3f} | {r['indexed']:,} / {r['total']:,} |")
    lines += ["", f"纯 Python 一栏每个数据量只测 {rows[0]['py_reps']} 条查询；N 超过 {PY_CAP:,} 的标「外推」："
              f"在前 {PY_CAP:,} 条上实测后按线性放大，是估算，不是实测。",
              f"空请求往返（HTTP 自身开销，中位数）：{fmt(rows[0]['rtt'])} ms。上传耗时："
              + "，".join(f"{r['n']:,} 条 {r['upload_s']:.1f}s" for r in rows) + "。",
              "「建了索引的向量」为 0 表示这个规模下 Qdrant 没建 HNSW，查询其实是全量扫描。"]
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
