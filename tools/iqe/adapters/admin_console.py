"""IQE adapter for Admin Console — registers admin.component_changes and admin.access_grants collections."""
from __future__ import annotations

from tools.iqe.executor import register_collection


def _admin_component_changes(query_ast, conn=None):
    """Query component_audit_log via IQE."""
    from tools.db.storage import get_connection
    _conn = conn or get_connection()
    try:
        rows = _conn.execute(
            "SELECT id, component_key, event_type, actor, detail, created_at "
            "FROM component_audit_log ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        if conn is None:
            _conn.close()


def _admin_access_grants(query_ast, conn=None):
    """Query tenant_component_overrides via IQE."""
    from tools.db.storage import get_connection
    _conn = conn or get_connection()
    try:
        rows = _conn.execute(
            "SELECT id, tenant_id, component_key, enabled, updated_at "
            "FROM tenant_component_overrides ORDER BY updated_at DESC LIMIT 200"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        if conn is None:
            _conn.close()


register_collection("admin.component_changes", _admin_component_changes)
register_collection("admin.access_grants", _admin_access_grants)
