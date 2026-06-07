# CUI // SP-CTI
"""IQE CWK — Co-Worker Canvas — collection adapters (icdev mirror).

See ``tools/iqe/adapters/cwk.py`` for full documentation.
"""
from __future__ import annotations

from typing import Any

from icdev.tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from icdev.tools.db.storage import get_canvas_connection  # noqa: PLC0415
        conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    return conn


def coworkers_adapter(conn: Any) -> list[dict]:
    """Return rows from ace_coworkers."""
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
    """Return rows from ace_instances."""
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
