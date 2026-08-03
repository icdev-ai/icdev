# CUI // SP-CTI
"""AADC Safety Layer — Circuit Breaker node execution for AI Security Monitor.

Provides per-design circuit breaker instances that map to the `circuit-breaker`
node type in AADC designs.  When the AI Security Monitor fires anomaly events,
this layer decides whether to allow continued AI agent execution or trip the
circuit and escalate to HITL + SIEM.

Public API:
    record_event(design_id, event_type, detail, node_id)
        event_type: "anomaly" | "success" | "reset"
    get_status(design_id) -> dict
    reset_circuit(design_id) -> dict
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Per-design circuit breaker registry
# ---------------------------------------------------------------------------
# Maps design_id -> InMemoryCircuitBreaker instance (in-process singleton).
# State is intentionally not persisted — a process restart always starts in
# CLOSED state.  Long-lived persistent state belongs in a time-series DB; the
# aadc_audit table gives the audit trail.
_registry: dict[str, object] = {}
_registry_lock = threading.Lock()

_DEFAULT_THRESHOLD = 3    # anomaly events before tripping
_DEFAULT_RECOVERY = 60    # seconds before HALF_OPEN probe


def _get_or_create(design_id: str):
    """Return the circuit breaker for this design, creating it if needed."""
    with _registry_lock:
        if design_id not in _registry:
            from tools.resilience.circuit_breaker import InMemoryCircuitBreaker
            _registry[design_id] = InMemoryCircuitBreaker(
                service_name=f"aadc-safety-{design_id}",
                failure_threshold=_DEFAULT_THRESHOLD,
                recovery_timeout_seconds=_DEFAULT_RECOVERY,
                half_open_max_calls=1,
            )
        return _registry[design_id]


# ---------------------------------------------------------------------------
# Audit logger
# ---------------------------------------------------------------------------

def _audit(design_id: str, action: str, detail: str) -> None:
    """Append an event to aadc_audit (best-effort; never raises)."""
    try:
        from tools.agentic_ai_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO aadc_audit (id, design_id, action, detail, classification, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (f"sl-{uuid.uuid4().hex[:8]}", design_id, action, detail,
                 "CUI", datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("safety_layer: audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# SIEM forwarder shim
# ---------------------------------------------------------------------------

def _forward_siem(design_id: str, event_type: str, state: str, detail: str) -> None:
    """Forward a security event to SIEM forwarder (best-effort)."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            # Write to siem_events if table exists; silently skip if not.
            conn.execute(
                "INSERT INTO siem_events (id, source, event_type, severity, detail, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (f"se-{uuid.uuid4().hex[:8]}",
                 f"aadc-safety:{design_id}",
                 event_type,
                 "HIGH" if state == "open" else "INFO",
                 detail,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Table may not exist in all deployments — that's fine
        logger.warning("_forward_siem: best-effort INSERT into siem_events failed (non-blocking): %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_event(
    design_id: str,
    event_type: str,
    detail: str = "",
    node_id: Optional[str] = None,
) -> dict:
    """Record an anomaly or success event for the design's circuit breaker.

    Args:
        design_id:  AADC design ID (e.g. 'aadc-f75b628f').
        event_type: 'anomaly' | 'success' | 'reset'.
        detail:     Human-readable description of the event.
        node_id:    Optional ID of the circuit-breaker node in the design graph.

    Returns:
        dict with keys: state, allowed, tripped (bool), detail.
    """
    cb = _get_or_create(design_id)
    prev_state = cb.get_state().value
    tripped = False

    if event_type == "anomaly":
        allowed = cb.allow_request()
        cb.record_failure()
        new_state = cb.get_state().value
        tripped = (prev_state != "open" and new_state == "open")

        audit_action = "circuit_breaker:tripped" if tripped else "circuit_breaker:anomaly"
        audit_detail = (
            f"Circuit TRIPPED after anomaly — {detail}" if tripped
            else f"Anomaly recorded — {detail}"
        )
        _audit(design_id, audit_action, audit_detail)

        if tripped:
            _forward_siem(design_id, "CIRCUIT_BREAKER_TRIPPED", new_state, audit_detail)
            logger.warning(
                "safety_layer: circuit breaker TRIPPED for design %s — %s",
                design_id, detail,
            )

    elif event_type == "success":
        allowed = True
        cb.record_success()
        new_state = cb.get_state().value
        _audit(design_id, "circuit_breaker:success", detail or "Success recorded")

    elif event_type == "reset":
        cb.reset()
        new_state = cb.get_state().value
        allowed = True
        _audit(design_id, "circuit_breaker:reset", detail or "Manual reset")
        logger.info("safety_layer: circuit breaker RESET for design %s", design_id)

    else:
        return {"error": f"unknown event_type: {event_type}"}

    stats = cb.get_stats()
    return {
        "design_id": design_id,
        "node_id": node_id,
        "event_type": event_type,
        "state": stats["state"],
        "allowed": allowed,
        "tripped": tripped,
        "failure_count": stats["failure_count"],
        "failure_threshold": stats["failure_threshold"],
        "detail": detail,
    }


def get_status(design_id: str) -> dict:
    """Return current circuit breaker status for a design."""
    cb = _get_or_create(design_id)
    stats = cb.get_stats()
    state = stats["state"]

    # Derive human-readable recommendation
    if state == "open":
        recommendation = (
            "Circuit is OPEN — AI agent execution halted. "
            "Awaiting HITL review before resuming. "
            f"Circuit will probe again in {stats['recovery_timeout_seconds']}s."
        )
    elif state == "half_open":
        recommendation = (
            "Circuit is HALF_OPEN — limited probe requests allowed. "
            "Record successes to restore normal operation."
        )
    else:
        recommendation = "Circuit is CLOSED — normal operation."

    return {
        "design_id": design_id,
        "state": state,
        "failure_count": stats["failure_count"],
        "success_count": stats["success_count"],
        "failure_threshold": stats["failure_threshold"],
        "recovery_timeout_seconds": stats["recovery_timeout_seconds"],
        "recommendation": recommendation,
    }


def reset_circuit(design_id: str) -> dict:
    """Force-reset the circuit breaker to CLOSED state."""
    return record_event(design_id, "reset", "CAIO manual reset")
