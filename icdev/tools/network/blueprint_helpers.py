# CUI // SP-CTI
"""Network Design Canvas — Blueprint helper functions."""

import uuid
from datetime import datetime, timezone
from functools import wraps


def _now():
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row):
    if row is None:
        return {}
    return dict(row) if hasattr(row, "keys") else {}


def _audit(action, entity_type="", entity_id="", detail="", classification="CUI"):
    """Append-only audit log entry for NDC operations.

    Called as: _audit("CREATE", "topology", topo_id, "optional detail")
    Opens its own connection so callers don't need to pass one.
    """
    from tools.network.db.init_db import get_connection as _get_conn

    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO ndc_audit (id, design_id, user, action, detail, classification, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"na-{uuid.uuid4().hex[:10]}", entity_id, entity_type, action, detail, classification, _now()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Audit is best-effort — never block the operation


def _notify(event_type, data=None):
    """Placeholder for SSE notification."""
    pass


def nc_login_required(f):
    """No-op decorator for login (dashboard auth handles this)."""

    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)

    return decorated


def _crud_list(conn, table, order_by="created_at DESC", limit=100):
    rows = conn.execute(f"SELECT * FROM [{table}] ORDER BY {order_by} LIMIT ?", (limit,)).fetchall()  # noqa: S608, E501
    return [_row_to_dict(r) for r in rows]


def _crud_create(conn, table, data):
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    conn.execute(f"INSERT INTO [{table}] ({cols}) VALUES ({placeholders})", list(data.values()))  # noqa: S608
    conn.commit()
    return data


def _crud_delete(conn, table, row_id):
    conn.execute(f"DELETE FROM [{table}] WHERE id = ?", (row_id,))  # noqa: S608
    conn.commit()
    return {"deleted": row_id}


_NDC_LIFECYCLE = ["draft", "review", "approved", "deployed", "retired"]
