"""recommend 命令的离线测试：档位匹配、默认档位表、.env 写入、硬件检测的容错、命令行接入。"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from demand_radar import hardware, recommend
from demand_radar.config import Config
from demand_radar.hardware import Hardware

HERE = os.path.dirname(__file__)
DEFAULT = os.path.join(HERE, "..", "models.toml")


def hw(ram=16.0, apple=False, vram=0.0, docker=False) -> Hardware:
    return Hardware(os="Linux", arch="x86_64", ram_gb=ram, cpu_cores=8, apple_silicon=apple,
                    nvidia_vram_gb=vram, in_docker=docker)


TIERS = [
    {"id": "nv", "when": {"min_vram_gb": 8}, "runtime": "ollama", "extract": "a", "embed": "e"},
    {"id": "mac", "when": {"apple_silicon": True, "min_ram_gb": 16}, "runtime": "mlx", "extract": "m", "embed": "e"},
    {"id": "cpu", "when": {"min_ram_gb": 8}, "runtime": "ollama", "extract": "c", "embed": "e"},
    {"id": "any", "when": {}, "runtime": "ollama", "extract": "s", "embed": "e"},
]


class MatchTest(unittest.TestCase):
    def test_first_match_wins(self):
        self.assertEqual(recommend.choose(TIERS, hw(vram=12))["id"], "nv")
        self.assertEqual(recommend.choose(TIERS, hw(apple=True, ram=32))["id"], "mac")
        self.assertEqual(recommend.choose(TIERS, hw(apple=True, ram=8))["id"], "cpu")
        self.assertEqual(recommend.choose(TIERS, hw(ram=4))["id"], "any")

    def test_apple_condition_false_excludes_mac(self):
        tiers = [{"id": "x", "when": {"apple_silicon": False}, "runtime": "ollama", "extract": "a", "embed": "e"}]
        self.assertIsNone(recommend.choose(tiers, hw(apple=True)))

    def test_forced_tier(self):
        self.assertEqual(recommend.choose(TIERS, hw(ram=4), "mac")["id"], "mac")
        with self.assertRaises(SystemExit):
            recommend.choose(TIERS, hw(), "nope")


class ExcludeOriginTest(unittest.TestCase):
    TIERS = [
        {"id": "q", "when": {}, "runtime": "ollama", "extract": "a", "embed": "e",
         "extract_origin": "cn", "embed_origin": "cn"},
        {"id": "mix", "when": {}, "runtime": "ollama", "extract": "b", "embed": "e",
         "extract_origin": "fr", "embed_origin": "CN"},
        {"id": "intl", "when": {}, "runtime": "ollama", "extract": "c", "embed": "e",
         "extract_origin": "fr", "embed_origin": "us"},
    ]

    def test_default_unchanged(self):
        self.assertEqual(recommend.choose(self.TIERS, hw())["id"], "q")

    def test_skips_if_either_model_excluded(self):
        self.assertEqual(recommend.choose(self.TIERS, hw(), exclude={"cn"})["id"], "intl")

    def test_errors_instead_of_falling_back(self):
        with self.assertRaises(SystemExit):
            recommend.choose(self.TIERS[:2], hw(), exclude={"cn"})

    def test_default_catalog_has_non_cn_for_every_machine(self):
        tiers = recommend.load_tiers(DEFAULT)
        for h in (hw(ram=4), hw(ram=16), hw(apple=True, ram=16), hw(apple=True, ram=64), hw(vram=12, ram=32)):
            t = recommend.choose(tiers, h, exclude={"cn"})
            self.assertNotIn("cn", (t["extract_origin"], t["embed_origin"]))

    def test_default_catalog_marks_origin_everywhere(self):
        for t in recommend.load_tiers(DEFAULT):
            self.assertTrue(t.get("extract_origin") and t.get("embed_origin"), t["id"])

    def test_nvidia_gets_gpu_tier(self):
        t = recommend.choose(recommend.load_tiers(DEFAULT), hw(vram=8, ram=16))
        self.assertIn("docker-compose.nvidia.yml", recommend.env_for(t)["COMPOSE_FILE"])


class EnvTest(unittest.TestCase):
    def test_env_per_runtime(self):
        self.assertEqual(recommend.env_for(TIERS[2]),
                         {"COMPOSE_FILE": "docker-compose.yml", "DR_EMBED_MODEL": "e", "DR_MODEL": "c"})
        self.assertIn("docker-compose.nvidia.yml", recommend.env_for(TIERS[0])["COMPOSE_FILE"])
        mac = recommend.env_for(TIERS[1])
        self.assertEqual(mac["MLX_MODEL"], "m")
        self.assertIn("docker-compose.mac-gpu.yml", mac["COMPOSE_FILE"])
        self.assertIn("11434", mac["DR_EMBED_BASE_URL"])  # mlx-lm 没有嵌入接口
        self.assertNotIn("DR_MODEL", mac)

    def test_write_env_keeps_other_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w") as f:
                f.write("DR_LIMIT=3\n# DR_MODEL=commented\nDR_MODEL=old\n")
            recommend.write_env(path, {"DR_MODEL": "new", "DR_EMBED_MODEL": "e"})
            with open(path) as f:
                text = f.read()
        self.assertIn("DR_LIMIT=3\n", text)
        self.assertIn("# DR_MODEL=commented\n", text)  # 注释行不动
        self.assertIn("DR_MODEL=new\n", text)
        self.assertNotIn("DR_MODEL=old", text)
        self.assertIn("DR_EMBED_MODEL=e\n", text)

    def test_write_env_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            recommend.write_env(path, {"DR_MODEL": "x"})
            with open(path) as f:
                self.assertIn("DR_MODEL=x", f.read())


class DefaultCatalogTest(unittest.TestCase):
    def test_valid_and_has_catch_all(self):
        tiers = recommend.load_tiers(DEFAULT)
        self.assertGreaterEqual(len(tiers), 3)
        self.assertEqual(tiers[-1].get("when") or {}, {})  # 最后一档兜底，任何机器都有推荐
        self.assertEqual(len({t["id"] for t in tiers}), len(tiers))

    def test_every_machine_gets_a_tier(self):
        tiers = recommend.load_tiers(DEFAULT)
        for h in (hw(ram=4), hw(ram=16), hw(apple=True, ram=8), hw(apple=True, ram=64), hw(vram=24, ram=64)):
            self.assertIsNotNone(recommend.choose(tiers, h))

    def test_bad_runtime_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[[tier]]\nid="x"\nruntime="vllm"\nextract="a"\nembed="b"\n')
        try:
            with self.assertRaises(SystemExit):
                recommend.load_tiers(f.name)
        finally:
            os.unlink(f.name)

    def test_missing_file(self):
        with self.assertRaises(SystemExit):
            recommend.load_tiers("/nonexistent/models.toml")


class HardwareTest(unittest.TestCase):
    def test_detect_never_raises(self):
        h = hardware.detect()
        self.assertGreater(h.ram_gb, 0)
        self.assertGreater(h.cpu_cores, 0)

    def test_nvidia_parse_picks_largest(self):
        out = "8192, RTX 3070\n24576, RTX 4090\n"
        with mock.patch.object(hardware, "_run", return_value=out):
            self.assertEqual(hardware._nvidia(), (24.0, "RTX 4090"))

    def test_no_nvidia_smi(self):
        with mock.patch.object(hardware, "_run", return_value=""):
            self.assertEqual(hardware._nvidia(), (0.0, ""))

    def test_override(self):
        h = recommend.override(hw(ram=8, docker=True), True, 32, None)
        self.assertTrue(h.apple_silicon)
        self.assertEqual(h.ram_gb, 32)
        self.assertIn("手动", h.chip)


class CliTest(unittest.TestCase):
    def _run(self, *argv) -> str:
        cfg = Config.from_args(["recommend", "--models-file", DEFAULT, *argv])
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.object(recommend, "detect", return_value=hw(ram=16)):
            self.assertEqual(recommend.run(cfg), 0)
        return buf.getvalue()

    def test_report(self):
        out = self._run()
        self.assertIn("推荐档位：", out)
        self.assertIn("DR_MODEL=", out)

    def test_apple_flag(self):
        self.assertIn("MLX_MODEL=", self._run("--apple", "--ram-gb", "32"))

    def test_list(self):
        self.assertIn("✓", self._run("--list"))
        self.assertIn("×", self._run("--list", "--exclude-origin", "cn"))

    def test_exclude_origin(self):
        out = self._run("--exclude-origin", "cn")
        self.assertIn("intl", out)
        self.assertNotIn("qwen", out.lower())

    def test_write_env_refused_in_docker(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            cfg = Config.from_args(["recommend", "--models-file", DEFAULT, "--write-env", "--env-file", path])
            buf = io.StringIO()
            with redirect_stdout(buf), mock.patch.object(recommend, "detect", return_value=hw(docker=True)):
                recommend.run(cfg)
            self.assertFalse(os.path.exists(path))
            self.assertIn("复制", buf.getvalue())

    def test_write_env(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            self._run("--write-env", "--env-file", path)
            with open(path) as f:
                self.assertIn("DR_MODEL=", f.read())


if __name__ == "__main__":
    unittest.main()
