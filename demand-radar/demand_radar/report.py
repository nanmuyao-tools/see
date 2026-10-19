"""输出层：全量需求清单、每周摘要，支持中英文。

每条需求附原文摘录和原帖链接，读者可以一眼核对模型有没有概括偏。
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime

_LABELS = {
    "zh": {
        "list_title": "需求雷达清单",
        "digest_title": "需求雷达周报",
        "generated": "生成时间",
        "total": "共 {n} 条",
        "range": "最近 {days} 天新增",
        "domain": "领域",
        "pain": "现有痛点",
        "excerpt": "原文",
        "scores": "清晰度 {c} · 迫切度 {i} · 可实现性 {f}",
        "heat": "热度",
        "rank": "排序分",
        "source": "来源",
        "empty": "这段时间没有新的需求。",
        "other": "其他",
        "held": "待人工确认（{n}）",
        "held_note": "以下需求提到了特定的他人，可能被用来追踪、监视、曝光、骚扰或欺骗别人，没有列入正文。"
                     "请人工确认后再决定是否发布。",
        "cue": "命中",
        "cluster": "来自 {s} 个来源，共 {n} 条",
        "sep": "：",
    },
    "en": {
        "list_title": "Demand Radar",
        "digest_title": "Demand Radar Weekly",
        "generated": "Generated",
        "total": "{n} needs",
        "range": "new in the last {days} days",
        "domain": "Domain",
        "pain": "Pain point",
        "excerpt": "Quote",
        "scores": "clarity {c} · intensity {i} · feasibility {f}",
        "heat": "heat",
        "rank": "rank",
        "source": "Source",
        "empty": "No new needs in this period.",
        "other": "Other",
        "held": "Held for review ({n})",
        "held_note": "These needs mention specific other people and could be used to track, monitor, expose, "
                     "harass or deceive someone, so they are not in the list above. Check them before publishing.",
        "cue": "Matched",
        "cluster": "From {s} sources, {n} posts",
        "sep": ": ",
    },
}


def _labels(lang: str) -> dict[str, str]:
    return _LABELS.get(lang, _LABELS["zh"])


def _entry(r: dict, heading: str, L: dict[str, str]) -> list[str]:
    sep = L["sep"]
    lines = [heading, ""]
    if r.get("review_cue"):
        lines.append(f"- {L['cue']}{sep}“{r['review_cue']}”")
    if r.get("domain"):
        lines.append(f"- {L['domain']}{sep}{r['domain']}")
    if r.get("pain_point"):
        lines.append(f"- {L['pain']}{sep}{r['pain_point']}")
    if r.get("excerpt"):
        lines.append(f"- {L['excerpt']}{sep}“{r['excerpt']}”")
    meta = L["scores"].format(c=r.get("clarity", "—"), i=r.get("intensity", "—"), f=r.get("feasibility", "—"))
    if r.get("score"):
        meta += f" · {L['heat']} {r['score']}"
    if "rank_score" in r:
        meta += f" · {L['rank']} {r['rank_score']}"
    lines.append(f"- {meta}")
    lines.append(f"- {L['source']}{sep}[{r['source']}]({r['url']}) {r['title']}")
    lines += _cluster_lines(r, L)
    lines.append("")
    return lines


def _cluster_lines(r: dict, L: dict[str, str]) -> list[str]:
    """簇代表多打一行「来自 N 个来源，共 M 条」和其余成员的原帖链接（字段由 cluster.group 挂上）。
    旧数据没有这些字段，输出和以前一字不差。"""
    if r.get("cluster_size", 1) <= 1:
        return []
    lines = [f"- {L['cluster'].format(s=len(r.get('cluster_sources') or []), n=r['cluster_size'])}"]
    for m in r.get("cluster_members") or []:
        lines.append(f"  - [{m['source']}]({m['url']}) {m['title']}")
    return lines


def _write(path: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _held_section(held: list[dict], L: dict[str, str]) -> list[str]:
    if not held:
        return []
    lines = [f"## {L['held'].format(n=len(held))}", "", L["held_note"], ""]
    for r in held:
        lines += _entry(r, f"### {r.get('need') or r['title']}", L)
    return lines


def write_markdown(rows: list[dict], output_path: str, lang: str = "zh",
                   held: list[dict] | None = None) -> None:
    """全量清单，按 rows 的顺序（调用方先排好序）。held 是待人工确认的需求，附在末尾。"""
    L = _labels(lang)
    lines = [
        f"# {L['list_title']}",
        "",
        f"{L['generated']}: {datetime.now():%Y-%m-%d %H:%M} | {L['total'].format(n=len(rows))}",
        "",
    ]
    for i, r in enumerate(rows, 1):
        lines += _entry(r, f"## {i}. {r.get('need') or r['title']}", L)
    lines += _held_section(held or [], L)
    _write(output_path, lines)


def write_digest(rows: list[dict], output_path: str, days: int, lang: str = "zh",
                 held: list[dict] | None = None) -> None:
    """每周摘要：按领域分组，组按条数降序，组内按 rows 的顺序（调用方先排好序）。
    held 是待人工确认的需求，附在末尾。"""
    L = _labels(lang)
    lines = [
        f"# {L['digest_title']} · {datetime.now():%Y-%m-%d}",
        "",
        f"{L['range'].format(days=days)} | {L['total'].format(n=len(rows))}",
        "",
    ]
    if not rows:
        lines += [L["empty"], ""]
        lines += _held_section(held or [], L)
        _write(output_path, lines)
        return
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("domain") or "").strip() or L["other"]].append(r)
    for domain, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        lines += [f"## {domain}（{len(items)}）" if lang == "zh" else f"## {domain} ({len(items)})", ""]
        for r in items:
            lines += _entry(r, f"### {r.get('need') or r['title']}", L)
    lines += _held_section(held or [], L)
    _write(output_path, lines)
