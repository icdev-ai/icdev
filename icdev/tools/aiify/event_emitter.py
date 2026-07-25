# CUI // SP-CTI
"""AI-ify Canvas event emitter — canvas_events on canvas score and gap detection.

Injection points:
  - canvas_scored: after run_scan() completes and the posture grade is determined.
  - gap_identified: after detect_score_anomalies() finds anomalies or when an
    opportunity is assessed as not built / partially built with high value.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SOURCE = "aiify"
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
        logger.debug("aiify_event_emitter: emitted %s (%s)", event_type, event_id)
        return True
    except Exception as exc:
        logger.warning("aiify_event_emitter: failed to emit %s: %s", event_type, exc)
        return False


def emit_canvas_scored(
    canvas_name: str,
    previous_grade: str,
    current_grade: str,
    current_score: float,
    scan_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit aiify.canvas_scored when AI-ify completes an assessment with grade change."""
    return _emit(
        "aiify.canvas_scored",
        {
            "device_id": canvas_name,
            "canvas_name": canvas_name,
            "previous_grade": previous_grade,
            "current_grade": current_grade,
            "current_score": current_score,
            "scan_id": scan_id,
            "change_summary": (
                f"AI-ify canvas '{canvas_name}' scored {current_score:.0f}/100 "
                f"(grade changed {previous_grade} → {current_grade})"
            ),
            "affected_segments": [canvas_name],
        },
        tenant_id=tenant_id,
        classification=classification,
    )


def emit_gap_identified(
    canvas_name: str,
    opportunity_id: str,
    gap_type: str,
    value_score: float,
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit aiify.gap_identified when a high-value unbuilt/partial opportunity is found."""
    return _emit(
        "aiify.gap_identified",
        {
            "device_id": canvas_name,
            "canvas_name": canvas_name,
            "opportunity_id": opportunity_id,
            "gap_type": gap_type,
            "value_score": value_score,
            "change_summary": (
                f"AI-ify gap identified in '{canvas_name}': "
                f"opportunity {opportunity_id} is {gap_type} (value={value_score:.2f})"
            ),
            "affected_segments": [canvas_name, gap_type],
        },
        tenant_id=tenant_id,
        classification=classification,
    )
