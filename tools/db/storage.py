# [TEMPLATE: CUI // SP-CTI]
"""Storage abstraction layer (D-DB-21).

Provides get_connection() that returns a sqlite3 Connection with row_factory set.
All tools should use this instead of direct sqlite3.connect() calls.
"""

import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))


def get_connection(db_path: str = None) -> sqlite3.Connection:
    """Return a sqlite3 connection with Row factory enabled."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
