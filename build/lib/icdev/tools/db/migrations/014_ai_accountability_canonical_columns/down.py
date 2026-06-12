#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 014 rollback: SQLite cannot DROP COLUMN cleanly without a full
table rebuild, and these columns are needed by accountability_manager. The
rollback is intentionally a no-op — the canonical schema still tolerates the
absence of these columns through DEFAULTs. To truly remove them, drop the
relevant tables and re-run init_icdev_db.py.
"""

import sqlite3


def down(conn: sqlite3.Connection) -> dict:  # pragma: no cover
    return {
        "status": "no_op",
        "reason": "Columns are additive and required by accountability_manager. "
        "SQLite has no DROP COLUMN; rebuild via init_icdev_db.py if needed.",
    }
