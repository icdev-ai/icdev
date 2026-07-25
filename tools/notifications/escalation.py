# CUI // SP-CTI
"""Notification escalation + acknowledgement tracking (crx-not-01).

Unacknowledged CRITICAL alerts re-route to escalation channels after a
configurable timeout. Acknowledgement is an explicit act — clicking the ack
link (which hits an API route) or an API/CLI call to :func:`acknowledge`.

State lives in the ``notification_escalations`` table, which carries
``tenant_id`` + ``classification`` for row-level security. The table is mutable
state (pending -> acked / escalated), NOT an append-only audit log; every
transition is additionally recorded to the immutable ``audit_trail`` via the
shared ``atomic_log_event`` helper.

Timing is deliberately pull-based: :func:`process_escalations` is a synchronous
sweep meant to be invoked by an existing reflex/scheduler tick — there is no
always-on daemon. It is fully testable by injecting ``now``.

Public surface (small + stable — consumed by crx-gen-02 / DMX):

    register_alert(alert_id, severity, tenant_id, classification, channels,
                   component=None, timeout_minutes=None, escalation_channels=None,
                   now=None) -> dict
    acknowledge(ack_token, actor="system", now=None) -> dict
    process_escalations(now=None, tenant_id=None) -> list[dict]
    get_escalation(ack_token) -> dict | None
    ack_link(ack_token, base_url=None) -> str
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from tools.db.storage import get_connection
from .routing_rules import load_rules, resolve_channels

_TABLE = "notification_escalations"

_STATUS_PENDING = "pending"
_STATUS_ACKED = "acknowledged"
_STATUS_ESCALATED = "escalated"

# Dialect-neutral DDL (TEXT/INTEGER only) so it applies identically on
# PostgreSQL (primary) and the SQLite init/test fallback.
_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL,
    severity TEXT DEFAULT 'info',
    component TEXT DEFAULT '',
    tenant_id TEXT,
    classification TEXT,
    channels TEXT DEFAULT '[]',
    escalation_channels TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    ack_deadline TEXT,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    escalated_at TEXT
)
"""


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _ensure_schema(conn) -> None:
    """Idempotently create the table so runtime degrades gracefully when the
    migration has not yet run (fresh worktree / test fixture)."""
    try:
        conn.execute(_DDL)
        conn.commit()
    except Exception:
        pass


def _escalation_cfg() -> dict:
    return (load_rules().get("escalation") or {})


def _audit(event: str, ack_token: str, detail: dict, classification: str | None) -> None:
    """Best-effort immutable audit record via the shared atomic helper."""
    try:
        from tools.audit.audit_logger import atomic_log_event

        atomic_log_event(
            event_type=f"notification_escalation_{event}",
            actor="notification_escalation",
            action=ack_token,
            details=detail,
            classification=classification or "CUI",
        )
    except Exception:
        pass  # Never block the escalation flow on audit availability.


def _ack_token() -> str:
    """Unguessable acknowledgement token used as the row id + link secret."""
    return "ack-" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:24]


