# CUI // SP-CTI
"""NDC Canvas event emitter — publishes canvas_events on topology/config changes.

All functions are fire-and-forget: they wrap in try/except so a failure here
never blocks the primary NDC operation. Uses the PG-primary get_connection()
(tools.db.storage), NOT the network canvas SQLite connection.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SOURCE = "ndc"
_TARGET = "dic"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event_type: str, payload: dict[str, Any],
          tenant_id: str = "", classification: str = "CUI") -> bool:
    """Insert one row into canvas_events. Returns True on success, False on failure."""
    try:
        event_id = f"ce-{uuid.uuid4().hex[:16]}"
        # canvas_events (migration 039) has no tenant_id/classification
        # columns; carry the security context inside payload_json and use
        # the RLS-disabled canvas connection (mirrors tools/canvas/event_bus.py,
        # PR #720) so get_connection()'s predicate injection can't raise
        # UndefinedColumn on PostgreSQL.
        payload = {**payload, "_security_context":
                   {"tenant_id": tenant_id, "clearance": classification}}
        payload_json = json.dumps(payload)
        with get_canvas_connection() as conn:
            conn.execute(
                """
                INSERT INTO canvas_events
                    (id, source_canvas, target_canvas, event_type,
                     payload_json, created_at, consumed_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL)
                """,
                (event_id, _SOURCE, _TARGET, event_type,
                 payload_json, _now()),
            )
            conn.commit()
        logger.debug("ndc_event_emitter: emitted %s (%s)", event_type, event_id)
        return True
    except Exception as exc:
        logger.warning("ndc_event_emitter: failed to emit %s: %s", event_type, exc)
        return False


# ── Public emit functions ─────────────────────────────────────────────────────

def emit_topology_change(
    topology_id: str,
    change_summary: str,
    affected_segments: list[str] | None = None,
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit ndc.topology.drift_detected when a topology is created/updated."""
    return _emit(
        "ndc.topology.drift_detected",
        {
            "device_id": topology_id,
            "change_summary": change_summary,
            "affected_segments": affected_segments or [],
        },
        tenant_id=tenant_id,
        classification=classification,
    )


def emit_config_push(
    topology_id: str,
    change_summary: str,
    affected_segments: list[str] | None = None,
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit ndc.config.push when device configs are generated/exported for deployment."""
    return _emit(
        "ndc.config.push",
        {
            "device_id": topology_id,
            "change_summary": change_summary,
            "affected_segments": affected_segments or [],
        },
        tenant_id=tenant_id,
        classification=classification,
    )


def emit_baseline_deviation(
    topology_id: str,
    drifted_devices: list[dict],
    drifted_count: int,
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit ndc.baseline.deviation when running configs diverge from golden baseline."""
    device_ids = [d.get("device_id", "") for d in drifted_devices]
    summary = f"{drifted_count} device(s) deviate from golden baseline"
    return _emit(
        "ndc.baseline.deviation",
        {
            "device_id": topology_id,
            "change_summary": summary,
            "affected_segments": device_ids,
            "drifted_count": drifted_count,
        },
        tenant_id=tenant_id,
        classification=classification,
    )
