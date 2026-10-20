"""cluster 命令、run --cluster 与周报按簇展示的离线测试：嵌入函数是假的，库用 mongomock。"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from harness import CONF, FakeEnv, demand, item, mongomock
from test_cluster import FakeEmbed

from demand_radar import cli
from demand_radar.config import Config


class ConfigTest(unittest.TestCase):
    def test_cluster_command_args(self):
        cfg = Config.from_args(["cluster", "--embed-model", "bge-m3", "--threshold", "0.9", "--rebuild",
                                "--embed-base-url", "http://e:1/v1", "--output", "o.md", "--lang", "en"])
        self.assertEqual(cfg.command, "cluster")
        self.assertEqual((cfg.embed_model, cfg.cluster_threshold, cfg.rebuild), ("bge-m3", 0.9, True))
        self.assertEqual((cfg.embed_base_url, cfg.output_path, cfg.output_language), ("http://e:1/v1", "o.md", "en"))
        self.assertFalse(cfg.cluster_eval)

    def test_cluster_eval_args(self):
        cfg = Config.from_args(["cluster", "--eval", "--pairs", "p.json", "-v"])
        self.assertEqual((cfg.cluster_eval, cfg.eval_pairs, cfg.verbose), (True, "p.json", True))

    def test_defaults(self):
        cfg = Config.from_args(["cluster"])
        self.assertEqual((cfg.embed_model, cfg.cluster_threshold, cfg.embed_base_url),
                         ("qwen3-embedding:0.6b", 0.78, ""))
        self.assertEqual(cfg.eval_pairs, "eval/cluster_pairs.json")

    def test_run_cluster_flag_default_off(self):
        self.assertFalse(Config.from_args(["run"]).cluster)
        self.assertTrue(Config.from_args(["run", "--cluster"]).cluster)
        self.assertTrue(Config.from_args(["schedule", "--cluster", "--threshold", "0.8"]).cluster)

    def test_env_vars(self):
        env = {"DR_CLUSTER": "1", "DR_EMBED_MODEL": "bge-m3", "DR_CLUSTER_THRESHOLD": "0.8",
               "DR_EMBED_BASE_URL": "http://ollama:11434/v1"}
        with mock.patch.dict(os.environ, env):
            cfg = Config.from_args(["run"])
        self.assertEqual((cfg.cluster, cfg.embed_model, cfg.cluster_threshold, cfg.embed_base_url),
                         (True, "bge-m3", 0.8, "http://ollama:11434/v1"))

    def test_other_commands_unaffected(self):
        cfg = Config.from_args(["digest", "--days", "3"])
        self.assertEqual((cfg.command, cfg.cluster, cfg.days), ("digest", False, 3))


def by_id(needs: dict[str, str]):
    """抽取替身：按 external_id 给出 need，向量化时用 VECS 里的假向量。"""
    return lambda it: demand(need=needs[it.external_id])


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -e '.[dev]'")
class RunClusterTest(unittest.TestCase):
    ITEMS = [item("1", source="hn"), item("2", source="reddit/software"), item("3", source="hn")]
    ITEMS[0].score = 5  # 并发抽取按完成顺序入库，同分时代表不确定；给 1 更高热度让它稳定当代表
    NEEDS = {"1": "快递追踪", "2": "包裹物流一处看", "3": "密码管理器"}

    def test_run_with_cluster_merges_and_records(self):
        env = FakeEnv(cluster=True)
        embed = FakeEmbed()
        with mock.patch.object(cli.cluster, "embedder", return_value=embed) as mk:
            out = env.run(self.ITEMS, extract=by_id(self.NEEDS))
        mk.assert_called_once_with("http://localhost:11434/v1", "qwen3-embedding:0.6b")
        self.assertEqual(len(embed.calls), 1)
        self.assertIn("向量化 3 条，归簇 3 条，新簇 2 个", out)
        self.assertEqual(len(list(env.coll.database["clusters"].find())), 2)

        s = Path(env.out).read_text(encoding="utf-8")
        self.assertIn("共 2 条", s)
        self.assertIn("- 来自 2 个来源，共 2 条，总热度 5\n  - [reddit/software](u) Is there a tool 2", s)
        self.assertEqual(s.count("\n## "), 2)  # 三条需求折成两条
        (run,) = env.runs()
        self.assertEqual((run["cluster_embedded"], run["cluster_new_clusters"], run["listed"]), (3, 2, 2))

    def test_embed_base_url_overrides(self):
        env = FakeEnv(cluster=True, embed_base_url="http://ollama:11434/v1", embed_model="bge-m3")
        with mock.patch.object(cli.cluster, "embedder", return_value=FakeEmbed()) as mk:
            env.run(self.ITEMS, extract=by_id(self.NEEDS))
        mk.assert_called_once_with("http://ollama:11434/v1", "bge-m3")

    def test_run_without_flag_does_not_embed(self):
        env = FakeEnv()
        with mock.patch.object(cli.cluster, "embedder") as mk:
            env.run(self.ITEMS, extract=by_id(self.NEEDS))
        mk.assert_not_called()
        self.assertNotIn("embedding", env.coll.find_one({"_id": "hn:1"}))
        self.assertEqual(s_count := Path(env.out).read_text(encoding="utf-8").count("\n## "), 3, s_count)

    def test_embed_failure_does_not_break_run(self):
        env = FakeEnv(cluster=True)

        def broken(texts):
            raise ConnectionError("embeddings down")

        with mock.patch.object(cli.cluster, "embedder", return_value=broken):
            out = env.run(self.ITEMS, extract=by_id(self.NEEDS))
        self.assertIn("合并失败", out)
        self.assertIn("共 3 条", Path(env.out).read_text(encoding="utf-8"))
        (run,) = env.runs()
        self.assertEqual(run["status"], "done")
        self.assertIn("embeddings down", run["cluster_error"])

    def test_second_run_keeps_old_clusters_and_folds_new_member(self):
        env = FakeEnv(cluster=True)
        embed = FakeEmbed()
        with mock.patch.object(cli.cluster, "embedder", return_value=embed):
            env.run(self.ITEMS, extract=by_id(self.NEEDS))
            env.run([item("4", source="softwarerecs")], extract=by_id({"4": "自托管密码库"}))
        self.assertEqual(embed.calls[-1], ["自托管密码库"])
        s = Path(env.out).read_text(encoding="utf-8")
        self.assertIn("## 2. 密码管理器\n", s)  # 4 分不比 3 高，3 仍是代表，4 作为成员链接挂在下面
        self.assertIn("- 来自 2 个来源，共 2 条\n  - [softwarerecs](u) Is there a tool 4", s)


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -e '.[dev]'")
class ClusterCommandTest(unittest.TestCase):
    def setUp(self):
        self.coll = mongomock.MongoClient().db.demands
        self.out = os.path.join(tempfile.mkdtemp(), "demands.md")
        now = datetime.now(UTC)
        base = {"is_request": True, "buildable": True, "confidence": 0.9, "review_cue": "", "status": "extracted",
                "clarity": 3, "intensity": 3, "feasibility": 3, "extracted_at": now, "url": "u"}
        self.coll.insert_many([
            {"_id": "hn:1", "source": "hn", "external_id": "1", "title": "t1", "need": "快递追踪", "score": 5, **base},
            {"_id": "se:2", "source": "softwarerecs", "external_id": "2", "title": "t2", "need": "包裹物流一处看", **base},
            {"_id": "hn:3", "source": "hn", "external_id": "3", "title": "t3", "need": "密码管理器", **base},
            {"_id": "hn:old", "source": "hn", "external_id": "old", "title": "旧数据", **base},  # 没有 need，用标题
        ])

    def run_cmd(self, argv, embed=None):
        cfg = Config.from_args(argv + ["--output", self.out])
        buf = io.StringIO()
        with mock.patch.object(cli.store, "connect", return_value=self.coll), \
             mock.patch.object(cli.sources, "load_config", return_value=CONF), \
             mock.patch.object(cli.cluster, "embedder", return_value=embed or FakeEmbed()), \
             redirect_stdout(buf), redirect_stderr(buf):
            cli.main(argv + ["--output", self.out])
        return buf.getvalue(), cfg

    def test_cluster_command_writes_list_and_run_record(self):
        out, _ = self.run_cmd(["cluster"])
        self.assertIn("向量化 4 条，归簇 4 条，新簇 3 个，库里共 3 个簇", out)
        s = Path(self.out).read_text(encoding="utf-8")
        self.assertIn("## 1. 快递追踪", s)
        self.assertIn("- 来自 2 个来源，共 2 条，总热度 5\n  - [softwarerecs](u) t2", s)
        self.assertIn("旧数据", s)
        (run,) = self.coll.database["runs"].find()
        self.assertEqual((run["command"], run["status"], run["embedded"], run["clusters"]), ("cluster", "done", 4, 3))

    def test_rebuild_flag(self):
        self.run_cmd(["cluster"])
        out, _ = self.run_cmd(["cluster", "--rebuild"])
        self.assertIn("（已重建）", out)
        self.assertIn("向量化 0 条", out)

    def test_embed_service_down_exits_with_hint(self):
        def broken(texts):
            raise ConnectionError("refused")

        with self.assertRaises(SystemExit) as cm:
            self.run_cmd(["cluster", "--embed-model", "bge-m3"], embed=broken)
        self.assertIn("ollama pull bge-m3", str(cm.exception))
        (run,) = self.coll.database["runs"].find()
        self.assertEqual(run["status"], "failed")

    def test_digest_folds_clusters_too(self):
        self.run_cmd(["cluster"])
        path = os.path.join(tempfile.mkdtemp(), "d.md")
        buf = io.StringIO()
        with mock.patch.object(cli.store, "connect", return_value=self.coll), \
             mock.patch.object(cli.sources, "load_config", return_value=CONF), redirect_stdout(buf):
            cli.digest(Config(command="digest", digest_output=path))
        s = Path(path).read_text(encoding="utf-8")
        self.assertIn("- 来自 2 个来源，共 2 条", s)
        self.assertIn("新增 3 条需求", buf.getvalue())

    def test_eval_mode_does_not_touch_db(self):
        pairs = os.path.join(tempfile.mkdtemp(), "pairs.json")
        with open(pairs, "w", encoding="utf-8") as f:
            json.dump([{"a": "快递追踪", "b": "包裹物流一处看", "same": True, "lang": "zh"},
                       {"a": "快递追踪", "b": "密码管理器", "same": False, "lang": "zh"}], f)
        buf = io.StringIO()
        with mock.patch.object(cli.store, "connect") as connect, \
             mock.patch.object(cli.cluster, "embedder", return_value=FakeEmbed()), \
             redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            cli.main(["cluster", "--eval", "--pairs", pairs])
        self.assertEqual(cm.exception.code, 0)
        connect.assert_not_called()
        self.assertIn("准确率   : 2/2 = 100.0%", buf.getvalue())

    def test_eval_missing_file(self):
        with mock.patch.object(cli.cluster, "embedder", return_value=FakeEmbed()), \
             self.assertRaises(SystemExit) as cm:
            cli.main(["cluster", "--eval", "--pairs", "/nonexistent/pairs.json"])
        self.assertIn("读取标注对失败", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