def register_alert(
    alert_id: str,
    severity: str,
    tenant_id: str | None,
    classification: str | None,
    channels: list[str],
    component: str | None = None,
    timeout_minutes: int | None = None,
    escalation_channels: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Register an alert for escalation tracking and return its ack handle.

    Only alerts whose severity is in the configured ``critical_severities`` are
    tracked; anything else returns ``{"tracked": False}`` unchanged (callers can
    always fire-and-forget non-critical alerts through the gateway directly).

    Returns a dict with ``ack_token`` (also the row id), ``ack_deadline`` and
    ``escalation_channels``. The token is the shared secret for the ack link.
    """
    crit = {s.lower() for s in (_escalation_cfg().get("critical_severities") or ["critical"])}
    if (severity or "").lower() not in crit:
        return {"tracked": False, "alert_id": alert_id, "severity": severity}

    cfg = _escalation_cfg()
    timeout = int(timeout_minutes if timeout_minutes is not None else cfg.get("timeout_minutes", 30))
    esc_channels = escalation_channels or cfg.get("default_escalation_channels") or list(channels)

    ts = _now(now)
    deadline = ts + timedelta(minutes=timeout)
    token = _ack_token()

    conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.execute(
            f"INSERT INTO {_TABLE} (id, alert_id, severity, component, tenant_id, "
            f"classification, channels, escalation_channels, status, created_at, ack_deadline) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                token,
                alert_id,
                (severity or "critical").lower(),
                component or "",
                tenant_id,
                classification,
                json.dumps(list(channels)),
                json.dumps(list(esc_channels)),
                _STATUS_PENDING,
                _iso(ts),
                _iso(deadline),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _audit("registered", token, {
        "alert_id": alert_id, "severity": severity, "component": component,
        "tenant_id": tenant_id, "ack_deadline": _iso(deadline),
    }, classification)

    return {
        "tracked": True,
        "ack_token": token,
        "alert_id": alert_id,
        "severity": (severity or "critical").lower(),
        "status": _STATUS_PENDING,
        "ack_deadline": _iso(deadline),
        "escalation_channels": list(esc_channels),
        "ack_link": ack_link(token),
    }


def acknowledge(ack_token: str, actor: str = "system", now: datetime | None = None) -> dict:
    """Acknowledge a tracked alert. Idempotent; returns the resulting status.

    Acking a row that is already ``acknowledged`` is a no-op success. Acking one
    that already ``escalated`` still records the ack (late acknowledgement) but
    leaves the escalated flag intact for the audit record.
    """
    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            f"SELECT id, status, alert_id, classification FROM {_TABLE} WHERE id = %s",
            (ack_token,),
        ).fetchone()
        if not row:
            return {"status": "error", "reason": "unknown ack token"}
        row = dict(row)
        if row["status"] == _STATUS_ACKED:
            return {"status": _STATUS_ACKED, "ack_token": ack_token, "already": True}

        ts = _iso(_now(now))
        conn.execute(
            f"UPDATE {_TABLE} SET status = %s, acknowledged_at = %s, acknowledged_by = %s "
            f"WHERE id = %s",
            (_STATUS_ACKED, ts, actor, ack_token),
        )
        conn.commit()
    finally:
        conn.close()

    _audit("acknowledged", ack_token, {"actor": actor, "alert_id": row["alert_id"]},
           row.get("classification"))
    return {"status": _STATUS_ACKED, "ack_token": ack_token, "acknowledged_by": actor}


def process_escalations(now: datetime | None = None, tenant_id: str | None = None) -> list[dict]:
    """Escalate every pending, past-deadline critical alert. Synchronous sweep.

    Intended to be called by an existing reflex/scheduler tick — NOT a daemon.
    For each due row: re-resolve escalation channels (routing-rules aware),
    flip status to ``escalated``, stamp ``escalated_at``, and audit. Returns the
    list of escalated entries (each with the channels it re-routed to).

    Deadline comparison is done in Python (compute-in-Python) rather than in SQL
    so it is dialect-independent and deterministic under an injected ``now``.
    """
    cutoff = _now(now)
    escalated: list[dict] = []

    conn = get_connection()
    try:
        _ensure_schema(conn)
        sql = f"SELECT * FROM {_TABLE} WHERE status = %s"
        params: tuple = (_STATUS_PENDING,)
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params = (_STATUS_PENDING, tenant_id)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

        for row in rows:
            deadline = _parse(row.get("ack_deadline"))
            if deadline is None or deadline > cutoff:
                continue

            # Re-resolve channels through the routing engine so escalation
            # honours the same (severity x component x tenant) rules. Fall back
            # to the stored escalation_channels list.
            try:
                stored = json.loads(row.get("escalation_channels") or "[]")
            except Exception:
                stored = []
            channels = resolve_channels(
                severity=row.get("severity"),
                component=row.get("component") or None,
                tenant_id=row.get("tenant_id"),
                default=stored,
            ) or stored

            conn.execute(
                f"UPDATE {_TABLE} SET status = %s, escalated_at = %s WHERE id = %s",
                (_STATUS_ESCALATED, _iso(cutoff), row["id"]),
            )
            conn.commit()

            entry = {
                "ack_token": row["id"],
                "alert_id": row["alert_id"],
                "severity": row.get("severity"),
                "component": row.get("component"),
                "tenant_id": row.get("tenant_id"),
                "channels": channels,
                "escalated_at": _iso(cutoff),
            }
            escalated.append(entry)
            _audit("escalated", row["id"], {
                "alert_id": row["alert_id"], "channels": channels,
                "tenant_id": row.get("tenant_id"),
            }, row.get("classification"))
    finally:
        conn.close()

    return escalated


def get_escalation(ack_token: str) -> dict | None:
    """Return the escalation row for a token, or ``None``."""
    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(f"SELECT * FROM {_TABLE} WHERE id = %s", (ack_token,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def ack_link(ack_token: str, base_url: str | None = None) -> str:
    """Build the one-click acknowledgement link for a token.

    Clicking it should hit an API route that calls :func:`acknowledge`. When no
    base URL is configured the bare relative path is returned so callers can
    prefix their own host.
    """
    base = base_url if base_url is not None else (_escalation_cfg().get("ack_base_url") or "")
    base = base.rstrip("/")
    path = f"/api/notifications/ack/{ack_token}"
    return f"{base}{path}" if base else path
