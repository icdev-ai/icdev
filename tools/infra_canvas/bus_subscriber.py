# CUI // SP-CTI
"""IDC Cross-Canvas Event Bus Subscriber.

Subscribes to:
  mdc.sop.approved  — when MDC migration SOP is approved → auto-create IDC design
  ndc.ipam.added    — when NDC adds a subnet/IPAM block → create IDC VPC resource
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register() -> None:
    """Register IDC event bus subscriptions. Call once at blueprint init."""
    try:
        from tools.canvas.event_bus import subscribe
        subscribe("idc", "mdc.sop.approved", _on_mdc_sop_approved)
        subscribe("idc", "ndc.ipam.added", _on_ndc_ipam_added)
        logger.info("idc.bus: subscriptions registered")
    except Exception as exc:
        logger.warning("idc.bus: could not register subscriptions: %s", exc)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _on_mdc_sop_approved(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """When an MDC migration SOP is approved, auto-create a linked IDC infrastructure design.

    The IDC design is pre-seeded with a graph noting the migration source design_id
    so the infrastructure team can immediately begin IaC design.
    """
    sop_id = payload.get("sop_id", "")
    mdc_design_id = payload.get("design_id", "")
    classification = payload.get("classification", "CUI")
    sop_type = payload.get("sop_type", "migration")
    approved_by = payload.get("approved_by", "system")

    if not sop_id:
        return
    logger.info("idc.bus: mdc.sop.approved sop=%s mdc_design=%s", sop_id, mdc_design_id)

    design_id = str(uuid.uuid4())
    graph = {
        "nodes": [
            {
                "id": f"mdc-ref-{mdc_design_id[:8]}",
                "type": "migration-source",
                "label": f"MDC Design {mdc_design_id[:8]}",
                "data": {"mdc_design_id": mdc_design_id, "sop_id": sop_id},
            }
        ],
        "edges": [],
    }
    try:
        from tools.infra_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            # Skip if an IDC design already references this SOP
            existing = conn.execute(
                "SELECT id FROM infra_designs WHERE name LIKE %s",
                (f"%{sop_id[:8]}%",),
            ).fetchone()
            if existing:
                logger.debug("idc.bus: design for sop %s already exists", sop_id)
                return
            conn.execute(
                "INSERT INTO infra_designs "
                "(id, name, description, graph_json, classification, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    design_id,
                    f"Infrastructure for Migration SOP {sop_id[:8]}",
                    f"Auto-created from MDC SOP approval. Approved by: {approved_by}. "
                    f"SOP type: {sop_type}. MDC design: {mdc_design_id}.",
                    json.dumps(graph),
                    classification,
                    _now(),
                    _now(),
                ),
            )
            conn.commit()
            logger.info("idc.bus: created infra_design %s for sop %s", design_id, sop_id)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("idc.bus: infra design creation failed: %s", exc)


def _on_ndc_ipam_added(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """When NDC adds a subnet/IPAM block, create a corresponding IDC infrastructure resource.

    Maps NDC subnet → IDC VPC/subnet resource so IaC design reflects live network topology.
    """
    block_id = payload.get("block_id", "")
    network = payload.get("network", "")
    topology_id = payload.get("topology_id", "")
    classification = payload.get("classification", "CUI")
    vrf = payload.get("vrf", "global")
    vlan_id = payload.get("vlan_id")

    if not network:
        return
    logger.info("idc.bus: ndc.ipam.added network=%s topology=%s", network, topology_id)

    try:
        from tools.infra_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO idc_infra_resources "
                "(csp, region, resource_type, resource_name, classification, tags, config, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "ndc",
                    vrf,
                    "subnet",
                    network,
                    classification,
                    json.dumps({"vlan_id": vlan_id, "topology_id": topology_id,
                                "ndc_block_id": block_id}),
                    json.dumps({"cidr": network, "vrf": vrf, "source": "ndc_ipam"}),
                    _now(),
                ),
            )
            conn.commit()
            logger.info("idc.bus: created idc_infra_resource for subnet %s", network)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("idc.bus: idc resource creation failed: %s", exc)
