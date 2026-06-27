# CUI // SP-CTI
"""AADC Cross-Canvas Event Bus Subscriber.

Publishes:
  aadc.design.saved   — on every AADC design save
  aadc.agent.flagged  — when a design contains L5 (unconstrained) agents

Subscribes to:
  sdc.topology.saved       — pulls security context for AI nodes in linked SDC design
  odc.source.added         — syncs monitoring baseline for linked observability design
  aimc.model.deprecated    — flags orphaned model refs when an AIMC model is deprecated
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
    """Register AADC event bus subscriptions. Call once at blueprint init."""
    try:
        from tools.canvas.event_bus import subscribe
        subscribe("aadc", "sdc.topology.saved", _on_sdc_topology_saved)
        subscribe("aadc", "odc.source.added", _on_odc_source_added)
        subscribe("aadc", "aimc.model.deprecated", _on_aimc_model_deprecated)
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

def _on_sdc_topology_saved(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """When SDC saves a topology, store a security context artifact on matching AADC designs.

    Queries SDC DB for the design's security controls, then upserts an
    aadc_artifacts row (type=sdc_security_context) for each AADC design whose
    classification matches the SDC design.
    """
    sdc_design_id = payload.get("design_id", "")
    if not sdc_design_id:
        return
    logger.info("aadc.bus: sdc.topology.saved for sdc_design=%s", sdc_design_id)

    # 1. Pull control/asset nodes from SDC graph
    controls: list[dict] = []
    classification = payload.get("classification", "CUI")
    try:
        from tools.security_canvas.db.init_db import get_connection as sdc_conn
        conn = sdc_conn()
        try:
            row = conn.execute(
                "SELECT graph_json, classification FROM security_designs WHERE id=%s",
                (sdc_design_id,),
            ).fetchone()
        finally:
            conn.close()
        if row:
            classification = row["classification"] if isinstance(row, dict) else row[1]
            graph_raw = row["graph_json"] if isinstance(row, dict) else row[0]
            graph = json.loads(graph_raw) if isinstance(graph_raw, str) else (graph_raw or {})
            for node in graph.get("nodes", []):
                if node.get("type") in ("control", "asset", "trust-boundary", "sc_control"):
                    controls.append({"id": node.get("id"), "label": node.get("label"),
                                     "type": node.get("type")})
    except Exception as exc:
        logger.debug("aadc.bus: sdc query failed: %s", exc)

    context_json = json.dumps({
        "sdc_design_id": sdc_design_id,
        "classification": classification,
        "control_nodes": controls,
        "synced_at": _now(),
        "event_id": event_id,
    })

    # 2. Find AADC designs with matching classification and store artifact
    try:
        from tools.agentic_ai_canvas.db.init_db import get_connection as aadc_conn
        conn = aadc_conn()
        try:
            designs = conn.execute(
                "SELECT id FROM aadc_designs WHERE classification=%s", (classification,)
            ).fetchall()
            for design_row in designs:
                design_id = design_row["id"] if isinstance(design_row, dict) else design_row[0]
                artifact_id = str(uuid.uuid4())
                conn.execute(
                    "DELETE FROM aadc_artifacts WHERE design_id=%s AND artifact_type=%s",
                    (design_id, "sdc_security_context"),
                )
                conn.execute(
                    "INSERT INTO aadc_artifacts (id, design_id, artifact_type, title, content_json, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (artifact_id, design_id, "sdc_security_context",
                     f"SDC Security Context ({sdc_design_id[:8]})", context_json, _now()),
                )
                conn.execute(
                    "INSERT INTO aadc_audit (id, design_id, action, detail, classification, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), design_id, "SDC_CONTEXT_SYNC",
                     f"sdc_design={sdc_design_id} controls={len(controls)}", classification, _now()),
                )
            conn.commit()
            logger.info("aadc.bus: sdc_security_context synced to %d designs", len(designs))
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("aadc.bus: sdc context store failed: %s", exc)


def _on_odc_source_added(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """When ODC adds/updates a monitoring source, sync environment model on matching AADC designs.

    Extracts signal-source nodes from the ODC graph and upserts an
    aadc_artifacts row (type=odc_environment_sources) per matching AADC design.
    """
    odc_design_id = payload.get("design_id", "")
    if not odc_design_id:
        return
    logger.info("aadc.bus: odc.source.added for odc_design=%s", odc_design_id)

    # 1. Pull monitoring source nodes from ODC graph
    sources: list[dict] = []
    classification = payload.get("classification", "CUI")
    try:
        from tools.observability_canvas.db.init_db import get_connection as odc_conn
        conn = odc_conn()
        try:
            row = conn.execute(
                "SELECT graph_json, classification FROM observability_designs WHERE id=%s",
                (odc_design_id,),
            ).fetchone()
        finally:
            conn.close()
        if row:
            classification = row["classification"] if isinstance(row, dict) else row[1]
            graph_raw = row["graph_json"] if isinstance(row, dict) else row[0]
            graph = json.loads(graph_raw) if isinstance(graph_raw, str) else (graph_raw or {})
            for node in graph.get("nodes", []):
                if node.get("type") in (
                    "log-source", "metric-source", "trace-source", "siem",
                    "data-source", "monitoring-source", "odc_source",
                ):
                    sources.append({"id": node.get("id"), "label": node.get("label"),
                                    "type": node.get("type")})
    except Exception as exc:
        logger.debug("aadc.bus: odc query failed: %s", exc)

    env_json = json.dumps({
        "odc_design_id": odc_design_id,
        "classification": classification,
        "signal_sources": sources,
        "source_count": len(sources),
        "synced_at": _now(),
        "event_id": event_id,
    })

    # 2. Find AADC designs with matching classification and store artifact
    try:
        from tools.agentic_ai_canvas.db.init_db import get_connection as aadc_conn
        conn = aadc_conn()
        try:
            designs = conn.execute(
                "SELECT id FROM aadc_designs WHERE classification=%s", (classification,)
            ).fetchall()
            for design_row in designs:
                design_id = design_row["id"] if isinstance(design_row, dict) else design_row[0]
                artifact_id = str(uuid.uuid4())
                conn.execute(
                    "DELETE FROM aadc_artifacts WHERE design_id=%s AND artifact_type=%s",
                    (design_id, "odc_environment_sources"),
                )
                conn.execute(
                    "INSERT INTO aadc_artifacts (id, design_id, artifact_type, title, content_json, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (artifact_id, design_id, "odc_environment_sources",
                     f"ODC Environment Sources ({odc_design_id[:8]})", env_json, _now()),
                )
                conn.execute(
                    "INSERT INTO aadc_audit (id, design_id, action, detail, classification, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), design_id, "ODC_ENV_SYNC",
                     f"odc_design={odc_design_id} sources={len(sources)}", classification, _now()),
                )
            conn.commit()
            logger.info("aadc.bus: odc_environment_sources synced to %d designs", len(designs))
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("aadc.bus: odc env store failed: %s", exc)


def _on_aimc_model_deprecated(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """When AIMC deprecates a model, audit every AADC design that references it.

    Queries aadc_aimc_model_refs for the deprecated model_id and writes:
      - aadc_audit row (action=AIMC_MODEL_DEPRECATED) per affected design
      - aadc_artifacts row (artifact_type=aimc_deprecated_model_ref) per ref so
        the AADC UI can surface a deprecation warning on each affected node.
    """
    model_id = payload.get("model_id", "")
    if not model_id:
        logger.warning("aadc.bus: aimc.model.deprecated missing model_id")
        return
    deprecation_date = payload.get("deprecation_date", _now())
    logger.info("aadc.bus: aimc.model.deprecated model_id=%s", model_id)

    try:
        from tools.agentic_ai_canvas.db.init_db import get_connection as aadc_conn
        conn = aadc_conn()
        try:
            refs = conn.execute(
                "SELECT id, aadc_design_id, aadc_node_id, aimc_design_id "
                "FROM aadc_aimc_model_refs WHERE aimc_model_id=%s",
                (model_id,),
            ).fetchall()

            for ref_row in refs:
                if isinstance(ref_row, dict):
                    ref_id = ref_row["id"]
                    aadc_design_id = ref_row["aadc_design_id"]
                    aadc_node_id = ref_row["aadc_node_id"]
                else:
                    ref_id, aadc_design_id, aadc_node_id = ref_row[0], ref_row[1], ref_row[2]

                detail = (
                    f"ref_id={ref_id} aadc_design_id={aadc_design_id} "
                    f"aimc_model_id={model_id} deprecation_date={deprecation_date}"
                )
                conn.execute(
                    "INSERT INTO aadc_audit (id, design_id, action, detail, classification, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), aadc_design_id, "AIMC_MODEL_DEPRECATED",
                     detail, "CUI", _now()),
                )

                artifact_content = json.dumps({
                    "event_id": event_id,
                    "ref_id": ref_id,
                    "aadc_design_id": aadc_design_id,
                    "aadc_node_id": aadc_node_id,
                    "aimc_model_id": model_id,
                    "deprecation_date": deprecation_date,
                    "flagged_at": _now(),
                })
                conn.execute(
                    "INSERT INTO aadc_artifacts (id, design_id, artifact_type, title, content_json, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), aadc_design_id, "aimc_deprecated_model_ref",
                     f"Deprecated AIMC Model ({model_id[:8]})", artifact_content, _now()),
                )

            conn.commit()
            logger.info(
                "aadc.bus: flagged %d orphaned ref(s) for deprecated aimc_model=%s",
                len(refs), model_id,
            )
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("aadc.bus: aimc model deprecated handler failed: %s", exc)
