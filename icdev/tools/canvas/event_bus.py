# CUI // SP-CTI
"""Cross-canvas event bus — publish/subscribe for canvas-to-canvas events.

publish(source_canvas, event_type, payload_dict, *, target_canvas, security_context) → writes a row to canvas_events.
subscribe(canvas_id, event_type, handler_fn, *, subscriber_context)     → registers an in-process listener.
dispatch_pending(canvas_id)                      → fires registered handlers for
                                                    unconsumed events targeted at canvas_id.
"""
from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable

from tools.compliance.classification_manager import get_clearance_order
from tools.db.storage import get_canvas_connection, get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.canvas.event_bus")

# ---------------------------------------------------------------------------
# New event types
# ---------------------------------------------------------------------------
EVENT_SECURITY_CONTEXT_PROPAGATED = "security_context_propagated"
EVENT_DOWNGRADED = "event_downgraded"
EVENT_BLOCKED = "event_blocked"

# ---------------------------------------------------------------------------
# Default security context for events and subscribers
# ---------------------------------------------------------------------------
_DEFAULT_SECURITY_CONTEXT = {
    "clearance": "CUI",
    "compartment": None,
    "tenant_id": None,
}

# ---------------------------------------------------------------------------
# In-process subscriber registry: {(canvas_id, event_type): [(handler_fn, subscriber_context), ...]}
# ---------------------------------------------------------------------------
_LISTENERS: dict[tuple[str, str], list[tuple[Callable, dict]]] = {}


def subscribe(
    canvas_id: str,
    event_type: str,
    handler_fn: Callable,
    *,
    subscriber_context: dict | None = None,
) -> None:
    ctx = {**_DEFAULT_SECURITY_CONTEXT, **(subscriber_context or {})}
    key = (canvas_id, event_type)
    _LISTENERS.setdefault(key, []).append((handler_fn, ctx))


def publish(
    source_canvas: str,
    event_type: str,
    payload_dict: dict,
    *,
    target_canvas: str | None = None,
    security_context: dict | None = None,
) -> str:
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Merge security context into payload
    sec_ctx = {**_DEFAULT_SECURITY_CONTEXT, **(security_context or {})}
    payload = {**payload_dict, "_security_context": sec_ctx}
    payload_json = json.dumps(payload)

    # canvas_events is a canvas table with NO tenant_id/classification columns
    # (migration 039), so it must use the RLS-disabled canvas connection.  A
    # normal get_connection() would inject the global tenant/classification RLS
    # predicate and raise UndefinedColumn on PostgreSQL, aborting the txn.
    conn = get_canvas_connection()
    try:
        conn.execute(
            """
            INSERT INTO canvas_events
                (id, source_canvas, target_canvas, event_type, payload_json, created_at, consumed_at)
            VALUES (%s, %s, %s, %s, %s, %s, NULL)
            """,
            (event_id, source_canvas, target_canvas, event_type, payload_json, now),
        )
        conn.commit()

        # Audit log the publish
        _audit_event(
            conn,
            action="event.publish",
            details={
                "event_id": event_id,
                "source_canvas": source_canvas,
                "target_canvas": target_canvas,
                "event_type": event_type,
                "security_context": sec_ctx,
            },
            classification=sec_ctx.get("clearance", "CUI"),
        )
    finally:
        conn.close()

    # Fire in-process listeners immediately
    _dispatch_to_listeners(source_canvas, event_type, event_id, payload)
    if target_canvas:
        _dispatch_to_listeners(target_canvas, event_type, event_id, payload)

    return event_id


