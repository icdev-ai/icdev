# CUI // SP-CTI
"""AADC Cross-Canvas Event Bus Subscriber.

Publishes:
  aadc.design.saved   — on every AADC design save
  aadc.agent.flagged  — when a design contains L5 (unconstrained) agents

Subscribes to:
  sdc.topology.saved  — pulls security context for AI nodes in linked SDC design
  odc.source.added    — syncs monitoring baseline for linked observability design
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register() -> None:
    """Register AADC event bus subscriptions. Call once at blueprint init."""
    try:
        from tools.canvas.event_bus import subscribe
        subscribe("aadc", "sdc.topology.saved", _on_sdc_topology_saved)
        subscribe("aadc", "odc.source.added", _on_odc_source_added)
        logger.info("aadc.bus: subscriptions registered")
    except Exception as exc:
        logger.warning("aadc.bus: could not register subscriptions: %s", exc)


def publish_design_saved(design_id: str, safety_impacting: bool,
                         rights_impacting: bool, autonomy_max: int) -> None:
    try:
        from tools.canvas.event_bus import publish
        publish("aadc", "aadc.design.saved", {
            "design_id": design_id,
            "safety_impacting": safety_impacting,
            "rights_impacting": rights_impacting,
            "autonomy_max": autonomy_max,
        })
    except Exception as exc:
        logger.debug("aadc.bus: publish_design_saved: %s", exc)


def publish_agent_flagged(design_id: str, agent_node_id: str,
                          autonomy_level: int, gaps: list[str]) -> None:
    try:
        from tools.canvas.event_bus import publish
        publish("aadc", "aadc.agent.flagged", {
            "design_id": design_id,
            "agent_node_id": agent_node_id,
            "autonomy_level": autonomy_level,
            "gaps": gaps,
        })
    except Exception as exc:
        logger.debug("aadc.bus: publish_agent_flagged: %s", exc)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _on_sdc_topology_saved(payload: dict) -> None:
    """When SDC saves a topology, if it's linked to an AADC design, update security context."""
    sdc_design_id = payload.get("design_id", "")
    logger.debug("aadc.bus: sdc.topology.saved received for %s", sdc_design_id)
    # Future: query aadc_designs for designs linked to this SDC design and re-assess


def _on_odc_source_added(payload: dict) -> None:
    """When ODC adds a log source, sync monitoring baseline for linked AI nodes."""
    odc_source = payload.get("source_id", "")
    logger.debug("aadc.bus: odc.source.added received for %s", odc_source)
    # Future: find AADC drift-detector / baseline-snapshot nodes linked to this ODC source
