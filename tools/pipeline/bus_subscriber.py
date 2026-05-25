# CUI // SP-CTI
"""PDC Cross-Canvas Event Bus Subscriber.

Subscribes to:
  qdc.gate.executed — when QDC runs a quality gate → write pc_compliance_findings
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import uuid
from datetime import datetime, timezone

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register() -> None:
    """Register PDC event bus subscriptions. Call once at blueprint init."""
    try:
        from tools.canvas.event_bus import subscribe
        subscribe("pdc", "qdc.gate.executed", _on_qdc_gate_executed)
        logger.info("pdc.bus: subscriptions registered")
    except Exception as exc:
        logger.warning("pdc.bus: could not register subscriptions: %s", exc)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _on_qdc_gate_executed(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """When QDC executes a quality gate, record the result as a PDC compliance finding.

    A failing gate creates an 'open' finding with severity CAT2.
    A passing gate closes any existing open finding for the same gate_id.
    """
    gate_id = payload.get("gate_id", "")
    design_id = payload.get("design_id", "")
    passed = payload.get("passed", False)
    score = payload.get("score", 0)
    status = payload.get("status", "unknown")

    if not gate_id:
        return
    logger.info("pdc.bus: qdc.gate.executed gate=%s passed=%s", gate_id, passed)

    try:
        from tools.pipeline.db.init_db import get_connection
        conn = get_connection()
        try:
            # Find the most recent pipeline to attach findings to
            pipeline_row = conn.execute(
                "SELECT id FROM pipelines ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            pipeline_id = (pipeline_row[0] if isinstance(pipeline_row, (list, tuple))
                           else (pipeline_row["id"] if pipeline_row else None))

            if passed:
                # Close any open finding for this gate
                conn.execute(
                    "UPDATE pc_compliance_findings SET status='resolved', remediated_at=? "
                    "WHERE rule_id=? AND status='open'",
                    (_now(), f"qdc.{gate_id}"),
                )
            else:
                # Insert open finding if none exists
                existing = conn.execute(
                    "SELECT id FROM pc_compliance_findings WHERE rule_id=? AND status='open'",
                    (f"qdc.{gate_id}",),
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO pc_compliance_findings "
                        "(id, pipeline_id, rule_id, framework, severity, title, description, "
                        "affected_entity, affected_type, status, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            str(uuid.uuid4()),
                            pipeline_id,
                            f"qdc.{gate_id}",
                            "QDC Quality Gate",
                            "CAT2",
                            f"Quality gate failed: {gate_id}",
                            f"QDC gate '{gate_id}' reported status={status}, score={score}. "
                            f"QDC design: {design_id}. Event: {event_id}.",
                            gate_id,
                            "quality_gate",
                            "open",
                            _now(),
                        ),
                    )
            conn.commit()
            logger.info("pdc.bus: compliance finding updated for gate %s (passed=%s)", gate_id, passed)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("pdc.bus: compliance finding update failed: %s", exc)
