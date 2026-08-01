# CUI // SP-CTI — Twin Core ↔ cross-canvas event bus bridge (twx-bus-01)
"""Wire the additive twin_core layer into the cross-canvas event bus.

Before this bridge, zero twin modules touched ``tools/canvas/event_bus.py`` —
the eight twins were isolated islands. This module turns them into a *system
twin* with a deliberately **small** taxonomy (extend later, per the card):

Twin lifecycle events (published BY twins, through the registry facade):
  * ``twin_snapshot_taken``       — a twin froze a snapshot
  * ``twin_simulation_completed`` — a twin produced a canonical verdict

First two cross-canvas subscriptions with real value (twins react to each other):
  * PDC ``pipeline_deployed``        → refresh the SDC attack-path twin
  * SDC ``sdc_threat_model_changed`` → re-run the BDC crosswalk-drift twin

All publishing honors the bus's existing classification-aware security-context
propagation (clearance downgrade / cross-tenant blocking) — we pass a
``security_context`` straight through and never bypass it. Publishing is
best-effort: a bus failure never breaks the underlying twin call.

The registry facade (:func:`simulate`, :func:`snapshot`) is the "automatic"
publish path — any caller that drives a registered twin through the facade emits
the lifecycle event with the canonical verdict payload, with no change to the
eight twins themselves.
"""
from __future__ import annotations

import os
from typing import Any

from tools.logging.icdev_logger import get_logger
from tools.twin_core.registry import TwinRegistry
from tools.twin_core.schema import worst_severity

logger = get_logger("icdev.twin_core.event_bridge")

# ── Small event taxonomy ──────────────────────────────────────────────────────
TWIN_SNAPSHOT_TAKEN = "twin_snapshot_taken"
TWIN_SIMULATION_COMPLETED = "twin_simulation_completed"
TWIN_EVENT_TYPES = (TWIN_SNAPSHOT_TAKEN, TWIN_SIMULATION_COMPLETED)

# Cross-canvas trigger events this bridge subscribes twins to.
EVENT_PIPELINE_DEPLOYED = "pipeline_deployed"          # published by PDC
EVENT_THREAT_MODEL_CHANGED = "sdc_threat_model_changed"  # published by SDC

_MAX_VIOLATIONS_IN_PAYLOAD = 10

_subscriptions_registered = False


# ── publish helpers ───────────────────────────────────────────────────────────

def _publish(source_canvas: str, event_type: str, payload: dict, *,
             target_canvas: str | None = None, security_context: dict | None = None) -> str | None:
    """Best-effort publish to the cross-canvas bus. Never raises."""
    try:
        from tools.canvas.event_bus import publish

        return publish(source_canvas, event_type, payload,
                       target_canvas=target_canvas, security_context=security_context)
    except Exception as exc:  # noqa: BLE001 — bus failure must not break the twin
        logger.warning("twin_core.bus: publish %s from %s failed: %s", event_type, source_canvas, exc)
        return None


def publish_snapshot(canvas: str, target_id: str, snapshot: dict, *,
                     security_context: dict | None = None) -> str | None:
    """Publish a ``twin_snapshot_taken`` event for a completed snapshot."""
    payload = {
        "canvas": canvas,
        "target_id": target_id,
        "snapshot_id": snapshot.get("id") or snapshot.get("snapshot_id"),
        "label": snapshot.get("label"),
    }
    return _publish(canvas, TWIN_SNAPSHOT_TAKEN, payload, security_context=security_context)


def publish_simulation(canvas: str, target_id: str, envelope: dict, *,
                       target_canvas: str | None = None,
                       security_context: dict | None = None) -> str | None:
    """Publish a ``twin_simulation_completed`` event carrying the canonical verdict.

    The payload is the compact canonical summary (verdict + counts + top
    violations), not the full envelope — enough for subscribers and the
    observatory feed without shipping large graphs across the bus.
    """
    violations = envelope.get("violations", []) or []
    payload = {
        "canvas": canvas,
        "target_id": target_id,
        "verdict": envelope.get("verdict"),
        "method": envelope.get("method"),
        "simulation_id": envelope.get("simulation_id"),
        "counts": envelope.get("counts"),
        "worst_severity": worst_severity(violations),
        "violations": violations[:_MAX_VIOLATIONS_IN_PAYLOAD],
    }
    return _publish(canvas, TWIN_SIMULATION_COMPLETED, payload,
                    target_canvas=target_canvas, security_context=security_context)


