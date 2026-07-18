# CUI // SP-CTI
"""Migration Design Canvas — Standard Operating Procedures (SOPs).

Provides CRUD operations and approval workflow for SOPs linked to the
Migration Design Canvas. Examples: migration readiness assessment,
cutover planning, rollback procedure, post-migration validation.
"""

import json
import uuid
from datetime import datetime, timezone


def _get_conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_json_field(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    if value is None:
        return default
    return value


def _sop_to_dict(row):
    if not row:
        return None
    d = dict(row)
    d["steps"] = _parse_json_field(d.get("steps"), [])
    d["nist_controls"] = _parse_json_field(d.get("nist_controls"), [])
    return d


# ── Read ───────────────────────────────────────────────────────────────────────


def get_all_sops(sop_type=None, approval_status=None):
    """Return all SOPs, optionally filtered by type and/or approval_status."""
    if sop_type and approval_status:
        sql = "SELECT * FROM mc_sops WHERE sop_type = %s AND approval_status = %s ORDER BY updated_at DESC"
        params = [sop_type, approval_status]
    elif sop_type:
        sql = "SELECT * FROM mc_sops WHERE sop_type = %s ORDER BY updated_at DESC"
        params = [sop_type]
    elif approval_status:
        sql = "SELECT * FROM mc_sops WHERE approval_status = %s ORDER BY updated_at DESC"
        params = [approval_status]
    else:
        sql = "SELECT * FROM mc_sops ORDER BY updated_at DESC"
        params = []
    with _get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_sop_to_dict(r) for r in rows]


def get_sop_by_id(sop_id):
    """Return a single SOP dict or None."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM mc_sops WHERE id=%s", (sop_id,)).fetchone()
    return _sop_to_dict(row)


# ── Write ──────────────────────────────────────────────────────────────────────


def create_sop(data):
    """Create a new SOP. Returns the new SOP dict."""
    sop_id = str(uuid.uuid4())
    now = _now()
    steps = json.dumps(data.get("steps", []))
    nist_controls = json.dumps(data.get("nist_controls", []))
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO mc_sops
               (id, title, sop_type, description, purpose, scope,
                steps, nist_controls, owner, reviewer,
                approval_status, version, next_review_date,
                classification, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                sop_id,
                data.get("title", "Untitled SOP"),
                data.get("sop_type", "custom"),
                data.get("description", ""),
                data.get("purpose", ""),
                data.get("scope", ""),
                steps,
                nist_controls,
                data.get("owner", ""),
                data.get("reviewer", ""),
                "draft",
                data.get("version", "1.0"),
                data.get("next_review_date", ""),
                data.get("classification", "CUI // SP-CTI"),
                now,
                now,
            ),
        )
        conn.commit()
    return get_sop_by_id(sop_id)


def update_sop(sop_id, data):
    """Update an existing SOP. Returns updated dict or None."""
    existing = get_sop_by_id(sop_id)
    if not existing:
        return None
    now = _now()
    steps = json.dumps(data.get("steps", existing["steps"]))
    nist_controls = json.dumps(data.get("nist_controls", existing["nist_controls"]))
    with _get_conn() as conn:
        conn.execute(
            """UPDATE mc_sops SET
               title=%s, sop_type=%s, description=%s, purpose=%s, scope=%s,
               steps=%s, nist_controls=%s, owner=%s, reviewer=%s,
               version=%s, next_review_date=%s, classification=%s, updated_at=%s
               WHERE id=%s""",
            (
                data.get("title", existing["title"]),
                data.get("sop_type", existing["sop_type"]),
                data.get("description", existing["description"]),
                data.get("purpose", existing["purpose"]),
                data.get("scope", existing["scope"]),
                steps,
                nist_controls,
                data.get("owner", existing["owner"]),
                data.get("reviewer", existing["reviewer"]),
                data.get("version", existing["version"]),
                data.get("next_review_date", existing["next_review_date"]),
                data.get("classification", existing["classification"]),
                now,
                sop_id,
            ),
        )
        conn.commit()
    return get_sop_by_id(sop_id)


def delete_sop(sop_id):
    """Delete a SOP. Returns True if deleted, False if not found."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM mc_sops WHERE id=%s", (sop_id,))
        conn.commit()
    return cur.rowcount > 0


# ── Approval Workflow ──────────────────────────────────────────────────────────


def submit_sop(sop_id):
    """Submit a draft SOP for review."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE mc_sops SET approval_status='pending_review', updated_at=%s WHERE id=%s AND approval_status='draft'",
            (_now(), sop_id),
        )
        conn.commit()
    return get_sop_by_id(sop_id)


def approve_sop(sop_id, approved_by=""):
    """Approve a pending SOP."""
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE mc_sops SET approval_status='approved', approved_by=%s, approved_at=%s, updated_at=%s WHERE id=%s",
            (approved_by, now, now, sop_id),
        )
        conn.commit()
    sop = get_sop_by_id(sop_id)
    try:
        from tools.canvas.event_bus import publish as _eb_publish
        _eb_publish("mdc", "mdc.sop.approved", {
            "sop_id": sop_id,
            "approved_by": approved_by,
            "design_id": (sop or {}).get("design_id", ""),
            "sop_type": (sop or {}).get("sop_type", ""),
            "classification": (sop or {}).get("classification", "CUI"),
        }, target_canvas="idc")
    except Exception:
        pass
    return sop


def reject_sop(sop_id, reason=""):
    """Reject a pending SOP."""
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE mc_sops SET approval_status='rejected', rejected_reason=%s, updated_at=%s WHERE id=%s",
            (reason, now, sop_id),
        )
        conn.commit()
    return get_sop_by_id(sop_id)
