# CUI // SP-CTI
"""IQE CWK — Co-Worker Canvas — collection adapters.

Importing this module registers two read-only collections on the module-level
Executor, each returning REAL rows from the ``ace_*`` canvas tables:

  cwk.coworkers  — co-worker rows (ace_coworkers); filter by state, role_id,
                   trust_tier, instance_id, assigned_step.
  cwk.sessions   — ACE instance / session rows (ace_instances); filter by
                   state, trust_tier, role_id, name.

These are canvas tables (no ``classification`` / ``tenant_id`` columns), so
reads MUST use ``get_canvas_connection("ICDEV_ACE_DB_URL")`` to avoid the
RLS predicate raising ``UndefinedColumn``.

Every adapter wraps its query in try/except and returns ``[]`` if the schema
is not yet migrated, so the IQE widget degrades gracefully.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_canvas_connection  # noqa: PLC0415
        conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    return conn


def coworkers_adapter(conn: Any) -> list[dict]:
    """Return rows from ace_coworkers (most recently active first)."""
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, instance_id, role_id, display_name, state, trust_tier, "
            "assigned_step, last_active_at, created_at "
            "FROM ace_coworkers ORDER BY last_active_at DESC NULLS LAST, created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def sessions_adapter(conn: Any) -> list[dict]:
    """Return rows from ace_instances (most recently created first)."""
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, name, role_id, state, trust_tier, "
            "created_at, updated_at, completed_at "
            "FROM ace_instances ORDER BY created_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


register_collection("cwk.coworkers", coworkers_adapter)
register_collection("cwk.sessions", sessions_adapter)
