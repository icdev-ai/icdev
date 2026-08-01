# CUI // SP-CTI
"""SIPA integrity canvas event emitter — canvas_events on vulnerability and quarantine.

Injection points:
  - vulnerability_found: called from engine.assess() when high/critical findings found.
  - quarantine_triggered: called when an artifact enters quarantine state.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SOURCE = "sipa"
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
        logger.debug("sipa_event_emitter: emitted %s (%s)", event_type, event_id)
        return True
    except Exception as exc:
        logger.warning("sipa_event_emitter: failed to emit %s: %s", event_type, exc)
        return False


def emit_vulnerability_found(
    file_path: str,
    finding_type: str,
    severity: str,
    assessment_id: str = "",
    project_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit sipa.vulnerability_found when a high/critical SIPA finding is recorded."""
    return _emit(
        "sipa.vulnerability_found",
        {
            "device_id": project_id or assessment_id,
            "file_path": file_path,
            "finding_type": finding_type,
            "severity": severity,
            "assessment_id": assessment_id,
            "change_summary": (
                f"SIPA finding: {finding_type} in {file_path} (severity={severity})"
            ),
            "affected_segments": [file_path],
        },
        tenant_id=tenant_id,
        classification=classification,
    )


def emit_quarantine_triggered(
    file_path: str,
    assessment_id: str = "",
    reason: str = "",
    project_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit sipa.quarantine_triggered when an artifact is quarantined."""
    return _emit(
        "sipa.quarantine_triggered",
        {
            "device_id": project_id or assessment_id,
            "file_path": file_path,
            "assessment_id": assessment_id,
            "reason": reason,
            "change_summary": f"SIPA quarantine triggered for {file_path}: {reason}",
            "affected_segments": [file_path],
        },
        tenant_id=tenant_id,
        classification=classification,
    )
