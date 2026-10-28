"""综合排序、清单与周报输出的离线测试。"""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from demand_radar import rank, report


def doc(need, domain="效率", clarity=3, intensity=3, feasibility=3, score=0, excerpt="原话"):
    return {"need": need, "domain": domain, "pain_point": "现有的不好用", "excerpt": excerpt,
            "clarity": clarity, "intensity": intensity, "feasibility": feasibility,
            "score": score, "source": "hackernews", "url": "https://x", "title": "原帖标题"}


class RankTest(unittest.TestCase):
    def test_weighted_scores_order(self):
        rows = rank.sort([doc("弱", 1, 1, 1), doc("强", 5, 5, 5), doc("中", 3, 3, 3)])
        self.assertEqual([r["need"] for r in rows], ["强", "中", "弱"])

    def test_heat_is_logarithmic(self):
        # 一万赞也不该压过分项打分高出很多的需求
        hot_but_vague = doc("热但模糊", 1, 1, 1, score=10000)
        clear_and_wanted = doc("清楚且迫切", 5, 5, 5, score=0)
        rows = rank.sort([hot_but_vague, clear_and_wanted])
        self.assertEqual(rows[0]["need"], "清楚且迫切")

    def test_weights_override(self):
        w = rank.weights({"heat_wish": 10.0})
        rows = rank.sort([doc("热", 1, 1, 1, score=1000), doc("冷", 5, 5, 5)], w)
        self.assertEqual(rows[0]["need"], "热")

    def test_legacy_docs_without_scores(self):
        legacy = {"need": "旧数据", "score": 3}
        self.assertGreater(rank.score(legacy), 0)


def gh(need, score=0, source="github", **kw):
    return {**doc(need, score=score, **kw), "source": source}


class BoardTest(unittest.TestCase):
    def test_board_by_source(self):
        self.assertEqual(rank.board({"source": "github"}), "project")
        self.assertEqual(rank.board({"source": "github/discussions"}), "project")
        for src in ("hackernews", "stackexchange/softwarerecs", "reddit/macapps", "githubby", "", None):
            self.assertEqual(rank.board({"source": src}), "wish", src)

    def test_sort_marks_board(self):
        rows = rank.sort([gh("a"), doc("b")])
        self.assertEqual({r["need"]: r["board"] for r in rows}, {"a": "project", "b": "wish"})

    def test_wish_heat_weight_default_is_small(self):
        # 同样 1500 赞、同样的分项打分：愿望榜只加 0.3×log1p，功能请求榜加 1.0×log1p
        wish = rank.score(doc("w", score=1500))
        project = rank.score(gh("p", score=1500))
        self.assertAlmostEqual(project - wish, 0.7 * math.log1p(1500), places=6)
        self.assertAlmostEqual(rank.score(doc("w", score=0)), 3 + 4.5 + 3)

    def test_heat_weights_are_independent(self):
        w = rank.weights({"heat": 5.0})  # 只改功能请求榜，愿望榜不变
        self.assertEqual(rank.score(doc("w", score=100), w), rank.score(doc("w", score=100)))
        self.assertGreater(rank.score(gh("p", score=100), w), rank.score(gh("p", score=100)))

    def test_negative_score_does_not_lower_wish(self):
        self.assertEqual(rank.score(doc("w", score=-5)), rank.score(doc("w", score=0)))


