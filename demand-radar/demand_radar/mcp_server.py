"""MCP 服务（stdio）：把需求库开放给 Claude Code 等 agent 当 RAG 用，只读。

自己实现最小的 JSON-RPC（initialize / tools/list / tools/call / ping），不引入 mcp SDK：
协议一行一个 JSON，stdout 只能放协议消息，日志一律写 stderr。

三个工具：
  search_demands  语义检索，找和一句话最像的需求（带原话和原帖链接，agent 回答时可以引用）
  get_demand      按 id 取一条的详情，连同同一需求的其余原帖
  list_top        按排序分列榜单（愿望榜 / 功能请求榜）
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

from . import __version__, cluster, rank, search, store

DEFAULT_VERSION = "2025-06-18"
MAX_K = 20

_FILTERS = {
    "board": {"type": "string", "enum": ["wish", "project"],
              "description": "wish 愿望榜（有人想要还不存在的东西）；project 功能请求榜（给已有开源项目提的请求，GitHub）。不传 = 全部"},
    "domain": {"type": "string", "description": "领域，如「开发工具」"},
    "days": {"type": "integer", "minimum": 1, "description": "只看最近 N 天抽取的"},
    "k": {"type": "integer", "minimum": 1, "maximum": MAX_K, "description": "返回几条，默认 5"},
}

TOOLS = [
    {"name": "search_demands",
     "description": "在需求雷达的库里语义搜索：用一句话描述想找的需求或点子，返回最像的真实用户需求"
                    "（需求要点、痛点、用户原话、原帖链接、相似度）。用来判断一个点子有没有人想要、有多少人想要。"
                    "每条结果标了相关度：较相关（相似度≥0.7）、弱相关（0.55–0.7，只能当线索）、疑似无关（<0.55，不要当证据）。"
                    "但分数只是辅助，泛泛的查询会把只是沾边的需求打到 0.7 以上，务必读需求内容确认真的是同一类需求。"
                    "没有真正匹配的就如实说「库里没有找到类似的需求」，不要硬凑。回答时引用原帖链接；"
                    "库里的需求要点是模型归纳的，用户原话在「原话」一栏，以原话为准。",
     "inputSchema": {"type": "object", "required": ["query"],
                     "properties": {"query": {"type": "string", "description": "要找的需求，中英文都行"},
                                    **_FILTERS}}},
    {"name": "get_demand",
     "description": "按 id 取一条需求的详情，连同同一需求在其他来源里的原帖。id 来自 search_demands / list_top 的结果。",
     "inputSchema": {"type": "object", "required": ["id"],
                     "properties": {"id": {"type": "string", "description": "需求 id"}}}},
    {"name": "list_top",
     "description": "按排序分列出榜单靠前的需求。默认看愿望榜；两个榜的排序分不可比，不要混着比较。",
     "inputSchema": {"type": "object", "properties": _FILTERS}},
]


class Server:
    def __init__(self, coll, embed, model: str, weights: dict[str, float], min_confidence: float = 0.5,
                 min_similarity: float = 0.5):
        self.coll, self.embed, self.model = coll, embed, model
        self.w, self.min_confidence, self.min_similarity = weights, min_confidence, min_similarity

    # ---- 协议 ----

    def handle(self, msg: dict) -> dict | None:
        """处理一条消息；通知（没有 id）不回。"""
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            return None
        try:
            if method == "initialize":
                ver = (msg.get("params") or {}).get("protocolVersion") or DEFAULT_VERSION
                result = {"protocolVersion": ver, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "demand-radar", "version": __version__}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.call((msg.get("params") or {}).get("name"), (msg.get("params") or {}).get("arguments") or {})
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"不支持的方法：{method}"}}
        except Exception as e:  # 协议层兜底，别让一次异常把整个服务带崩
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def call(self, name: str | None, args: dict) -> dict:
        """工具出错不是协议错误：返回 isError，让 agent 读到原因自己决定怎么办。"""
        try:
            tool = {"search_demands": self.search_demands, "get_demand": self.get_demand,
                    "list_top": self.list_top}.get(name or "")
            if tool is None:
                return _text(f"没有这个工具：{name}", error=True)
            return _text(tool(args))
        except Exception as e:
            return _text(f"{name} 失败：{type(e).__name__}: {str(e)[:300]}", error=True)

    # ---- 工具 ----

    def _k(self, args: dict) -> int:
        return max(1, min(int(args.get("k") or 5), MAX_K))

    def search_demands(self, args: dict) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        res = search.search(self.coll, self.embed, self.model, query, k=self._k(args), board=args.get("board"),
                            domain=args.get("domain"), days=args.get("days"),
                            min_confidence=self.min_confidence, min_similarity=self.min_similarity)
        return search.render_results(res)

    def get_demand(self, args: dict) -> str:
        doc = self.coll.find_one({"_id": args.get("id")}, {"embedding": 0})
        if not doc:
            raise ValueError(f"找不到 id {args.get('id')}")
        cl = next((c for c in store.load_clusters(self.coll) if c["_id"] == doc.get("cluster_id")), None)
        out = [search.render(search._item(doc, None, cl["size"] if cl else 1))]
        others = [m for m in store.member_docs(self.coll, cl["members"]) if m["_id"] != doc["_id"]] if cl else []
        if others:
            out.append("\n同一需求在其他帖子里：")
            out += [f"- [{m.get('title') or m['source']}]({m.get('url', '')}) （{m.get('source', '')}）" for m in others]
        return "\n".join(out)

    def list_top(self, args: dict) -> str:
        since = datetime.now(UTC) - timedelta(days=args["days"]) if args.get("days") else None
        rows = rank.sort(store.query_demands(self.coll, self.min_confidence, held=False,
                                             domain=args.get("domain"), since=since), self.w)
        board = args.get("board") or "wish"
        rows = [r for r in rows if rank.board(r) == board]
        rows = _fold(self.coll, rows, self.w)[: self._k(args)]
        if not rows:
            return "这个范围里没有需求。"
        lines = [f"{search.BOARD_NAME[board]}（排序分只在同一个榜内可比）："]
        for i, r in enumerate(rows, 1):
            lines.append(search.render({**search._item(r, None, r.get("cluster_size", 1)),
                                        "need": f"{r.get('need') or r['title']}［排序分 {r['rank_score']}］"}, i))
        return "\n\n".join(lines)


def _fold(coll, rows: list[dict], w: dict[str, float]) -> list[dict]:
    """同一簇只留一条，和清单一致；没跑过 cluster 的库原样返回。"""
    cids = {r.get("cluster_id") for r in rows} - {None}
    if not cids:
        return rows
    clusters = [c for c in store.load_clusters(coll) if c["_id"] in cids]
    return cluster.group(rows, clusters, store.member_docs(coll, {i for c in clusters for i in c["members"]}), w)


def _text(text: str, error: bool = False) -> dict:
    out = {"content": [{"type": "text", "text": text}]}
    if error:
        out["isError"] = True
    return out


def serve(server: Server, stdin=None, stdout=None) -> None:
    """一行一个 JSON，读到 EOF 退出。stdout 只写协议消息。"""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "不是合法的 JSON"}}
        else:
            resp = server.handle(msg) if isinstance(msg, dict) else None
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
