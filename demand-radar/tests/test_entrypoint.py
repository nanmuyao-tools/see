"""容器入口脚本：--version 直通、输出目录和已有文件的写权限检查。"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("entrypoint", ROOT / "docker" / "entrypoint.py")
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


class OutputDirTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        p = mock.patch.object(entrypoint, "OUT_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)

    def test_writable_dir_passes(self):
        (Path(self.dir) / "demands.md").write_text("x")
        entrypoint.check_output_dir("run")  # 不退出

    def test_unwritable_old_file_is_caught_before_the_run(self):
        f = Path(self.dir) / "demands.md"
        f.write_text("x")
        real = os.access
        with mock.patch.object(entrypoint.os, "access", lambda p, m: False if p == str(f) else real(p, m)), \
             self.assertRaises(SystemExit) as e:
            entrypoint.check_output_dir("run")
        self.assertIn("demands.md", str(e.exception))
        self.assertIn("chown", str(e.exception))

    def test_unwritable_dir_is_caught(self):
        real = os.access
        with mock.patch.object(entrypoint.os, "access", lambda p, m: False if p == self.dir else real(p, m)), \
             self.assertRaises(SystemExit) as e:
            entrypoint.check_output_dir("digest")
        self.assertIn("没有写权限", str(e.exception))

    def test_read_only_commands_skip_the_check(self):
        with mock.patch.object(entrypoint.os, "access", lambda p, m: False):
            entrypoint.check_output_dir("serve")
            entrypoint.check_output_dir("eval")


class ArgsTest(unittest.TestCase):
    def test_version_passes_through(self):
        with mock.patch.object(entrypoint.sys, "argv", ["entrypoint", "--version"]), \
             mock.patch.object(entrypoint.os, "execvp", side_effect=SystemExit) as ex, \
             self.assertRaises(SystemExit):  # 真的 execvp 会替换进程、不再返回
            entrypoint.main()
        self.assertEqual(ex.call_args[0][1][-1], "--version")
        self.assertNotIn("run", ex.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
