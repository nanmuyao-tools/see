"""输出层：把筛选后的需求写成 Markdown 清单。"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime


def write_markdown(rows: list[sqlite3.Row], output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    lines = [
        "# 需求雷达清单",
        "",
        f"生成时间：{datetime.now():%Y-%m-%d %H:%M}　|　共 {len(rows)} 条",
        "",
    ]
    for i, r in enumerate(rows, 1):
        lines += [
            f"## {i}. {r['need'] or r['title']}",
            "",
            f"- 领域：{r['domain'] or '—'}",
            f"- 现有痛点：{r['pain_point'] or '—'}",
            f"- 可信度：{r['confidence']:.2f}",
            f"- 来源：[{r['source']}]({r['url']})",
            "",
        ]
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
