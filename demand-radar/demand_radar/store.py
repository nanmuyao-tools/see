"""SQLite 存储：原文 + 抽取结果，按来源去重。"""

from __future__ import annotations

import os
import sqlite3
from .models import Demand, SourceItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS demands (
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    is_request    INTEGER,
    need          TEXT,
    domain        TEXT,
    pain_point    TEXT,
    buildable     INTEGER,
    confidence    REAL,
    created_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (source, external_id)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def already_seen(conn: sqlite3.Connection, source: str, external_id: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM demands WHERE source=? AND external_id=?",
        (source, external_id),
    )
    return cur.fetchone() is not None


def save(conn: sqlite3.Connection, item: SourceItem, d: Demand) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO demands
           (source, external_id, title, url, is_request, need, domain,
            pain_point, buildable, confidence)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            item.source, item.external_id, item.title, item.url,
            int(d.is_request), d.need, d.domain, d.pain_point,
            int(d.buildable), d.confidence,
        ),
    )
    conn.commit()


def top_demands(conn: sqlite3.Connection, min_confidence: float) -> list[sqlite3.Row]:
    """取出通过过滤的需求，按可信度降序。"""
    cur = conn.execute(
        """SELECT * FROM demands
           WHERE is_request=1 AND buildable=1 AND confidence>=?
           ORDER BY confidence DESC""",
        (min_confidence,),
    )
    return cur.fetchall()
