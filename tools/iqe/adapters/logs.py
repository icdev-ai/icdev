# CUI // SP-CTI
"""IQE centralized-logging collection adapter (eqo-log-04).

Registers one read-only collection on the module-level Executor:

  logs.entries — rows from the append-only ``centralized_logs`` sink (created by
                 migration 181); each row carries tenant_id + classification, so
                 reads through ``get_connection()`` are RLS-filtered to the
                 caller. Filter on component / level / message / trace_id / ts.

Mirrors the ``tools/iqe/adapters/integrity.py`` pattern: opens an RLS-aware
connection when none is supplied, runs a parameter-free SELECT (works on both
SQLite and PostgreSQL), and degrades to an empty list if the schema is not yet
present.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection
from tools.logging.constants import LOGS_TABLE, MAX_LIMIT


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection()
    return conn


def entries_adapter(conn: Any) -> list[dict]:
    """Return recent centralized_logs rows (newest first), RLS-filtered."""
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, ts, component, level, message, trace_id, session_id, "
            f"classification, tenant_id FROM {LOGS_TABLE} "
            f"ORDER BY ts DESC, id DESC LIMIT {MAX_LIMIT}"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            c.close()
        except Exception:
            pass


register_collection("logs.entries", entries_adapter)
