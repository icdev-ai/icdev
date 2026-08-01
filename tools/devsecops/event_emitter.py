# CUI // SP-CTI
"""DevSecOps Canvas event emitter — canvas_events on pipeline fail and STIG finding.

Injection points:
  - pipeline_stage_failed: called when generate_security_stages() records a failed stage.
  - stig_finding_new: called when a new STIG finding is added to audit results.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SOURCE = "devsecops"
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
        logger.debug("devsecops_event_emitter: emitted %s (%s)", event_type, event_id)
        return True
    except Exception as exc:
        logger.warning("devsecops_event_emitter: failed to emit %s: %s", event_type, exc)
        return False


def emit_pipeline_stage_failed(
    pipeline_id: str,
    stage_name: str,
    severity: str = "high",
    change_summary: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit devsecops.pipeline_stage_failed when a CI/CD pipeline stage fails."""
    return _emit(
        "devsecops.pipeline_stage_failed",
        {
            "device_id": pipeline_id,
            "pipeline_id": pipeline_id,
            "stage_name": stage_name,
            "severity": severity,
            "change_summary": change_summary or f"Pipeline stage '{stage_name}' failed",
            "affected_segments": [stage_name],
        },
        tenant_id=tenant_id,
        classification=classification,
    )


def emit_stig_finding_new(
    pipeline_id: str,
    stage_name: str,
    severity: str = "CAT1",
    control_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit devsecops.stig_finding_new when a new STIG finding is added."""
    return _emit(
        "devsecops.stig_finding_new",
        {
            "device_id": pipeline_id,
            "pipeline_id": pipeline_id,
            "stage_name": stage_name,
            "control_id": control_id,
            "severity": severity,
            "change_summary": (
                f"New STIG finding {severity} in pipeline '{pipeline_id}' "
                f"(stage={stage_name}, control={control_id})"
            ),
            "affected_segments": [stage_name],
        },
        tenant_id=tenant_id,
        classification=classification,
    )