def _audit_event(
    conn,
    action: str,
    details: dict,
    classification: str = "CUI",
) -> None:
    """Write an append-only audit entry for the event bus."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                None,
                "compliance_check",
                "icdev-event-bus",
                action,
                json.dumps(details),
                json.dumps([]),
                classification,
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Never let audit failure crash the bus
        logger.warning("_audit_event: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _downgrade_payload(payload: dict) -> dict:
    """Return a CUI-downgraded copy of the payload: strip TS fields, keep CUI."""
    result = deepcopy(payload)
    sec_ctx = result.get("_security_context", {})
    sec_ctx["clearance"] = "CUI"
    sec_ctx["compartment"] = None
    result["_security_context"] = sec_ctx
    result["_downgraded"] = True

    # Heuristic: remove keys that look TS-sensitive at top level
    ts_patterns = ("ts_", "top_secret_", "sci_", "secret_")
    keys_to_remove = [
        k for k in list(result.keys())
        if k not in ("_security_context", "_downgraded") and k.startswith(ts_patterns)
    ]
    for k in keys_to_remove:
        del result[k]

    # Recursively downgrade nested dict values
    for k, v in list(result.items()):
        if isinstance(v, dict):
            result[k] = _downgrade_payload(v)

    return result


def _dispatch_to_listeners(
    canvas_id: str, event_type: str, event_id: str, payload: dict
) -> None:
    event_sec_ctx = payload.get("_security_context", _DEFAULT_SECURITY_CONTEXT)
    event_clearance = event_sec_ctx.get("clearance", "CUI")
    event_tenant = event_sec_ctx.get("tenant_id")

    for key in ((canvas_id, event_type), (canvas_id, "*")):
        entries = _LISTENERS.get(key, [])
        for handler, sub_ctx in entries:
            sub_clearance = sub_ctx.get("clearance", "CUI")
            sub_tenant = sub_ctx.get("tenant_id")
            sub_admin = sub_ctx.get("is_admin", False)

            # Cross-tenant check
            if (
                event_tenant
                and sub_tenant
                and event_tenant != sub_tenant
                and not sub_admin
            ):
                conn = get_connection()
                try:
                    _audit_event(
                        conn,
                        action="event.blocked",
                        details={
                            "event_id": event_id,
                            "canvas_id": canvas_id,
                            "event_type": event_type,
                            "reason": "cross-tenant",
                            "event_tenant": event_tenant,
                            "subscriber_tenant": sub_tenant,
                            "subscriber_clearance": sub_clearance,
                        },
                        classification=event_clearance,
                    )
                finally:
                    conn.close()
                continue  # Blocked

            # Clearance verification
            if get_clearance_order(sub_clearance) < get_clearance_order(event_clearance):
                downgraded = _downgrade_payload(payload)
                conn = get_connection()
                try:
                    _audit_event(
                        conn,
                        action="event.downgraded",
                        details={
                            "event_id": event_id,
                            "canvas_id": canvas_id,
                            "event_type": event_type,
                            "from_clearance": event_clearance,
                            "to_clearance": "CUI",
                            "subscriber_clearance": sub_clearance,
                        },
                        classification="CUI",
                    )
                finally:
                    conn.close()
                try:
                    handler(event_id, canvas_id, EVENT_DOWNGRADED, downgraded)
                except Exception:  # noqa: BLE001
                    pass
                continue

            # Successful propagation
            conn = get_connection()
            try:
                _audit_event(
                    conn,
                    action="event.propagated",
                    details={
                        "event_id": event_id,
                        "canvas_id": canvas_id,
                        "event_type": event_type,
                        "subscriber_clearance": sub_clearance,
                        "subscriber_tenant": sub_tenant,
                        "security_context": event_sec_ctx,
                    },
                    classification=event_clearance,
                )
            finally:
                conn.close()
            try:
                handler(event_id, canvas_id, event_type, payload)
            except Exception:  # noqa: BLE001 — never let a subscriber crash the bus
                pass


def dispatch_pending(canvas_id: str) -> int:
    """Fire handlers for unconsumed events targeting canvas_id; mark them consumed."""
    # canvas_events lacks tenant_id/classification columns — use the RLS-disabled
    # canvas connection so the SELECT/UPDATE below are not rewritten with a
    # classification/tenant predicate (UndefinedColumn on PostgreSQL).
    conn = get_canvas_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, source_canvas, event_type, payload_json
            FROM canvas_events
            WHERE target_canvas = %s AND consumed_at IS NULL
            ORDER BY created_at
            """,
            (canvas_id,),
        ).fetchall()

        fired = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            event_id = row[0] if isinstance(row, (list, tuple)) else row["id"]
            _src = row[1] if isinstance(row, (list, tuple)) else row["source_canvas"]
            etype = row[2] if isinstance(row, (list, tuple)) else row["event_type"]
            payload = json.loads(
                row[3] if isinstance(row, (list, tuple)) else row["payload_json"]
            )
            _dispatch_to_listeners(canvas_id, etype, event_id, payload)
            conn.execute(
                "UPDATE canvas_events SET consumed_at = %s WHERE id = %s",
                (now, event_id),
            )
            fired += 1

        if fired:
            conn.commit()
        return fired
    finally:
        conn.close()