class ReportTest(unittest.TestCase):
    def _out(self):
        return os.path.join(tempfile.mkdtemp(), "o.md")

    def test_list_zh_has_excerpt_and_scores(self):
        p = self._out()
        report.write_markdown(rank.sort([doc("一个工具")]), p, "zh")
        s = Path(p).read_text(encoding="utf-8")
        self.assertIn("# 需求雷达清单", s)
        self.assertIn("原文：“原话”", s)
        self.assertIn("清晰度 3 · 迫切度 3 · 可实现性 3", s)

    def test_list_en_labels(self):
        p = self._out()
        report.write_markdown([doc("A tool", domain="productivity")], p, "en")
        s = Path(p).read_text(encoding="utf-8")
        self.assertIn("# Demand Radar", s)
        self.assertIn("Domain: productivity", s)
        self.assertIn("clarity 3 · intensity 3 · feasibility 3", s)
        self.assertNotIn("清晰度", s)

    def test_digest_groups_by_domain_largest_first(self):
        p = self._out()
        rows = [doc("a", "截图"), doc("b", "效率"), doc("c", "效率"), doc("d", "")]
        report.write_digest(rows, p, 7, "zh")
        s = Path(p).read_text(encoding="utf-8")
        self.assertLess(s.index("## 效率（2）"), s.index("## 截图（1）"))
        self.assertIn("## 其他（1）", s)
        self.assertIn("最近 7 天新增", s)

    def test_digest_empty(self):
        p = self._out()
        report.write_digest([], p, 7, "en")
        self.assertIn("No new needs", Path(p).read_text(encoding="utf-8"))

    def test_list_single_board_is_unchanged(self):
        p = self._out()
        report.write_markdown(rank.sort([doc("一个工具")]), p, "zh")
        s = Path(p).read_text(encoding="utf-8")
        self.assertIn("## 1. 一个工具", s)
        self.assertNotIn("功能请求", s)

    def test_list_splits_boards_and_renumbers(self):
        p = self._out()
        rows = rank.sort([gh("某项目的功能", score=3000, clarity=5, intensity=5, feasibility=5),
                          doc("新产品愿望A", 3, 3, 3), doc("新产品愿望B", 2, 2, 2)])
        report.write_markdown(rows, p, "zh")
        s = Path(p).read_text(encoding="utf-8")
        # GitHub 分再高也排在愿望之后，并且各榜各自从 1 编号
        self.assertLess(s.index("## 2. 新产品愿望B"), s.index("## 已有项目的功能请求（1）"))
        self.assertLess(s.index("## 已有项目的功能请求（1）"), s.index("### 1. 某项目的功能"))
        self.assertIn("## 1. 新产品愿望A", s)
        self.assertIn("共 3 条", s)

    def test_digest_puts_project_board_after_domain_groups(self):
        p = self._out()
        rows = rank.sort([gh("某项目的功能"), doc("愿望", domain="效率")])
        report.write_digest(rows, p, 7, "en")
        s = Path(p).read_text(encoding="utf-8")
        self.assertLess(s.index("## 效率 (1)"), s.index("## Feature requests on existing projects (1)"))
        self.assertNotIn("## 其他", s)

    def test_digest_only_project_rows(self):
        p = self._out()
        report.write_digest(rank.sort([gh("某项目的功能")]), p, 7, "zh")
        s = Path(p).read_text(encoding="utf-8")
        self.assertIn("## 已有项目的功能请求（1）", s)

    def test_held_section_at_end_with_cue(self):
        held = [{**doc("盯梢工具"), "review_cue": "makes public"}]
        for write in (lambda p: report.write_digest([doc("一个工具")], p, 7, "zh", held),
                      lambda p: report.write_markdown([doc("一个工具")], p, "zh", held)):
            p = self._out()
            write(p)
            s = Path(p).read_text(encoding="utf-8")
            self.assertLess(s.index("一个工具"), s.index("## 待人工确认（1）"))
            self.assertLess(s.index("## 待人工确认（1）"), s.index("盯梢工具"))
            self.assertIn("命中：“makes public”", s)

    def test_no_held_section_when_empty(self):
        p = self._out()
        report.write_digest([doc("一个工具")], p, 7, "en", [])
        self.assertNotIn("Held for review", Path(p).read_text(encoding="utf-8"))


try:
    import mongomock
except ImportError:  # pragma: no cover
    mongomock = None


@unittest.skipIf(mongomock is None, "需要 mongomock：pip install -e '.[dev]'")
class RecentDemandsTest(unittest.TestCase):
    def test_only_recent_listed_demands(self):
        from demand_radar import store
        coll = mongomock.MongoClient().db.demands
        now = datetime.now(UTC)
        base = {"is_request": True, "buildable": True, "confidence": 0.9}
        coll.insert_many([
            {"_id": "new", **base, "extracted_at": now - timedelta(days=1)},
            {"_id": "old", **base, "extracted_at": now - timedelta(days=30)},
            {"_id": "not_buildable", **base, "buildable": False, "extracted_at": now},
            {"_id": "filtered", "status": "filtered", "fetched_at": now},
        ])
        got = store.recent_demands(coll, 0.5, now - timedelta(days=7))
        self.assertEqual([d["_id"] for d in got], ["new"])


if __name__ == "__main__":
    unittest.main()
