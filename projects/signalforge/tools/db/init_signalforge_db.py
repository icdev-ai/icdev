#!/usr/bin/env python3
# CUI // SP-CTI
"""signalforge database initialization."""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "signalforge.db"


def init_db(db_path=None):
    db_path = db_path or str(DB_PATH)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS projects "
        "(id TEXT PRIMARY KEY, name TEXT, status TEXT DEFAULT 'active', "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_trail "
        "(id TEXT PRIMARY KEY, event_type TEXT, action TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    print(f"signalforge database initialized at {db_path}")
    print(f"Tables created ({len(tables)}): {', '.join(sorted(tables))}")
    conn.close()


if __name__ == "__main__":
    init_db()
