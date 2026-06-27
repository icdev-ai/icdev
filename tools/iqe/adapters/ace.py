# CUI // SP-CTI
"""IQE ACE (ANVIL Co-Worker Engine) collection adapters.

Importing this module registers three read-only collections on the module-level
Executor, each returning REAL rows from the ACE canvas tables. ACE tables
(``ace_*``) carry no ``classification``/``tenant_id`` columns, so reads MUST go
through ``get_canvas_connection('ICDEV_ACE_DB_URL')`` — using ``get_connection()``
would attach the global RLS predicate and raise ``UndefinedColumn``.

  ace.instances  — co-worker instances        (ace_instances)
  ace.coworkers  — co-workers + parent state  (ace_coworkers JOIN ace_instances)
  ace.messages   — inter-co-worker messages    (ace_messages)

Column names follow the canonical schema of record
(``icdev/tools/ace/db/init_db.py``): ``id`` / ``state`` / ``trust_tier`` —
not ``instance_id`` / ``status``. Each adapter runs a parameter-free SELECT
(portable across SQLite and PostgreSQL) and degrades to an empty list if the
ACE schema is not yet present on this DB.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _canvas_conn(conn: Any):
    """ACE canvas tables live in the canvas DB, not the main ICDEV DB."""
    if conn is not None:
        return conn
    from tools.db.storage import get_canvas_connection  # noqa: PLC0415

    return get_canvas_connection("ICDEV_ACE_DB_URL")


def _trunc(value: Any, limit: int = 80) -> Any:
    """Truncate long text fields for compact IQE display."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def instances_adapter(conn: Any) -> list[dict]:
    """Return rows from ace_instances."""
    c = _canvas_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, name, role_id, state, trust_tier, created_at "
            "FROM ace_instances ORDER BY created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def coworkers_adapter(conn: Any) -> list[dict]:
    """Return rows from ace_coworkers joined to their parent instance."""
    c = _canvas_conn(conn)
    try:
        cur = c.execute(
            "SELECT w.id, w.instance_id, w.role_id, w.display_name, w.state, "
            "w.trust_tier, w.assigned_step, i.name AS instance_name, "
            "i.state AS instance_state, w.created_at "
            "FROM ace_coworkers w "
            "JOIN ace_instances i ON i.id = w.instance_id "
            "ORDER BY w.created_at DESC LIMIT 1000"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def messages_adapter(conn: Any) -> list[dict]:
    """Return rows from ace_messages (content truncated for compact display)."""
    c = _canvas_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, instance_id, coworker_id, message_type, role, "
            "content, created_at "
            "FROM ace_messages ORDER BY created_at DESC LIMIT 1000"
        )
        cols = [d[0] for d in cur.description]
        rows = []
        for row in cur.fetchall():
            rec = dict(zip(cols, row))
            rec["content"] = _trunc(rec.get("content"), 80)
            rows.append(rec)
        return rows
    except Exception:
        return []
    finally:
        c.close()


def sessions_adapter(conn: Any) -> list[dict]:
    """Return rows from agent_loop_sessions (migration 220)."""
    c = _canvas_conn(conn)
    try:
        cur = c.execute(
            "SELECT session_id, instance_id, coworker_id, llm_function, "
            "result_subtype, turns, total_input_tokens, total_output_tokens, "
            "total_cost_usd, created_at "
            "FROM agent_loop_sessions ORDER BY created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


register_collection("ace.instances", instances_adapter)
register_collection("ace.coworkers", coworkers_adapter)
register_collection("ace.messages", messages_adapter)
register_collection("ace.sessions", sessions_adapter)
