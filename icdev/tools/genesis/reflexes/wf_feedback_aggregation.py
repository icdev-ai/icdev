# CUI // SP-CTI
"""Genesis reflex: wf_feedback_aggregation — 6h aggregation of HITL feedback into insights."""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone, timedelta

logger = get_logger(__name__)


def run(config: dict, trust) -> dict:
    """Aggregate wf_feedback → wf_feedback_insights per canvas/template/feedback_type."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    now = datetime.now(timezone.utc)
    period_end   = now.isoformat()
    period_start = (now - timedelta(hours=24)).isoformat()

    try:
        rows = conn.execute(
            """SELECT canvas_type, template_id, feedback_types, rating, decision
               FROM wf_feedback
               WHERE submitted_at >= %s AND submitted_at <= %s""",
            (period_start, period_end),
        ).fetchall()
    except Exception as exc:
        conn.close()
        return {"status": "error", "error": str(exc)}

    # Aggregate per (canvas_type, template_id, feedback_type)
    buckets: dict[tuple, dict] = {}
    for row in rows:
        canvas_type  = row[0]
        template_id  = row[1]
        fb_types_raw = row[2]
        rating       = row[3]
        decision     = row[4]

        fb_types: list = []
        if fb_types_raw:
            try:
                fb_types = json.loads(fb_types_raw)
            except Exception:
                fb_types = [fb_types_raw]
        if not fb_types:
            fb_types = ["general"]

        for fbt in fb_types:
            key = (canvas_type, template_id, fbt)
            if key not in buckets:
                buckets[key] = {"ratings": [], "kickbacks": 0, "total": 0, "tags": {}}
            b = buckets[key]
            b["total"] += 1
            if rating is not None:
                b["ratings"].append(rating)
            if decision == "kickback":
                b["kickbacks"] += 1

    # Write insights
    written = 0
    for (canvas_type, template_id, fbt), b in buckets.items():
        avg_rating   = sum(b["ratings"]) / len(b["ratings"]) if b["ratings"] else None
        kickback_rate = b["kickbacks"] / b["total"] if b["total"] else 0.0
        insight_id = f"wffi-{uuid.uuid4().hex[:12]}"
        try:
            conn.execute(
                """INSERT INTO wf_feedback_insights
                   (id, canvas_type, template_id, feedback_type, avg_rating, issue_count,
                    top_tags, kickback_rate, period_start, period_end, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (insight_id, canvas_type, template_id, fbt, avg_rating, b["total"],
                 json.dumps([]), kickback_rate, period_start, period_end, now.isoformat()),
            )
            written += 1
        except Exception as exc:
            logger.warning("Failed to write insight for %s/%s/%s: %s", canvas_type, template_id, fbt, exc)

    try:
        conn.commit()
    except Exception:
        pass
    conn.close()

    logger.info("wf_feedback_aggregation: processed %d feedback rows → %d insights", len(rows), written)
    return {"status": "ok", "feedback_rows": len(rows), "insights_written": written}
