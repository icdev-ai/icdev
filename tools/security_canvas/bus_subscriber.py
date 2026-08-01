
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""SDC canvas event bus subscriber.

Listens for PDC pipeline_deployed events and:
  1. Marks sc_threats entries stale for designs linked to the deployed pipeline.
  2. Queues the Genesis 'audit' reflex for the next daemon cycle.
"""
from datetime import datetime, timezone

logger = get_logger("icdev.security_canvas.bus_subscriber")


def _handle_pipeline_deployed(
    event_id: str, source_canvas: str, event_type: str, payload: dict
) -> None:
    pipeline_id = payload.get("pipeline_id", "")
    if not pipeline_id:
        return

    # Mark threat model entries stale for designs linked to this pipeline
    try:
        from tools.security_canvas.db.init_db import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT design_id FROM sc_assessments "
                "WHERE source_entity_id = ? AND design_id IS NOT NULL",
                (pipeline_id,),
            ).fetchall()
            design_ids = [
                (r[0] if isinstance(r, (list, tuple)) else r["design_id"]) for r in rows
            ]
            if design_ids:
                placeholders = ",".join(["%s"] * len(design_ids))
                conn.execute(
                    f"UPDATE sc_threats SET is_stale = 1 WHERE design_id IN ({placeholders})",
                    design_ids,
                )
                conn.commit()
                logger.info(
                    "SDC: marked threats stale for %d design(s) linked to pipeline %s",
                    len(design_ids),
                    pipeline_id,
                )
            else:
                logger.debug(
                    "SDC: no designs linked to pipeline %s — nothing to mark stale",
                    pipeline_id,
                )
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            "SDC bus_subscriber: stale-mark failed for pipeline %s: %s", pipeline_id, exc
        )

    # Queue Genesis audit reflex by advancing its next_run_at to now
    try:
        from tools.db.storage import get_connection as get_main_conn

        now = datetime.now(timezone.utc).isoformat()
        conn = get_main_conn()
        try:
            conn.execute(
                """
                INSERT INTO genesis_reflex_state
                    (reflex_name, enabled, next_run_at, updated_at)
                VALUES ('audit', 1, ?, ?)
                ON CONFLICT(reflex_name) DO UPDATE SET
                    next_run_at = excluded.next_run_at,
                    updated_at  = excluded.updated_at
                """,
                (now, now),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info(
            "SDC: queued Genesis audit reflex for pipeline_deployed event %s", event_id
        )
    except Exception as exc:
        logger.warning("SDC bus_subscriber: genesis reflex queue failed: %s", exc)


def register() -> None:
    """Register PDC pipeline_deployed handler with the canvas event bus."""
    from tools.canvas.event_bus import subscribe

    subscribe("pdc", "pipeline_deployed", _handle_pipeline_deployed)
    logger.debug("SDC bus_subscriber: registered handler for pdc/pipeline_deployed")
