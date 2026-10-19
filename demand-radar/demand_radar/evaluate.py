"""在标注集上评测「规则预过滤 + 模型抽取」的整体质量。

标注集（eval/eval_set.json）每条带 label：
  is_request  bool          该内容是否在表达需求
  buildable   bool | null   个人能否做成；null 表示存疑，不计入 buildable 和入选评分
  note        str           标注理由

评测指标：
  - is_request：判断「是不是需求」的准确率（被规则过滤的按「判为非需求」计）
  - buildable：在「是需求」且标注非 null 的条目上，判断「能否做成」的准确率
  - 入选（端到端）：is_request ∧ buildable ∧ confidence≥阈值，与标准答案算精确率/召回率/F1
  - 规则过滤：挡掉多少条，其中有没有误杀应入选的（误杀必须为 0）
  - 摘录有效率：模型给出的摘录里，确实出自原文的比例
不写数据库。
"""

from __future__ import annotations

import json
import sys

from . import filters, sources
from .config import Config
from .extract import build_client, excerpt_in_source, extract_raw
from .models import SourceItem

_ITEM_FIELDS = ("source", "external_id", "title", "text", "url", "score", "kind")


def _load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _filter_conf(cfg: Config) -> dict:
    try:
        return sources.load_config(cfg.sources_file).get("filters") or {}
    except OSError:
        return {}


def run(cfg: Config) -> int:
    try:
        data = _load(cfg.eval_set)
    except OSError as e:
        sys.exit(f"读取标注集失败：{e}")

    fconf = _filter_conf(cfg)
    client = build_client(cfg.provider, cfg.ollama_base_url)
    print(f"在 {len(data)} 条标注样本上评测（模型 {cfg.provider}，语言 {cfg.output_language}，"
          f"阈值 {cfg.min_confidence}）…\n")

    req_ok = req_total = 0
    bld_ok = bld_total = 0
    tp = fp = fn = tn = 0  # 以「应入选」为正类
    fails = 0
    filtered = filter_kills = 0
    ex_given = ex_valid = 0
    model_calls = 0
    mistakes: list[str] = []

    for d in data:
        label = d["label"]
        item = SourceItem(**{k: d.get(k, "post" if k == "kind" else None) for k in _ITEM_FIELDS})
        uncertain = label["is_request"] and label["buildable"] is None
        gold_listed = bool(label["is_request"] and label["buildable"])

        reason = filters.check(item, fconf)
        if reason:
            # 被规则挡掉：视为「判为非需求、不入选」，不调模型
            filtered += 1
            req_total += 1
            req_ok += not label["is_request"]
            if gold_listed:
                filter_kills += 1
                mistakes.append(f"  ✗ 规则误杀[{reason}] 应入选  {item.title[:46]}")
            elif cfg.verbose:
                mistakes.append(f"  · 规则过滤[{reason}]  {item.title[:46]}")
            if not uncertain:
                fn += gold_listed
                tn += not gold_listed
            continue

        model_calls += 1
        try:
            r = extract_raw(client, item, cfg.output_language)
        except Exception as e:
            fails += 1
            mistakes.append(f"  ⚠ 抽取失败  {item.title[:50]}  （{type(e).__name__}）")
            continue

        # 摘录有效率：只统计模型给了摘录的
        if r.excerpt:
            ex_given += 1
            ex_valid += excerpt_in_source(r.excerpt, item)

        # is_request
        req_total += 1
        req_hit = r.is_request == label["is_request"]
        req_ok += req_hit

        # buildable：仅在确实是需求、且标注非 null 时评
        if label["is_request"] and label["buildable"] is not None and r.is_request:
            bld_total += 1
            bld_ok += r.buildable == label["buildable"]

        # 端到端「应入选」。可做性存疑的需求没有标准答案，跳过
        pred_listed = bool(r.is_request and r.buildable and r.confidence >= cfg.min_confidence)
        if uncertain:
            if cfg.verbose or not req_hit:
                mistakes.append(
                    f"  {'·' if req_hit else '✗'} 可做性存疑 判入选={pred_listed!s:5} "
                    f"conf={r.confidence:.2f}  {item.title[:46]}"
                    + ("" if req_hit else f"  [is_request 判成 {r.is_request}]")
                )
            continue
        tp += gold_listed and pred_listed
        fp += (not gold_listed) and pred_listed
        fn += gold_listed and (not pred_listed)
        tn += (not gold_listed) and (not pred_listed)

        if cfg.verbose or not req_hit or (gold_listed != pred_listed):
            flag = "✓" if (req_hit and gold_listed == pred_listed) else "✗"
            mistakes.append(
                f"  {flag} 应入选={gold_listed!s:5} 判入选={pred_listed!s:5} "
                f"conf={r.confidence:.2f}  {item.title[:46]}"
                + ("" if req_hit else f"  [is_request 判成 {r.is_request}]")
            )

    def pct(a: int, b: int) -> str:
        return f"{a}/{b} = {a / b * 100:4.1f}%" if b else "—"

    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0

    if mistakes:
        print("逐条（只列判错的，-v 列全部）：")
        print("\n".join(mistakes), "\n")

    print("=" * 48)
    print(f"is_request 准确率 : {pct(req_ok, req_total)}")
    print(f"buildable  准确率 : {pct(bld_ok, bld_total)}  （仅在真需求上评）")
    print(f"应入选 混淆矩阵   : TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"           精确率 : {prec*100:4.1f}%  （判入选的里有多少真该入选）")
    print(f"           召回率 : {rec*100:4.1f}%  （该入选的里抓到多少）")
    print(f"           F1     : {f1*100:4.1f}%")
    print(f"规则过滤          : {filtered} 条，误杀应入选 {filter_kills} 条；调用模型 {model_calls} 次")
    print(f"摘录有效率        : {pct(ex_valid, ex_given)}  （给出的摘录里确实出自原文的）")
    if fails:
        print(f"抽取失败          : {fails} 条")
    print("=" * 48)
    return 0
