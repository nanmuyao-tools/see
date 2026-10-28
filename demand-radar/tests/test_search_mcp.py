"""语义检索和 MCP 服务的离线测试：mongomock 当库，用假的嵌入函数，不碰网络、不起服务。"""

from __future__ import annotations

import io
import json
import unittest

from demand_radar import mcp_server, search

try:
    import mongomock
except ImportError:  # pragma: no cover
    mongomock = None

MODEL = "m1"
# 假嵌入：按关键词落到固定方向，余弦相似度一目了然
AXES = {"快递": [1, 0, 0], "密码": [0, 1, 0], "记账": [0, 0, 1]}


def embed(texts):
    out = []
    for t in texts:
        v = [0.0, 0.0, 0.0]
        for kw, ax in AXES.items():
            if kw in t:
                v = [a + b for a, b in zip(v, ax, strict=True)]
        out.append(v if any(v) else [0.1, 0.1, 0.1])
    return out


def doc(_id, need, source="hackernews", score=0, cluster_id=None, vec=True, model=MODEL, **kw):
    d = {"_id": _id, "source": source, "need": need, "title": f"帖子 {_id}", "url": f"https://x/{_id}",
         "domain": "效率", "pain_point": f"{need}的痛点", "excerpt": "原话", "score": score, "confidence": 0.9,
         "is_request": True, "buildable": True, "review_cue": "", "status": "extracted",
         "clarity": 3, "intensity": 3, "feasibility": 3, **kw}
    if vec:
        d["embedding"] = embed([need])[0]
        d["embedding_model"] = model
    if cluster_id:
        d["cluster_id"] = cluster_id
    return d


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -e '.[dev]'")
class SearchTest(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.coll = self.db.demands
        self.coll.insert_many([
            doc("a", "想要一个快递追踪工具", cluster_id="c:a"), doc("b", "快递包裹一处看", cluster_id="c:a"),
            doc("c", "自托管的密码管理器"), doc("g", "快递相关的 CLI 功能", source="github", score=900),
            doc("n", "没有向量的记账需求", vec=False),
            doc("o", "别的模型算的向量", model="other"),
        ])
        self.db.clusters.insert_one({"_id": "c:a", "rep_id": "a", "members": ["a", "b"], "size": 2,
                                     "sources": ["hackernews"]})

    def run_search(self, q, **kw):
        return search.search(self.coll, embed, MODEL, q, **kw)

    def test_nearest_first_and_cluster_deduped(self):
        res = self.run_search("快递在哪儿", k=5)
        ids = [r["id"] for r in res["results"]]
        self.assertIn(ids[0], ("a", "b"))
        self.assertFalse({"a", "b"} <= set(ids))  # 同一簇只留一条
        self.assertEqual(res["results"][0]["cluster_size"], 2)
        self.assertGreater(res["results"][0]["similarity"], 0.9)

    def test_only_same_model_and_reports_missing(self):
        res = self.run_search("记账")
        self.assertEqual(res["missing_embeddings"], 2)  # n 没向量、o 的向量是别的模型
        self.assertNotIn("o", [r["id"] for r in res["results"]])
        self.assertIn("demand-radar cluster", search.render_results(res))

    def test_board_filter(self):
        wish = self.run_search("快递", board="wish")
        proj = self.run_search("快递", board="project")
        self.assertNotIn("g", [r["id"] for r in wish["results"]])
        self.assertEqual([r["id"] for r in proj["results"]], ["g"])
        self.assertEqual(proj["results"][0]["board"], "project")

    def test_k_and_min_similarity(self):
        self.assertEqual(len(self.run_search("快递", k=1)["results"]), 1)
        res = self.run_search("密码", min_similarity=0.9)
        self.assertEqual([r["id"] for r in res["results"]], ["c"])

    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):
            self.run_search("  ")

    def test_held_demands_never_returned(self):
        self.coll.insert_one(doc("h", "快递盯梢某人", review_cue="追踪"))
        self.assertNotIn("h", [r["id"] for r in self.run_search("快递", k=10)["results"]])

    def test_strength_labels(self):
        self.assertEqual([search.strength(x) for x in (0.9, 0.7, 0.6, 0.55, 0.54)],
                         ["较相关", "较相关", "弱相关", "弱相关", "疑似无关"])
        self.assertIn("较相关", search.render_results(self.run_search("密码", k=1)))

    def test_render_has_quote_and_link(self):
        text = search.render_results(self.run_search("密码", k=1))
        self.assertIn("“原话”", text)
        self.assertIn("(https://x/c)", text)
        self.assertIn("愿望榜", text)


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -e '.[dev]'")
class McpTest(unittest.TestCase):
    def setUp(self):
        db = mongomock.MongoClient().db
        db.demands.insert_many([doc("a", "想要一个快递追踪工具", cluster_id="c:a"),
                                doc("b", "快递包裹一处看", cluster_id="c:a"), doc("c", "自托管的密码管理器"),
                                doc("g", "快递 CLI", source="github", score=900)])
        db.clusters.insert_one({"_id": "c:a", "rep_id": "a", "members": ["a", "b"], "size": 2,
                                "sources": ["hackernews"]})
        self.srv = mcp_server.Server(db.demands, embed, MODEL, {"clarity": 1.0, "intensity": 1.5,
                                                               "feasibility": 1.0, "heat": 1.0, "heat_wish": 0.3})

    def rpc(self, method, params=None, mid=1):
        return self.srv.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})

    def tool(self, name, args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return r["content"][0]["text"], r.get("isError", False)

    def test_initialize_echoes_version_and_declares_tools(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(r["serverInfo"]["name"], "demand-radar")

    def test_notification_gets_no_reply_and_ping(self):
        self.assertIsNone(self.srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(self.rpc("ping")["result"], {})

    def test_tools_list(self):
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["search_demands", "get_demand", "list_top"])
        for t in self.rpc("tools/list")["result"]["tools"]:
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_unknown_method_is_protocol_error(self):
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)

    def test_search_tool(self):
        text, err = self.tool("search_demands", {"query": "快递", "board": "wish", "k": 3})
        self.assertFalse(err)
        self.assertIn("快递", text)
        self.assertNotIn("CLI", text)  # 功能请求榜的不在愿望榜结果里

    def test_get_demand_lists_cluster_siblings(self):
        text, err = self.tool("get_demand", {"id": "a"})
        self.assertFalse(err)
        self.assertIn("同一需求在其他帖子里", text)
        self.assertIn("(https://x/b)", text)

    def test_get_demand_missing_is_tool_error_not_crash(self):
        text, err = self.tool("get_demand", {"id": "nope"})
        self.assertTrue(err)
        self.assertIn("找不到", text)

    def test_list_top_defaults_to_wish_board_and_folds_clusters(self):
        text, err = self.tool("list_top", {})
        self.assertFalse(err)
        self.assertIn("愿望榜", text)
        self.assertNotIn("CLI", text)
        self.assertEqual(text.count("快递"), 1 + text.count("同一需求"))  # 簇折叠成一条
        text2, _ = self.tool("list_top", {"board": "project"})
        self.assertIn("CLI", text2)

    def test_tool_errors_are_reported_not_raised(self):
        text, err = self.tool("search_demands", {"query": ""})
        self.assertTrue(err)
        text, err = self.tool("nope", {})
        self.assertTrue(err)

    def test_embedding_failure_becomes_tool_error(self):
        def boom(_):
            raise ConnectionError("ollama down")
        srv = mcp_server.Server(self.srv.coll, boom, MODEL, self.srv.w)
        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "search_demands", "arguments": {"query": "快递"}}})["result"]
        self.assertTrue(r["isError"])
        self.assertIn("ConnectionError", r["content"][0]["text"])

    def test_stdio_loop_one_json_per_line_stdout_only_protocol(self):
        inp = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            "", "不是 json",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n")
        out = io.StringIO()
        mcp_server.serve(self.srv, inp, out)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 3)  # initialize、解析错误、tools/list；通知和空行不回
        msgs = [json.loads(x) for x in lines]
        self.assertEqual([m.get("id") for m in msgs], [1, None, 2])
        self.assertEqual(msgs[1]["error"]["code"], -32700)
        self.assertNotIn("\n", lines[0])


if __name__ == "__main__":
    unittest.main()
