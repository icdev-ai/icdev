# CUI // SP-CTI
"""STRATEGOS — War Knowledge Graph Schema.

Defines node types, edge types, and seeding helpers for the STRATEGOS
war-domain knowledge graph.  Uses the existing icdev.db kg_nodes / kg_edges
tables (migration 031 / awareness phase).

Node types (9):
  MilitaryUnit, Equipment, ConflictEvent, CyberOperation, Vessel,
  Infrastructure, LogisticsNode, BehaviorProfile, WarReadinessEvent

Edge types (7):
  DEPLOYED_AT, ATTACKED, PRECEDES, CORRELATES,
  LAUNCHED_FROM, SUPPLIES, DAMAGED
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.strategos.war_kg")

# ── Node types ────────────────────────────────────────────────────────────────

WAR_NODE_TYPES = [
    "MilitaryUnit",
    "Equipment",
    "ConflictEvent",
    "CyberOperation",
    "Vessel",
    "Infrastructure",
    "LogisticsNode",
    "BehaviorProfile",
    "WarReadinessEvent",
]

# ── Edge types ────────────────────────────────────────────────────────────────

WAR_EDGE_TYPES = [
    "DEPLOYED_AT",
    "ATTACKED",
    "PRECEDES",
    "CORRELATES",
    "LAUNCHED_FROM",
    "SUPPLIES",
    "DAMAGED",
]

# ── Node type descriptions ────────────────────────────────────────────────────

NODE_TYPE_META: dict[str, dict] = {
    "MilitaryUnit": {
        "color": "#e74c3c",
        "icon": "shield",
        "description": "Military formation (battalion, brigade, fleet, wing)",
        "key_props": ["actor", "domain", "strength", "status", "commander"],
    },
    "Equipment": {
        "color": "#e67e22",
        "icon": "gear",
        "description": "Weapon system, platform, or equipment item",
        "key_props": ["actor", "class", "quantity", "operational_pct", "source"],
    },
    "ConflictEvent": {
        "color": "#c0392b",
        "icon": "explosion",
        "description": "ACLED/Oryx conflict event — attack, battle, explosion",
        "key_props": ["event_type", "actor", "date", "fatalities", "location"],
    },
    "CyberOperation": {
        "color": "#8e44ad",
        "icon": "code",
        "description": "Cyber attack, intrusion, or information operation",
        "key_props": ["technique_id", "actor", "target", "severity", "date"],
    },
    "Vessel": {
        "color": "#2980b9",
        "icon": "anchor",
        "description": "AIS-tracked naval vessel or civilian ship",
        "key_props": ["mmsi", "flag", "vessel_class", "status", "last_pos"],
    },
    "Infrastructure": {
        "color": "#7f8c8d",
        "icon": "building",
        "description": "Physical infrastructure node (power, rail, water, comms)",
        "key_props": ["infra_type", "country", "criticality", "cyber_vuln_score"],
    },
    "LogisticsNode": {
        "color": "#795548",
        "icon": "truck",
        "description": "Supply depot, logistics hub, or transit chokepoint",
        "key_props": ["node_type", "actor", "capacity_tons", "status"],
    },
    "BehaviorProfile": {
        "color": "#ff9800",
        "icon": "person",
        "description": "Ghost Track behavioral profile — GMM anomaly model",
        "key_props": ["mmsi", "anomaly_score", "route_class", "last_updated"],
    },
    "WarReadinessEvent": {
        "color": "#f44336",
        "icon": "alert",
        "description": "I&W composite war readiness signal event",
        "key_props": ["composite_score", "escalation_lvl", "domain", "summary"],
    },
}

# ── Edge type descriptions ────────────────────────────────────────────────────

EDGE_TYPE_META: dict[str, dict] = {
    "DEPLOYED_AT": {
        "color": "#27ae60",
        "description": "Military unit or vessel deployed at a location/entity",
        "directional": True,
    },
    "ATTACKED": {
        "color": "#e74c3c",
        "description": "One entity attacked another",
        "directional": True,
    },
    "PRECEDES": {
        "color": "#f39c12",
        "description": "Event A temporally precedes event B (causal/temporal link)",
        "directional": True,
    },
    "CORRELATES": {
        "color": "#3498db",
        "description": "Two signals or events are correlated (undirected by convention)",
        "directional": False,
    },
    "LAUNCHED_FROM": {
        "color": "#9b59b6",
        "description": "Weapon / operation launched from a platform or location",
        "directional": True,
    },
    "SUPPLIES": {
        "color": "#1abc9c",
        "description": "Logistics node supplies a military unit",
        "directional": True,
    },
    "DAMAGED": {
        "color": "#e67e22",
        "description": "Infrastructure or equipment was damaged/destroyed",
        "directional": True,
    },
}

# ── Ontology mappings for cross-domain alignment ──────────────────────────────

WAR_ONTOLOGY_MAP: dict[str, str] = {
    "MilitaryUnit": "war:MilitaryUnit",
    "Equipment": "war:Equipment",
    "ConflictEvent": "war:ConflictEvent",
    "CyberOperation": "war:CyberOperation",
    "Vessel": "war:Vessel",
    "Infrastructure": "war:Infrastructure",
    "LogisticsNode": "war:LogisticsNode",
    "BehaviorProfile": "war:BehaviorProfile",
    "WarReadinessEvent": "war:WarReadinessEvent",
}

# ── KG graph ID for war domain ────────────────────────────────────────────────

WAR_GRAPH_ID = "strategos-war-kg"


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def _get_db_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "icdev.db"


def ensure_war_graph(conn=None) -> str:
    """Create the war KG graph record if it doesn't exist. Returns graph_id."""
    from tools.db.storage import get_connection

    close_after = conn is None
    if conn is None:
        conn = get_connection()

    try:
        row = conn.execute(
            "SELECT id FROM kg_graphs WHERE id=%s", (WAR_GRAPH_ID,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO kg_graphs (id, name, description, created_at) VALUES (%s,%s,%s,%s)",
                (
                    WAR_GRAPH_ID,
                    "STRATEGOS War Knowledge Graph",
                    "Multi-domain conflict entities, events, and relationships for I&W and COA analysis",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("ensure_war_graph: best-effort INSERT into kg_graphs failed (non-blocking): %s", exc)
    finally:
        if close_after:
            conn.close()

    return WAR_GRAPH_ID


def upsert_node(
    entity_type: str,
    label: str,
    properties: dict,
    node_id: str | None = None,
    conn=None,
) -> str:
    """Insert or update a war KG node. Returns node_id."""
    from tools.db.storage import get_connection

    if entity_type not in WAR_NODE_TYPES:
        raise ValueError(f"Unknown war node type: {entity_type}. Valid: {WAR_NODE_TYPES}")

    close_after = conn is None
    if conn is None:
        conn = get_connection()

    nid = node_id or str(uuid.uuid4())
    # Inject ontology_id for cross-domain alignment
    props = dict(properties)
    props.setdefault("ontology_id", WAR_ONTOLOGY_MAP.get(entity_type))
    props_json = json.dumps(props)
    now = datetime.now(timezone.utc).isoformat()

    try:
        existing = conn.execute(
            "SELECT id FROM kg_nodes WHERE id=%s", (nid,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE kg_nodes SET label=%s, entity_type=%s, properties=%s WHERE id=%s",
                (label, entity_type, props_json, nid),
            )
        else:
            conn.execute(
                "INSERT INTO kg_nodes (id, graph_id, label, entity_type, properties, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (nid, WAR_GRAPH_ID, label, entity_type, props_json, now),
            )
        conn.commit()
    finally:
        if close_after:
            conn.close()

    return nid


def upsert_edge(
    src_id: str,
    tgt_id: str,
    relationship: str,
    properties: dict | None = None,
    conn=None,
) -> str:
    """Insert a war KG edge (idempotent by src+tgt+type). Returns edge_id."""
    from tools.db.storage import get_connection

    if relationship not in WAR_EDGE_TYPES:
        raise ValueError(f"Unknown war edge type: {relationship}. Valid: {WAR_EDGE_TYPES}")

    close_after = conn is None
    if conn is None:
        conn = get_connection()

    now = datetime.now(timezone.utc).isoformat()
    props_json = json.dumps(properties or {})

    try:
        existing = conn.execute(
            "SELECT id FROM kg_edges WHERE source_id=%s AND target_id=%s AND relationship=%s",
            (src_id, tgt_id, relationship),
        ).fetchone()
        if existing:
            return existing[0]
        eid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO kg_edges (id, graph_id, source_id, target_id, relationship, properties, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (eid, WAR_GRAPH_ID, src_id, tgt_id, relationship, props_json, now),
        )
        conn.commit()
        return eid
    finally:
        if close_after:
            conn.close()


def get_schema_summary() -> dict:
    """Return a summary of the war KG schema for API/UI consumption."""
    return {
        "graph_id": WAR_GRAPH_ID,
        "node_types": [
            {"type": t, **NODE_TYPE_META.get(t, {})}
            for t in WAR_NODE_TYPES
        ],
        "edge_types": [
            {"type": t, **EDGE_TYPE_META.get(t, {})}
            for t in WAR_EDGE_TYPES
        ],
    }
