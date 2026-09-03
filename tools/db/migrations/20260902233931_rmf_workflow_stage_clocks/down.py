# CUI // SP-CTI
"""Rollback 20260902233931 — drop the four cycle-time columns.

PostgreSQL only, and deliberately partial.

On PostgreSQL the four columns are dropped and the table itself is LEFT IN
PLACE. This migration did not create that table on PG (the consolidated
snapshot did), so dropping it here would destroy stage rows that predate this
change.

On SQLite the rollback is a NO-OP and says so rather than pretending. SQLite
gained ``DROP COLUMN`` only in 3.35, and on a database where this migration
CREATED the table there is nothing to roll back to — the pre-migration state is
"no table", and dropping it would delete every stage row any producer has
written since. Leaving four unused nullable columns in place is the strictly
less destructive outcome, and re-running ``up`` over them is a no-op by
construction.
"""
from __future__ import annotations

DROPPED_COLUMNS = ("started_at", "actor", "evidence_ref", "submitted_at")


def down(conn):
    backend = getattr(conn, "_backend", "sqlite")
    if backend != "postgresql":
        # pg-portability: sqlite-only path — see the module docstring for why
        # this is a deliberate no-op rather than a table rebuild.
        return
    for name in DROPPED_COLUMNS:
        conn.execute(f"ALTER TABLE rmf_workflow_stages DROP COLUMN IF EXISTS {name}")
    conn.commit()
