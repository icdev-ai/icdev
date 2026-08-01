# CUI // SP-CTI
"""Federated Governance Engine — OPA REST client with pure-Python fallback.

OPA_URL from ICDEV_OPA_URL env var. Empty = local fallback mode.
Stores policies in dm_opa_policies; audit trail in dm_policy_audit_log.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from tools.data_canvas.db.init_db import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.data_canvas.data_mesh.governance_engine")

_OPA_URL = os.environ.get("ICDEV_OPA_URL", "").rstrip("/")

_STARTER_REGO = (
    'package datamesh\n'
    'default allow = false\n'
    'allow {\n'
    '  input.user.clearance != ""\n'
    '}\n'
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _governance_fail_closed() -> bool:
    """Read the governance fail-closed toggle at call time (not import time).

    Default OFF to preserve the historical local pass-through behavior. When ON
    (``ICDEV_GOVERNANCE_FAIL_CLOSED`` in ``{"1", "true", "yes"}``, case-insensitive)
    and no OPA server is configured, ``check_ext_access`` denies by default instead
    of allowing. Read at call time so tests can toggle it via monkeypatch/env.
    """
    return os.environ.get("ICDEV_GOVERNANCE_FAIL_CLOSED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


# ── Policy CRUD ───────────────────────────────────────────────────────────────

def list_policies(domain_id: str | None = None) -> list[dict]:
    with get_connection() as conn:
        if domain_id:
            rows = conn.execute(
                "SELECT * FROM dm_opa_policies WHERE domain_id=? ORDER BY name",
                (domain_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dm_opa_policies ORDER BY name"
            ).fetchall()
    return [dict(r) for r in rows]


def get_policy(policy_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM dm_opa_policies WHERE id=?", (policy_id,)
        ).fetchone()
    return dict(row) if row else None


def create_policy(data: dict) -> dict:
    policy_id = _uid()
    now = _now()
    row = {
        "id": policy_id,
        "domain_id": data.get("domain_id", ""),
        "name": data.get("name", "Untitled Policy"),
        "rego_text": data.get("rego_text", _STARTER_REGO),
        "policy_path": data.get("policy_path", "datamesh/allow"),
        "enabled": 1 if data.get("enabled", True) else 0,
        "created_at": now,
        "updated_at": now,
    }
    # Positional ? + ordered tuple: get_connection() is HYBRID (raw sqlite3 on the
    # SQLite branch — no translate wrapper; StorageConnection on PG). translate_sql
    # only rewrites ?→%s (never :name), and StorageCursor wraps a dict param into a
    # 1-tuple, so a :name mapping never reaches either driver. Column order MUST
    # match _DM_OPA_COLS.
    _DM_OPA_COLS = (
        "id", "domain_id", "name", "rego_text", "policy_path", "enabled",
        "created_at", "updated_at",
    )
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO dm_opa_policies (id, domain_id, name, rego_text, policy_path, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(row[c] for c in _DM_OPA_COLS),
        )
        conn.commit()
    return row


def update_policy(policy_id: str, data: dict) -> dict | None:
    now = _now()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM dm_opa_policies WHERE id=?", (policy_id,)
        ).fetchone()
        if not existing:
            return None
        conn.execute(
            "UPDATE dm_opa_policies SET name=?, domain_id=?, rego_text=?, policy_path=?, enabled=?, updated_at=? WHERE id=?",
            (
                data.get("name", existing["name"]),
                data.get("domain_id", existing["domain_id"]),
                data.get("rego_text", existing["rego_text"]),
                data.get("policy_path", existing["policy_path"]),
                1 if data.get("enabled", bool(existing["enabled"])) else 0,
                now,
                policy_id,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM dm_opa_policies WHERE id=?", (policy_id,)).fetchone()
    return dict(row) if row else None


def delete_policy(policy_id: str) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM dm_opa_policies WHERE id=?", (policy_id,))
        conn.commit()
    return cur.rowcount > 0


# ── Access Check ──────────────────────────────────────────────────────────────

def check_access(user_attrs: dict, resource: dict) -> dict:
    """Check access, try OPA first, fall back to local evaluation."""
    result: dict
    if _OPA_URL:
        try:
            import urllib.request
            payload = json.dumps({"input": {"user": user_attrs, "resource": resource}}).encode()
            req = urllib.request.Request(
                f"{_OPA_URL}/v1/data/datamesh/allow",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                body = json.loads(resp.read())
            allowed = bool(body.get("result", False))
            result = {
                "allowed": allowed,
                "reason": "OPA policy decision",
                "method": "opa",
                "policy_id": None,
            }
        except Exception:
            result = evaluate_locally(user_attrs, resource)
    else:
        result = evaluate_locally(user_attrs, resource)

    _log_audit(user_attrs, resource, result)
    return result


def evaluate_locally(user_attrs: dict, resource: dict) -> dict:
    """Built-in CUI policy: allow if user has CUI/SECRET/TS clearance on CUI resource."""
    clearance = user_attrs.get("clearance", "")
    classification = resource.get("classification", "")
    allowed = (
        clearance in ("CUI", "SECRET", "TS", "TS/SCI")
        and "CUI" in classification
    )
    reason = (
        f"clearance={clearance!r} authorized for classification={classification!r}"
        if allowed
        else f"clearance={clearance!r} insufficient for classification={classification!r}"
    )
    return {"allowed": allowed, "reason": reason, "method": "local", "policy_id": None}


def _log_audit(user_attrs: dict, resource: dict, result: dict) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO dm_policy_audit_log (id, policy_id, user, resource, decision, reason, method, classification, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _uid(),
                    result.get("policy_id") or "",
                    user_attrs.get("user", "anonymous"),
                    json.dumps(resource),
                    1 if result.get("allowed") else 0,
                    result.get("reason", ""),
                    result.get("method", "local"),
                    resource.get("classification", "CUI // SP-CTI"),
                    _now(),
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_log_audit: best-effort INSERT into dm_policy_audit_log failed (non-blocking): %s", exc)


# ── External IQE resource access check ───────────────────────────────────────

_EXT_RESOURCE_CLASSIFICATION = "CUI // SP-CTI"


def check_ext_access(
    connector_name: str,
    table: str,
    user_attrs: dict | None = None,
) -> dict:
    """Governance gate for IQE ext.* collection reads.

    Local mode (ICDEV_OPA_URL blank): default (fail-open) allows and writes a
    pass-through audit entry — no OPA server is configured. When the
    ``ICDEV_GOVERNANCE_FAIL_CLOSED`` toggle is ON, local mode instead DENIES and
    still writes an audit entry (fail-closed posture for IL4+ deployments).
    OPA mode: evaluates the datamesh policy and writes an audit entry.

    Args:
        connector_name: DataBridge connector name (e.g. ``"splunk"``).
        table: Logical table name (e.g. ``"alerts"``).
        user_attrs: Optional caller identity dict (``user``, ``clearance``, …).
                    Defaults to ``{"user": "system", "clearance": "CUI"}`` so
                    OPA mode local-fallback still evaluates correctly.

    Returns:
        Standard ``check_access`` result dict:
        ``{"allowed": bool, "reason": str, "method": str, "policy_id": str|None}``
    """
    resource = {
        "resource": f"ext.{connector_name}.{table}",
        "action": "ext_resource_read",
        "classification": _EXT_RESOURCE_CLASSIFICATION,
    }
    effective_user = user_attrs or {"user": "system", "clearance": "CUI"}

    if not _OPA_URL:
        if _governance_fail_closed():
            result: dict = {
                "allowed": False,
                "reason": "governance fail-closed: no OPA server configured",
                "method": "local",
                "policy_id": None,
            }
        else:
            result = {
                "allowed": True,
                "reason": "local pass-through — no OPA server configured",
                "method": "local",
                "policy_id": None,
            }
        _log_audit(effective_user, resource, result)
        return result

    return check_access(effective_user, resource)


# ── Governance Score ──────────────────────────────────────────────────────────

def compute_governance_score(domain_id: str | None = None) -> dict:
    """Return governance score (0-100) based on fraction of domains with active policies."""
    with get_connection() as conn:
        if domain_id:
            total = conn.execute(
                "SELECT count(*) FROM dm_domains WHERE id=?", (domain_id,)
            ).fetchone()[0]
            with_policy = conn.execute(
                "SELECT count(DISTINCT domain_id) FROM dm_opa_policies WHERE enabled=1 AND domain_id=?",
                (domain_id,),
            ).fetchone()[0]
        else:
            total = conn.execute("SELECT count(*) FROM dm_domains").fetchone()[0]
            with_policy = conn.execute(
                "SELECT count(DISTINCT domain_id) FROM dm_opa_policies WHERE enabled=1"
            ).fetchone()[0]

    if total == 0:
        coverage_pct = 0.0
        score = 0.0
    else:
        coverage_pct = with_policy / total
        score = round(coverage_pct * 100, 1)

    if score >= 75:
        label = "Governed"
    elif score >= 40:
        label = "Partial"
    else:
        label = "At Risk"

    return {
        "score": score,
        "domains_with_policy": with_policy,
        "total_domains": total,
        "coverage_pct": round(coverage_pct, 4),
        "label": label,
    }


# ── Audit Log ─────────────────────────────────────────────────────────────────

def get_policy_audit_log(domain_id: str | None = None, limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM dm_policy_audit_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
