"""cli 编排测试共用的脚手架：mongomock 当库，抓取与模型都用假函数，不碰网络。

`unittest discover -s tests` 会把 tests/ 放进 sys.path，测试文件直接 `from harness import …`。
"""

from __future__ import annotations

import io
import os
import tempfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from unittest import mock

from demand_radar import cli
from demand_radar import extract as extract_mod
from demand_radar.config import Config
from demand_radar.models import Demand, SourceItem

try:
    import mongomock
except ImportError:  # pragma: no cover
    mongomock = None

CONF = {"softwarerecs": {"enabled": True}, "hn_algolia": {"queries": ["q"]}, "filters": {}, "ranking": {}}


def item(id_, created_at=0, source="s", text="a body long enough to pass the empty-body rule", title=None):
    return SourceItem(source=source, external_id=id_, title=title or f"Is there a tool {id_}", text=text,
                      url="u", created_at=created_at)


def demand(**kw):
    base = dict(is_request=True, need="n", domain="d", pain_point="", excerpt="", buildable=True,
                clarity=3, intensity=3, feasibility=3, confidence=0.9)
    return Demand(**{**base, **kw})


class FakeEnv:
    """一次 run / backfill 调用所需的全部替身。"""

    def __init__(self, **cfg):
        self.coll = mongomock.MongoClient().db.demands
        self.out = os.path.join(tempfile.mkdtemp(), "demands.md")
        self.cfg = Config(**{"command": "run", "source": "softwarerecs", "output_path": self.out, **cfg})
        self.calls: list[str] = []   # 送去抽取的 external_id，按调用顺序

    @contextmanager
    def patched(self, extract=None, conf=None):
        """库、配置、模型客户端与抽取函数全部替换掉；yield 捕获的标准输出。"""
        def fake_extract(client, it, lang):
            self.calls.append(it.external_id)
            return (extract or (lambda it: demand()))(it)

        buf = io.StringIO()
        with mock.patch.object(cli.store, "connect", return_value=self.coll), \
             mock.patch.object(cli.sources, "load_config", return_value=conf or CONF), \
             mock.patch.object(cli, "build_client", return_value=None), \
             mock.patch.object(extract_mod, "extract", side_effect=fake_extract), \
             redirect_stdout(buf), redirect_stderr(buf):
            yield buf

    def run(self, fetched, errors=None, extract=None, cursor_value=42, conf=None):
        """跑 cli.run。fetched 是假抓取结果；extract 是 item -> Demand，默认都判为需求。"""
        def fake_fetch_all(names, limit, conf, cursors):
            cursors.setdefault("softwarerecs", {})["softwarerecs"] = cursor_value
            return fetched, errors or {}

        with self.patched(extract, conf) as buf, \
             mock.patch.object(cli.sources, "fetch_all", side_effect=fake_fetch_all):
            cli.run(self.cfg)
        return buf.getvalue()

    def backfill(self, module, extract=None, conf=None):
        """跑 cli.backfill，module 是假的可回填源模块（units / page_before / interval）。"""
        with self.patched(extract, conf) as buf, \
             mock.patch.object(cli.sources, "backfill_module", return_value=module):
            cli.backfill(self.cfg)
        return buf.getvalue()

    def runs(self) -> list[dict]:
        return list(self.coll.database["runs"].find())
