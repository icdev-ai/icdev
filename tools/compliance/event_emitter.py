# CUI // SP-CTI
"""Compliance Canvas event emitter — canvas_events on finding creation and POAM overdue.

Injection points:
  - finding_created: called from _ivv_persist_finding() for CAT1/CAT2 findings.
  - poam_overdue: called when a POAM item passes scheduled completion without closure.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SOURCE = "compliance"
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
        logger.debug("compliance_event_emitter: emitted %s (%s)", event_type, event_id)
        return True
    except Exception as exc:
        logger.warning("compliance_event_emitter: failed to emit %s: %s", event_type, exc)
        return False


def emit_finding_created(
    control_id: str,
    finding_severity: str,
    project_id: str = "",
    framework: str = "",
    finding_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit compliance.finding_created when a CAT1/CAT2 finding is recorded."""
    return _emit(
        "compliance.finding_created",
        {
            "device_id": project_id,
            "control_id": control_id,
            "finding_severity": finding_severity,
            "framework": framework,
            "finding_id": finding_id,
            "change_summary": (
                f"Compliance finding created: {control_id} "
                f"(severity={finding_severity}, framework={framework})"
            ),
            "affected_segments": [control_id],
        },
        tenant_id=tenant_id,
        classification=classification,
    )


def emit_poam_overdue(
    poam_id: str,
    control_id: str,
    scheduled_completion: str,
    project_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit compliance.poam_overdue when a POAM item passes its due date unclosed."""
    return _emit(
        "compliance.poam_overdue",
        {
            "device_id": project_id,
            "control_id": control_id,
            "poam_id": poam_id,
            "scheduled_completion": scheduled_completion,
            "change_summary": (
                f"POAM item {poam_id} overdue: {control_id} "
                f"was due {scheduled_completion}"
            ),
            "affected_segments": [control_id],
        },
        tenant_id=tenant_id,
        classification=classification,
    )
