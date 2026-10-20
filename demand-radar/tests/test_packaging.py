"""打包相关：默认数据文件的回退、版本号、命令行入口。"""

from __future__ import annotations

import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import demand_radar
from demand_radar import config, sources

ROOT = Path(__file__).resolve().parent.parent


class BundledDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.cwd)

    def _fake_package(self):
        """造一个带 defaults/ 的「安装包」目录，模拟 pip 安装后的布局。"""
        pkg = Path(self.tmp) / "pkg"
        (pkg / "defaults").mkdir(parents=True)
        (pkg / "defaults" / "sources.toml").write_text("[hn_algolia]\nqueries = []\n")
        return mock.patch.object(config.resources, "files", return_value=pkg)

    def test_default_falls_back_to_packaged_copy(self):
        with self._fake_package():
            path = config.Config.from_args(["run"]).sources_file
        self.assertTrue(path.endswith("defaults/sources.toml"), path)

    def test_local_file_wins(self):
        Path("sources.toml").write_text("")
        with self._fake_package():
            self.assertEqual(config.Config.from_args(["run"]).sources_file, "sources.toml")

    def test_explicit_path_never_falls_back(self):
        with self._fake_package():
            self.assertEqual(config.Config.from_args(["run", "--sources-file", "mine.toml"]).sources_file,
                             "mine.toml")

    def test_missing_config_is_a_friendly_error(self):
        with self.assertRaises(SystemExit) as e:
            sources.load_config("nope.toml")
        self.assertIn("找不到数据源配置文件", str(e.exception))


class MetadataTest(unittest.TestCase):
    def test_pyproject_points_at_real_files(self):
        meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        for src in meta["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]:
            self.assertTrue((ROOT / src).is_file(), src)
        self.assertEqual(meta["project"]["scripts"]["demand-radar"], "demand_radar.cli:main")

    def test_version(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stdout"):
            config.Config.from_args(["--version"])
        self.assertRegex(demand_radar.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
