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


def _normalize_sop_step(step: dict) -> dict:
    """Normalize any SOP step schema variant to a canonical dict.

    Three schemas exist in ndc_sops.steps:
      A) {number, action, verify, rollback, time_est}           — most seed_sops SOPs
      B) {order, action, verify, rollback, estimated_time_min}  — E2E test SOPs
      C) {number, title, actions:[{label,command}],             — DoD/cloud SOPs
          verification:[{command,expected_output}], time_est}

    Returns: {number, text, verify, time_est, rollback}
    """
    # Step number
    number = step.get("number") or step.get("order") or ""

    # Action text: Schema A/B → 'action' string; Schema C → 'title' string or first action label
    text = step.get("action") or step.get("title") or ""
    if not text:
        actions = step.get("actions") or []
        parts = []
        for a in actions:
            if isinstance(a, dict):
                parts.append(a.get("label") or a.get("command") or "")
            elif isinstance(a, str):
                parts.append(a)
        text = "; ".join(p for p in parts if p)

    # Verification text: Schema A/B → 'verify' string; Schema C → verification[0].expected_output
    verify = step.get("verify") or ""
    if not verify:
        verif = step.get("verification") or []
        if isinstance(verif, list) and verif:
            first = verif[0]
            verify = (first.get("expected_output") or first.get("expected_result") or "") \
                if isinstance(first, dict) else str(first)
        elif isinstance(verif, str):
            verify = verif

    # Time estimate
    time_est = step.get("time_est") or step.get("estimated_time_min") or ""
    if isinstance(time_est, (int, float)):
        time_est = f"{int(time_est)}m"

    return {
        "number": number,
        "text": str(text),
        "verify": str(verify),
        "time_est": str(time_est),
        "rollback": str(step.get("rollback") or ""),
    }


def _audit(action, entity_type="", entity_id="", detail="", classification="CUI"):
    """Append-only audit log entry for NDC operations.

    Called as: _audit("CREATE", "topology", topo_id, "optional detail")
    Opens its own connection so callers don't need to pass one.
    """
    from tools.network.db.init_db import get_connection as _get_conn

    try:
        conn = _get_conn()
        conn.execute(
            'INSERT INTO ndc_audit (id, design_id, "user", action, detail, classification, created_at) '
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
