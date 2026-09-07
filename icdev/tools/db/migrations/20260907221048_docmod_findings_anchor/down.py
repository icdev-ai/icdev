# CUI // SP-CTI
"""Rollback 20260907221048 — drop the three anchor columns.

PostgreSQL only. ``docmod_findings`` is append-only, so the columns are the
only thing this migration added and the only thing the rollback removes; the
rows stay.

On SQLite the rollback is a NO-OP and says so. SQLite gained ``DROP COLUMN``
only in 3.35, and a table rebuild to remove three nullable columns from an
append-only table risks the rows for no benefit: re-running ``up`` over them
is a no-op by construction, and a scanner from before dwr-anchor-02 simply
never names them.
"""
from __future__ import annotations

DROPPED_COLUMNS = ("anchor_start", "anchor_end", "anchor_text")


def down(conn):
    backend = getattr(conn, "_backend", "sqlite")
    if backend != "postgresql":
        # pg-portability: sqlite-only path — see the module docstring for why
        # this is a deliberate no-op rather than a table rebuild.
        return
    for name in DROPPED_COLUMNS:
        conn.execute(f"ALTER TABLE docmod_findings DROP COLUMN IF EXISTS {name}")
    conn.commit()
