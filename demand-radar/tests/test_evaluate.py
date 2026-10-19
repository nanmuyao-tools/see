"""评测模块的离线测试：指标计算正确、标注集格式有效。"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from demand_radar import evaluate
from demand_radar.config import Config
from demand_radar.models import Demand

HERE = os.path.dirname(__file__)
EVAL_SET = os.path.join(HERE, "..", "eval", "eval_set.json")
_FIELDS = ("is_request", "need", "domain", "pain_point", "buildable", "confidence")


def demand(is_request, buildable, confidence=0.9):
    return Demand(is_request=is_request, need="x" if is_request else "", domain="d",
                  pain_point="", buildable=buildable, confidence=confidence)


def run_eval(eset, fake, min_confidence=0.5):
    p = os.path.join(tempfile.mkdtemp(), "e.json")
    json.dump(eset, open(p, "w"))
    cfg = Config(command="eval", eval_set=p, provider="ollama/x",
                 min_confidence=min_confidence, verbose=True)
    buf = io.StringIO()
    with mock.patch.object(evaluate, "build_client", return_value=None), \
         mock.patch.object(evaluate, "extract", fake), redirect_stdout(buf):
        evaluate.run(cfg)
    return buf.getvalue()


def item(id_, is_request, buildable, note=""):
    return {"source": "s", "external_id": id_, "title": f"t{id_}", "text": "",
            "url": "u", "score": 0,
            "label": {"is_request": is_request, "buildable": buildable, "note": note}}


class MetricsTest(unittest.TestCase):
    def test_perfect_model(self):
        eset = [item("1", True, True), item("2", False, None), item("3", True, False)]

        def perfect(client, it):
            lbl = next(e["label"] for e in eset if e["title"] == it.title)
            return demand(lbl["is_request"], bool(lbl["buildable"]))

        out = run_eval(eset, perfect)
        self.assertIn("is_request 准确率 : 3/3", out)
        self.assertIn("TP=1 FP=0 FN=0 TN=2", out)
        self.assertIn("F1     : 100.0%", out)

    def test_false_positive_and_negative(self):
        eset = [item("1", True, True), item("2", False, None)]
        # 把该入选的判成非需求（FN），把非需求判成入选（FP）
        preds = {"t1": demand(False, False), "t2": demand(True, True)}
        out = run_eval(eset, lambda c, it: preds[it.title])
        self.assertIn("TP=0 FP=1 FN=1 TN=0", out)
        self.assertIn("is_request 准确率 : 0/2", out)

    def test_confidence_threshold_excludes(self):
        eset = [item("1", True, True)]
        # 判断正确但可信度低于阈值 → 不入选 → FN
        out = run_eval(eset, lambda c, it: demand(True, True, confidence=0.3), min_confidence=0.5)
        self.assertIn("FN=1", out)

    def test_buildable_scored_only_on_real_requests(self):
        # 非需求条目即使 buildable 标注存在也不计入 buildable 分母
        eset = [item("1", True, True), item("2", False, None)]
        out = run_eval(eset, lambda c, it: demand(it.title == "t1", True))
        self.assertIn("buildable  准确率 : 1/1", out)

    def test_uncertain_buildable_excluded_from_listing(self):
        # 是需求但可做性存疑：只评 is_request，不进入入选混淆矩阵
        eset = [item("1", True, None), item("2", True, True)]
        out = run_eval(eset, lambda c, it: demand(True, True))
        self.assertIn("is_request 准确率 : 2/2", out)
        self.assertIn("TP=1 FP=0 FN=0 TN=0", out)

    def test_extraction_failure_counted(self):
        eset = [item("1", True, True)]

        def boom(client, it):
            raise RuntimeError("model down")

        out = run_eval(eset, boom)
        self.assertIn("抽取失败          : 1 条", out)


class EvalSetIntegrityTest(unittest.TestCase):
    def test_labels_wellformed(self):
        data = json.load(open(EVAL_SET, encoding="utf-8"))
        self.assertGreaterEqual(len(data), 40)
        keys = set()
        for d in data:
            for f in ("source", "external_id", "title", "url", "label"):
                self.assertIn(f, d)
            lbl = d["label"]
            self.assertIsInstance(lbl["is_request"], bool)
            self.assertIn(lbl["buildable"], (True, False, None))
            self.assertTrue(lbl["note"])
            # buildable 只在 is_request 为真时才有意义
            if not lbl["is_request"]:
                self.assertIsNone(lbl["buildable"])
            keys.add((d["source"], d["external_id"]))
        self.assertEqual(len(keys), len(data), "标注集有重复条目")

    def test_balanced(self):
        data = json.load(open(EVAL_SET, encoding="utf-8"))
        listed = sum(1 for d in data if d["label"]["is_request"] and d["label"]["buildable"])
        not_req = sum(1 for d in data if not d["label"]["is_request"])
        # 正负样本都要有，不能退化成单一类别
        self.assertGreater(listed, 5)
        self.assertGreater(not_req, 5)


if __name__ == "__main__":
    unittest.main()