# ── registry facade (the "automatic" publish path) ────────────────────────────

def snapshot(canvas: str, target_id: str, *, publish: bool = True,
             security_context: dict | None = None, **kwargs) -> dict:
    """Take a snapshot via the registered twin and (by default) publish the event."""
    adapter = TwinRegistry.get(canvas)
    if adapter is None:
        raise KeyError(f"No registered twin for canvas {canvas!r}")
    snap = adapter.take_snapshot(target_id, **kwargs)
    if publish:
        publish_snapshot(canvas, target_id, snap, security_context=security_context)
    return snap


def simulate(canvas: str, target_id: str, delta: Any, *, publish: bool = True,
             target_canvas: str | None = None, security_context: dict | None = None,
             **kwargs) -> dict:
    """Simulate via the registered twin and (by default) publish the canonical verdict."""
    adapter = TwinRegistry.get(canvas)
    if adapter is None:
        raise KeyError(f"No registered twin for canvas {canvas!r}")
    envelope = adapter.simulate_delta(target_id, delta, **kwargs)
    if publish:
        publish_simulation(canvas, target_id, envelope,
                           target_canvas=target_canvas, security_context=security_context)
    return envelope


# ── cross-canvas subscription handlers ────────────────────────────────────────

def _on_pipeline_deployed(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """PDC ``pipeline_deployed`` → refresh the SDC attack-path twin.

    A newly deployed pipeline can change the runtime attack surface, so we
    re-snapshot the associated security design. ``design_id`` (or ``sdc_design_id``)
    must be in the payload; without it there's nothing to refresh (logged, no-op).
    """
    design_id = payload.get("sdc_design_id") or payload.get("design_id")
    if not design_id:
        logger.debug("twin_core.bus: pipeline_deployed with no design_id; skip SDC refresh")
        return
    sec_ctx = payload.get("_security_context")
    try:
        adapter = TwinRegistry.get("sdc")
        if adapter is None:
            return
        snap = adapter.take_snapshot(design_id, label="auto-refresh-pipeline-deployed")
        logger.info("twin_core.bus: SDC attack-path twin refreshed for design %s (pipeline deploy)", design_id)
        publish_snapshot("sdc", design_id, snap, security_context=sec_ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("twin_core.bus: SDC refresh failed: %s", exc)


def _on_threat_model_changed(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """SDC ``sdc_threat_model_changed`` → re-run the BDC crosswalk-drift twin.

    A changed threat model can invalidate compliance-control crosswalks, so we
    re-run BDC crosswalk drift for the affected project. Requires ``project_id``;
    frameworks default to a FedRAMP Moderate → High crosswalk when unspecified.
    """
    project_id = payload.get("project_id") or payload.get("bdc_project_id")
    if not project_id:
        logger.debug("twin_core.bus: threat_model_changed with no project_id; skip BDC drift")
        return
    fw_src = payload.get("fw_src", "FedRAMP Moderate")
    fw_tgt = payload.get("fw_tgt", "FedRAMP High")
    sec_ctx = payload.get("_security_context")
    try:
        from tools.boundary_canvas import twin as bdc_twin

        drift = bdc_twin.crosswalk_drift(project_id, fw_src, fw_tgt)
        total = drift.get("total", len(drift.get("drifts", []) or []))
        logger.info("twin_core.bus: BDC crosswalk drift re-run for project %s (%s→%s): %s drifts",
                    project_id, fw_src, fw_tgt, total)
        _publish("bdc", TWIN_SIMULATION_COMPLETED, {
            "canvas": "bdc",
            "target_id": project_id,
            "verdict": "warn" if total else "pass",
            "method": "crosswalk-drift",
            "trigger": EVENT_THREAT_MODEL_CHANGED,
            "drift_total": total,
        }, security_context=sec_ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("twin_core.bus: BDC crosswalk drift failed: %s", exc)


def register_subscriptions(force: bool = False) -> bool:
    """Register the twin_core cross-canvas subscriptions (idempotent).

    Call once at startup (e.g. dashboard init). Returns True when registration
    ran, False when already registered or the bus is unavailable.
    """
    global _subscriptions_registered
    if _subscriptions_registered and not force:
        return False
    try:
        from tools.canvas.event_bus import subscribe

        # PDC pipeline deploy → SDC attack-path refresh. Subscribe under the SDC
        # canvas id so publishes targeting "sdc" (or from "pdc") reach it.
        subscribe("sdc", EVENT_PIPELINE_DEPLOYED, _on_pipeline_deployed)
        subscribe("pdc", EVENT_PIPELINE_DEPLOYED, _on_pipeline_deployed)
        # SDC threat-model change → BDC crosswalk drift re-run.
        subscribe("bdc", EVENT_THREAT_MODEL_CHANGED, _on_threat_model_changed)
        subscribe("sdc", EVENT_THREAT_MODEL_CHANGED, _on_threat_model_changed)
        _subscriptions_registered = True
        logger.info("twin_core.bus: cross-canvas subscriptions registered")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("twin_core.bus: could not register subscriptions: %s", exc)
        return False


# ── observatory feed + optional drift cards ───────────────────────────────────

def recent_twin_events(limit: int = 50) -> list[dict]:
    """Recent twin lifecycle events from ``canvas_events`` (for the Observatory feed).

    Best-effort: returns ``[]`` if the table is absent (fresh checkout). Filters
    to :data:`TWIN_EVENT_TYPES` only.
    """
    import json

    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            placeholders = ",".join(["%s"] * len(TWIN_EVENT_TYPES))
            rows = conn.execute(
                f"SELECT id, source_canvas, target_canvas, event_type, payload_json, created_at "
                f"FROM canvas_events WHERE event_type IN ({placeholders}) "  # nosec B608 — placeholders only
                f"ORDER BY created_at DESC LIMIT %s",
                (*TWIN_EVENT_TYPES, int(limit)),
            ).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {
                "id": r[0], "source_canvas": r[1], "target_canvas": r[2],
                "event_type": r[3], "payload_json": r[4], "created_at": r[5],
            }
            try:
                payload = json.loads(d.get("payload_json") or "{}")
            except (ValueError, TypeError):
                payload = {}
            payload.pop("_security_context", None)  # don't leak sec-ctx into the feed
            out.append({
                "id": d.get("id"),
                "source_canvas": d.get("source_canvas"),
                "target_canvas": d.get("target_canvas"),
                "event_type": d.get("event_type"),
                "created_at": d.get("created_at"),
                "verdict": payload.get("verdict"),
                "target_id": payload.get("target_id"),
                "worst_severity": payload.get("worst_severity"),
                "payload": payload,
            })
        return out
    except Exception as exc:  # noqa: BLE001 — table may not exist yet
        logger.debug("twin_core.bus: recent_twin_events unavailable: %s", exc)
        return []


def create_twin_drift_card(canvas: str, target_id: str, summary: str, detail: str) -> dict:
    """Create a **quarantined** ('suggested') kanban card for observed twin drift.

    Reuses the suggested-card quarantine convention (``status='suggested'``) so a
    twin-drift signal NEVER becomes an auto-scheduled task — it waits for HITL
    promotion. This is a library helper (obs-01 / a human invokes it); the bus
    handlers do NOT call it automatically. Off unless ``ICDEV_TWIN_DRIFT_CARDS``
    is truthy, to keep the default footprint zero.

    Returns ``{"created": bool, "task_id": str|None, "reason": str}``.
    """
    if not os.getenv("ICDEV_TWIN_DRIFT_CARDS"):
        return {"created": False, "task_id": None, "reason": "disabled (set ICDEV_TWIN_DRIFT_CARDS=1)"}
    import uuid
    from datetime import datetime, timezone

    task_id = f"twin-drift-{uuid.uuid4().hex[:8]}"
    title = f"[twin drift] {canvas}:{target_id} — {summary}"[:200]
    now = datetime.now(timezone.utc).isoformat()
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            # Quarantined: status='suggested' only. Never backlog/scheduled.
            conn.execute(
                "INSERT INTO kanban_tasks (id, title, description, status, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'suggested', %s, %s)",
                (task_id, title, detail, now, now),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("twin_core.bus: quarantined twin-drift card %s created", task_id)
        return {"created": True, "task_id": task_id, "reason": "quarantined (suggested)"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("twin_core.bus: drift card creation failed: %s", exc)
        return {"created": False, "task_id": None, "reason": str(exc)}
