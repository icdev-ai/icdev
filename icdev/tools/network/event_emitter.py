# CUI // SP-CTI
"""Network Canvas event emitter — canvas_events on migration phase and anomaly.

Fire-and-forget: all functions are wrapped in try/except so a DB failure
never blocks the primary Network Canvas operation.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SOURCE = "network"
_TARGET = "dic"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event_type: str, payload: dict[str, Any],
          tenant_id: str = "", classification: str = "CUI") -> bool:
    try:
        event_id = f"ce-{uuid.uuid4().hex[:16]}"
        # canvas_events (migration 039) has no tenant_id/classification
        # columns; carry the security context inside payload_json and use
        # the RLS-disabled canvas connection (mirrors tools/canvas/event_bus.py,
        # PR #720) so get_connection()'s predicate injection can't raise
        # UndefinedColumn on PostgreSQL.
        payload = {**payload, "_security_context":
                   {"tenant_id": tenant_id, "clearance": classification}}
        with get_canvas_connection() as conn:
            conn.execute(
                """
                INSERT INTO canvas_events
                    (id, source_canvas, target_canvas, event_type,
                     payload_json, created_at, consumed_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL)
                """,
                (event_id, _SOURCE, _TARGET, event_type,
                 json.dumps(payload), _now()),
            )
            conn.commit()
        logger.debug("network_event_emitter: emitted %s (%s)", event_type, event_id)
        return True
    except Exception as exc:
        logger.warning("network_event_emitter: failed to emit %s: %s", event_type, exc)
        return False


def emit_migration_phase_complete(
    phase_id: str,
    migration_id: str,
    phase_name: str = "",
    severity: str = "info",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit network.migration_phase_complete when a phase transitions to 'completed'."""
    return _emit(
        "network.migration_phase_complete",
        {
            "device_id": migration_id,
            "phase_name": phase_name,
            "migration_id": migration_id,
            "phase_id": phase_id,
            "severity": severity,
            "change_summary": f"Migration phase '{phase_name}' completed",
            "affected_segments": [],
        },
        tenant_id=tenant_id,
        classification=classification,
    )


def emit_anomaly_detected(
    migration_id: str,
    anomaly_type: str,
    severity: str = "medium",
    change_summary: str = "",
    affected_segments: list[str] | None = None,
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit network.anomaly_detected when migration analysis flags a deviation."""
    return _emit(
        "network.anomaly_detected",
        {
            "device_id": migration_id,
            "migration_id": migration_id,
            "anomaly_type": anomaly_type,
            "severity": severity,
            "change_summary": change_summary or f"Anomaly detected: {anomaly_type}",
            "affected_segments": affected_segments or [],
        },
        tenant_id=tenant_id,
        classification=classification,
    )
