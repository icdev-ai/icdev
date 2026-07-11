# CUI // SP-CTI
"""IQE Cortex Canvas collection adapters.

Registers three collections on the module-level Executor:
  cortex.chat_sessions       — canvas chat/agent sessions (cortex_chat_sessions)
  cortex.audit          — governance/facade audit trail (cortex_audit)
  cortex.search_history — unified-search / ask invocations (cortex_search_history)

Every adapter is defensive: the tables may not exist yet on a fresh checkout
(the blueprint creates them lazily), so each returns [] on any error.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def sessions_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT session_id, user_id, mode, domain, title, status, "
            "classification, tenant_id, created_at, updated_at "
            "FROM cortex_chat_sessions ORDER BY created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def audit_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, session_id, function, outcome, blocked, provenance_id, "
            "classification, tenant_id, created_at "
            "FROM cortex_audit ORDER BY created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def search_history_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT query_id, session_id, user_id, mode, domain, query_text, "
            "strategy, result_count, grounded, classification, tenant_id, created_at "
            "FROM cortex_search_history ORDER BY created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


register_collection("cortex.chat_sessions", sessions_adapter)
register_collection("cortex.audit", audit_adapter)
register_collection("cortex.search_history", search_history_adapter)
