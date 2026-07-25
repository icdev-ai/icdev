# CUI // SP-CTI
"""ZIG canvas event emitter — canvas_events on posture score drop and pillar gap.

Injection points:
  - posture_score_drop: called after score_all_pillars() when overall score
    decreases by >=5 points vs. previous assessment.
  - pillar_gap_detected: called when any pillar score is below threshold (<70).

Both are fire-and-forget: failures never block the primary ZIG operation.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SOURCE = "zig"
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
        logger.debug("zig_event_emitter: emitted %s (%s)", event_type, event_id)
        return True
    except Exception as exc:
        logger.warning("zig_event_emitter: failed to emit %s: %s", event_type, exc)
        return False


def emit_posture_score_drop(
    pillar_name: str,
    previous_score: float,
    current_score: float,
    project_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit zig.posture_score_drop when overall posture drops >=5 points."""
    drop = round(previous_score - current_score, 2)
    return _emit(
        "zig.posture_score_drop",
        {
            "device_id": project_id,
            "pillar_name": pillar_name,
            "previous_score": previous_score,
            "current_score": current_score,
            "score_drop": drop,
            "change_summary": (
                f"ZIG posture score dropped {drop:.1f} points "
                f"(pillar: {pillar_name}, {previous_score} → {current_score})"
            ),
            "affected_segments": [pillar_name],
        },
        tenant_id=tenant_id,
        classification=classification,
    )


def emit_pillar_gap_detected(
    pillar_name: str,
    current_score: float,
    threshold: float = 70.0,
    project_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Emit zig.pillar_gap_detected when a pillar score falls below threshold."""
    return _emit(
        "zig.pillar_gap_detected",
        {
            "device_id": project_id,
            "pillar_name": pillar_name,
            "current_score": current_score,
            "threshold": threshold,
            "change_summary": (
                f"ZIG pillar '{pillar_name}' scored {current_score:.1f} "
                f"(below threshold {threshold:.0f})"
            ),
            "affected_segments": [pillar_name],
        },
        tenant_id=tenant_id,
        classification=classification,
    )
