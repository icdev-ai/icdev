# CUI // SP-CTI
"""PDC Cross-Canvas Event Bus Subscriber.

Subscribes to:
  qdc.gate.executed — when QDC runs a quality gate → write pc_compliance_findings
  pdc.pipeline.stale — when the pdc_pipeline_stale reflex flags a stale pipeline
                       → write a pc_compliance_findings row (visible artifact)
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
        subscribe("pdc", "pdc.pipeline.stale", _on_pdc_pipeline_stale)
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
            # Resolve the target pipeline from an explicit CORRELATION field in
            # the event payload. QDC publishes design_id (a QDC design, NOT a PDC
            # pipeline) and MAY publish pipeline_id. We attribute the finding ONLY
            # when a correlation id resolves to a real pipeline row; we NEVER fall
            # back to "most recently updated" (the old behaviour), which silently
            # mis-attributed every finding to whatever pipeline anyone touched
            # last. If nothing resolves, pipeline_id stays NULL (the column is
            # nullable) — an honest "unattributed" finding beats a wrong guess.
            pipeline_id = None
            for _key in ("pipeline_id", "design_id"):
                _cand = payload.get(_key)
                if not _cand:
                    continue
                _prow = conn.execute(
                    "SELECT id FROM pipelines WHERE id=%s", (_cand,)
                ).fetchone()
                if _prow:
                    pipeline_id = (_prow[0] if isinstance(_prow, (list, tuple))
                                   else _prow["id"])
                    break

            if passed:
                # Close any open finding for this gate
                conn.execute(
                    "UPDATE pc_compliance_findings SET status='resolved', remediated_at=%s "
                    "WHERE rule_id=%s AND status='open'",
                    (_now(), f"qdc.{gate_id}"),
                )
            else:
                # Insert open finding if none exists
                existing = conn.execute(
                    "SELECT id FROM pc_compliance_findings WHERE rule_id=%s AND status='open'",
                    (f"qdc.{gate_id}",),
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO pc_compliance_findings "
                        "(id, pipeline_id, rule_id, framework, severity, title, description, "
                        "affected_entity, affected_type, status, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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


def _on_pdc_pipeline_stale(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """When the pdc_pipeline_stale reflex flags a stale pipeline, record a finding.

    Creates (idempotently) an 'open' pc_compliance_findings row of severity CAT3
    per stale pipeline so the staleness signal is visible in the PDC compliance
    surface. A rule_id keyed to the pipeline id keeps repeated cycles from
    duplicating findings while the pipeline stays stale.
    """
    pipeline_id = payload.get("pipeline_id")
    pipeline_name = payload.get("pipeline_name", "")
    days_since_update = payload.get("days_since_update", 0)

    if not pipeline_id:
        return
    logger.info(
        "pdc.bus: pdc.pipeline.stale pipeline=%s days=%s", pipeline_id, days_since_update
    )

    rule_id = f"pdc.pipeline.stale.{pipeline_id}"
    try:
        from tools.pipeline.db.init_db import get_connection
        conn = get_connection()
        try:
            existing = conn.execute(
                "SELECT id FROM pc_compliance_findings WHERE rule_id=%s AND status='open'",
                (rule_id,),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO pc_compliance_findings "
                    "(id, pipeline_id, rule_id, framework, severity, title, description, "
                    "affected_entity, affected_type, status, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        str(uuid.uuid4()),
                        pipeline_id,
                        rule_id,
                        "PDC Pipeline Health",
                        "CAT3",
                        f"Pipeline stale: {pipeline_name or pipeline_id}",
                        f"Pipeline '{pipeline_name or pipeline_id}' has not been updated in "
                        f"{days_since_update} days (flagged by pdc_pipeline_stale reflex). "
                        f"Event: {event_id}.",
                        str(pipeline_id),
                        "pipeline",
                        "open",
                        _now(),
                    ),
                )
                conn.commit()
                logger.info("pdc.bus: stale-pipeline finding recorded for %s", pipeline_id)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("pdc.bus: stale-pipeline finding update failed: %s", exc)
