# CUI // SP-CTI
"""Migration 20261008003850 — make aisg_audit append-only on PostgreSQL.

``aisg_audit`` (AI Strategy Canvas audit trail, NIST AU) has always been
immutable on SQLite through two ``RAISE(ABORT)`` triggers, and NEVER on
PostgreSQL, the primary backend. Those triggers lived in ``SCHEMA`` in SQLite
dialect, which PostgreSQL rejects as a syntax error; PR #2376 moved them into
``_SQLITE_TRIGGERS`` so they stopped breaking the PG schema load, which made
the gap explicit: on PG an UPDATE or DELETE of an audit row simply succeeded.

This installs the PostgreSQL equivalent on databases that already have the
table: ``aisg_audit_immutable()`` raising, wired BEFORE UPDATE and BEFORE
DELETE ``FOR EACH ROW`` (the sc_audit / nc_audit / bd_audit pattern). The
statements are the ones ``tools/aisg/db/init_db.py`` runs on a fresh install,
imported rather than restated so the two can never disagree.

* SQLite: no-op. The canvas keeps its own ``aisg_canvas.db``, where init_db
  already installs the SQLite triggers.
* PostgreSQL without ``aisg_audit`` (canvas never initialised): no-op. The
  canvas's init_db creates the table AND the triggers together.
* Idempotent: CREATE OR REPLACE FUNCTION and DROP TRIGGER IF EXISTS before
  each CREATE TRIGGER, so a re-run converges.
"""
from __future__ import annotations

_TAG = "[20261008003850_aisg_audit_pg_immutability]"


def _is_pg(conn) -> bool:
    from tools.db.storage import is_pg

    return is_pg(conn)


def _table_present(conn) -> bool:
    row = conn.execute("SELECT to_regclass('aisg_audit') AS reg").fetchone()
    if row is None:
        return False
    value = row["reg"] if hasattr(row, "keys") else row[0]
    return value is not None


def up(conn) -> dict:
    if not _is_pg(conn):
        print(f"{_TAG} SQLite: no-op (init_db installs the SQLite triggers)")
        return {"status": "skipped", "reason": "sqlite"}
    if not _table_present(conn):
        print(f"{_TAG} aisg_audit absent: no-op (init_db installs table + triggers)")
        return {"status": "skipped", "reason": "aisg_audit absent"}

    from tools.aisg.db.init_db import install_pg_audit_triggers

    install_pg_audit_triggers(conn)
    conn.commit()
    print(f"{_TAG} aisg_audit_no_update / aisg_audit_no_delete installed")
    return {"status": "applied", "triggers": ["aisg_audit_no_update", "aisg_audit_no_delete"]}
