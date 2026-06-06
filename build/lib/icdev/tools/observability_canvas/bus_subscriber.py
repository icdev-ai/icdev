# CUI // SP-CTI
"""ODC Cross-Canvas Event Bus Subscriber.

Subscribes to:
  bdc.design.saved — when BDC saves a boundary design → inject an observability zone node
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from datetime import datetime, timezone

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register() -> None:
    """Register ODC event bus subscriptions. Call once at blueprint init."""
    try:
        from tools.canvas.event_bus import subscribe
        subscribe("odc", "bdc.design.saved", _on_bdc_design_saved)
        logger.info("odc.bus: subscriptions registered")
    except Exception as exc:
        logger.warning("odc.bus: could not register subscriptions: %s", exc)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _on_bdc_design_saved(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """When BDC saves a boundary design, inject an observability zone node into matching ODC designs.

    For each ODC design with the same classification, adds a 'boundary-zone' node
    referencing the BDC boundary, enabling coverage gap analysis against zone boundaries.
    """
    bdc_design_id = payload.get("design_id", "")
    classification = payload.get("classification", "CUI")
    graph_changed = payload.get("graph_changed", True)

    if not bdc_design_id or not graph_changed:
        return
    logger.info("odc.bus: bdc.design.saved bdc_design=%s", bdc_design_id)

    new_node = {
        "id": f"bdc-zone-{bdc_design_id[:8]}",
        "type": "boundary-zone",
        "label": f"BDC Boundary {bdc_design_id[:8]}",
        "data": {
            "bdc_design_id": bdc_design_id,
            "auto_added": True,
            "source_event": event_id,
        },
        "x": 50,
        "y": 50,
    }

    try:
        from tools.observability_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            designs = conn.execute(
                "SELECT id, graph_json FROM observability_designs WHERE classification=?",
                (classification,),
            ).fetchall()
            updated = 0
            for row in designs:
                odc_id = row["id"] if isinstance(row, dict) else row[0]
                graph_raw = row["graph_json"] if isinstance(row, dict) else row[1]
                graph = json.loads(graph_raw) if isinstance(graph_raw, str) else (graph_raw or {})
                nodes = graph.setdefault("nodes", [])
                # Skip if zone node already present for this BDC design
                if any(n.get("id") == new_node["id"] for n in nodes):
                    continue
                nodes.append(new_node)
                conn.execute(
                    "UPDATE observability_designs SET graph_json=?, updated_at=? WHERE id=?",
                    (json.dumps(graph), _now(), odc_id),
                )
                updated += 1
            if updated:
                conn.commit()
            logger.info("odc.bus: injected boundary-zone node into %d odc designs", updated)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("odc.bus: boundary zone injection failed: %s", exc)
