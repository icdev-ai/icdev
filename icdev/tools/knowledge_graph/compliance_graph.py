#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Compliance Framework Crosswalk Knowledge Graph.

Models NIST 800-53 crosswalk data as a knowledge graph with nodes for
control families, individual controls, and compliance frameworks, and
edges for belongs_to, satisfies, and overlaps_with relationships.

Uses crosswalk data from tools/compliance/crosswalk_engine.py and stores
in kg_graphs, kg_nodes, kg_edges tables (same schema as graph_rag.py).

Usage:
    python tools/knowledge_graph/compliance_graph.py --build --json
    python tools/knowledge_graph/compliance_graph.py --build --project-id sparkpilot --json
    python tools/knowledge_graph/compliance_graph.py --crosswalk AC-2 --target cmmc --json
    python tools/knowledge_graph/compliance_graph.py --coverage fedramp --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.knowledge_graph.compliance_graph")

GRAPH_NAME = "compliance-crosswalk"

# Priority to numeric weight mapping for edge weights
PRIORITY_WEIGHTS = {
    "P1": 1.0,
    "P2": 0.7,
    "P3": 0.4,
}

# Framework keys that are boolean flags in crosswalk entries
FRAMEWORK_BOOL_KEYS = [
    "fedramp_moderate",
    "fedramp_high",
    "il4_required",
    "il5_required",
    "il6_required",
]

# Framework keys that hold a mapped control ID (non-null means satisfied)
FRAMEWORK_ID_KEYS = [
    "nist_800_171",
    "cmmc_level_2",
    "cmmc_level_3",
]

# Friendly names for framework nodes
FRAMEWORK_LABELS = {
    "fedramp_moderate": "FedRAMP Moderate",
    "fedramp_high": "FedRAMP High",
    "nist_800_171": "NIST 800-171",
    "cmmc_level_2": "CMMC Level 2",
    "cmmc_level_3": "CMMC Level 3",
    "il4_required": "DoD IL4",
    "il5_required": "DoD IL5",
    "il6_required": "DoD IL6",
}

# CLI aliases for --target and --coverage
FRAMEWORK_CLI_ALIASES = {
    "fedramp": ["fedramp_moderate", "fedramp_high"],
    "fedramp_moderate": ["fedramp_moderate"],
    "fedramp_high": ["fedramp_high"],
    "cmmc": ["cmmc_level_2", "cmmc_level_3"],
    "cmmc_level_2": ["cmmc_level_2"],
    "cmmc_level_3": ["cmmc_level_3"],
    "nist_800_171": ["nist_800_171"],
    "800-171": ["nist_800_171"],
    "il4": ["il4_required"],
    "il5": ["il5_required"],
    "il6": ["il6_required"],
}


# ---------------------------------------------------------------------------
# Deterministic ID helpers
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    """SHA-256 content hash (first 12 hex chars) for deterministic IDs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _ctrl_id(control: str) -> str:
    """Deterministic node ID for a control."""
    return f"kg-ctrl-{_content_hash(control.upper())}"


def _fam_id(family_code: str) -> str:
    """Deterministic node ID for a control family."""
    return f"kg-fam-{_content_hash(family_code.upper())}"


def _fw_id(framework_key: str) -> str:
    """Deterministic node ID for a framework."""
    return f"kg-fw-{_content_hash(framework_key.lower())}"


def _edge_id(source_id: str, target_id: str, relationship: str) -> str:
    """Deterministic edge ID based on endpoints and relationship."""
    combined = f"{source_id}|{target_id}|{relationship}"
    return f"kg-edge-{_content_hash(combined)}"


# ---------------------------------------------------------------------------
# Database helpers (same patterns as graph_rag.py)
# ---------------------------------------------------------------------------


def _get_db(db_path: Optional[Path] = None):
    """Get a connection to icdev.db."""
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path) if db_path else None)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Ensure KG tables exist (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_graphs (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            entity_count INTEGER DEFAULT 0,
            edge_count INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            label TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            embedding BLOB,
            centrality REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_edges (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            source_id TEXT NOT NULL REFERENCES kg_nodes(id),
            target_id TEXT NOT NULL REFERENCES kg_nodes(id),
            relationship TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def _now() -> str:
    """ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Crosswalk data loader
# ---------------------------------------------------------------------------


def _load_crosswalk_data() -> Dict[str, Any]:
    """Load crosswalk JSON from context/compliance/control_crosswalk.json.

    Returns:
        Dict with 'families' list and 'crosswalk' list.
    """
    crosswalk_path = BASE_DIR / "context" / "compliance" / "control_crosswalk.json"
    if not crosswalk_path.exists():
        raise FileNotFoundError(
            f"Crosswalk data not found: {crosswalk_path}\nExpected: context/compliance/control_crosswalk.json"
        )
    with open(crosswalk_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


# ---------------------------------------------------------------------------
# Build compliance graph
# ---------------------------------------------------------------------------


def build_compliance_graph(
    project_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build or update the compliance-crosswalk knowledge graph.

    Creates nodes for control families, individual controls, and frameworks.
    Creates edges for belongs_to, satisfies, and overlaps_with relationships.

    Args:
        project_id: Optional project ID to associate with the graph.
        db_path: Optional database path override.

    Returns:
        Summary dict with graph_id, node_count, edge_count, framework_count.
    """
    data = _load_crosswalk_data()
    families = data.get("families", [])
    crosswalk = data.get("crosswalk", [])

    if not crosswalk:
        return {"status": "error", "error": "No crosswalk data found"}

    conn = _get_db(db_path)
    _ensure_tables(conn)
    timestamp = _now()

    # Determine graph ID (deterministic based on name + project)
    graph_key = f"{GRAPH_NAME}|{project_id or 'global'}"
    graph_id = f"kg-graph-{_content_hash(graph_key)}"

    # Upsert graph record
    existing = conn.execute("SELECT id FROM kg_graphs WHERE id = %s", (graph_id,)).fetchone()

    if existing:
        # Clear existing nodes/edges for idempotent rebuild
        conn.execute("DELETE FROM kg_edges WHERE graph_id = %s", (graph_id,))
        conn.execute("DELETE FROM kg_nodes WHERE graph_id = %s", (graph_id,))
        conn.execute(
            "UPDATE kg_graphs SET updated_at = %s WHERE id = %s",
            (timestamp, graph_id),
        )
    else:
        conn.execute(
            """INSERT INTO kg_graphs (id, project_id, name, description,
                                      entity_count, edge_count, metadata,
                                      created_at, updated_at)
               VALUES (%s, %s, %s, %s, 0, 0, '{}', %s, %s)""",
            (
                graph_id,
                project_id,
                GRAPH_NAME,
                "NIST 800-53 crosswalk as knowledge graph — controls, families, and framework mappings",
                timestamp,
                timestamp,
            ),
        )

    # --- Create family nodes ---
    family_map: Dict[str, str] = {}  # code -> family name
    for fam in families:
        code = fam["code"]
        name = fam["name"]
        family_map[code] = name
        node_id = _fam_id(code)
        props = json.dumps({"code": code, "name": name}, ensure_ascii=False)
        conn.execute(
            """INSERT OR REPLACE INTO kg_nodes
               (id, graph_id, label, entity_type, properties, centrality, created_at)
               VALUES (%s, %s, %s, %s, %s, 0.0, %s)""",
            (node_id, graph_id, f"{code} — {name}", "control_family", props, timestamp),
        )

    # --- Create framework nodes ---
    all_fw_keys = set(FRAMEWORK_BOOL_KEYS + FRAMEWORK_ID_KEYS)
    for fw_key in all_fw_keys:
        node_id = _fw_id(fw_key)
        label = FRAMEWORK_LABELS.get(fw_key, fw_key)
        # Determine regime and baseline info
        if fw_key.startswith("fedramp"):
            regime = "fedramp"
            baseline = fw_key.replace("fedramp_", "")
        elif fw_key.startswith("cmmc"):
            regime = "cmmc"
            baseline = fw_key.replace("cmmc_", "")
        elif fw_key.startswith("il"):
            regime = "dod_il"
            baseline = fw_key.replace("_required", "").upper()
        elif fw_key == "nist_800_171":
            regime = "nist"
            baseline = "CUI"
        else:
            regime = fw_key
            baseline = "n/a"

        props = json.dumps(
            {"regime": regime, "baseline": baseline, "key": fw_key},
            ensure_ascii=False,
        )
        conn.execute(
            """INSERT OR REPLACE INTO kg_nodes
               (id, graph_id, label, entity_type, properties, centrality, created_at)
               VALUES (%s, %s, %s, %s, %s, 0.0, %s)""",
            (node_id, graph_id, label, "framework", props, timestamp),
        )

    # --- Create control nodes and edges ---
    # Track which frameworks each control satisfies (for overlaps_with)
    framework_controls: Dict[str, Set[str]] = defaultdict(set)
    node_count = len(families) + len(all_fw_keys)
    edge_count = 0

    for entry in crosswalk:
        ctrl_id_str = entry.get("nist_800_53", "")
        if not ctrl_id_str:
            continue

        family_code = entry.get("family", ctrl_id_str.split("-")[0])
        title = entry.get("title", "")
        priority = entry.get("priority", "P3")
        description = entry.get("description", "")

        # Determine baseline from priority (P1 = high, P2 = moderate, P3 = low)
        baseline_level = {"P1": "low", "P2": "moderate", "P3": "high"}.get(priority, "moderate")

        # Create control node
        ctrl_node_id = _ctrl_id(ctrl_id_str)
        props = json.dumps(
            {
                "control_id": ctrl_id_str,
                "title": title,
                "priority": priority,
                "baseline": baseline_level,
                "family": family_code,
                "description": description[:200] if description else "",
            },
            ensure_ascii=False,
        )
        conn.execute(
            """INSERT OR REPLACE INTO kg_nodes
               (id, graph_id, label, entity_type, properties, centrality, created_at)
               VALUES (%s, %s, %s, %s, %s, 0.0, %s)""",
            (ctrl_node_id, graph_id, f"{ctrl_id_str}: {title}", "control", props, timestamp),
        )
        node_count += 1

        # Edge: control -> control_family (belongs_to)
        fam_node_id = _fam_id(family_code)
        bt_edge_id = _edge_id(ctrl_node_id, fam_node_id, "belongs_to")
        conn.execute(
            """INSERT OR REPLACE INTO kg_edges
               (id, graph_id, source_id, target_id, relationship, weight, properties, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, '{}', %s)""",
            (bt_edge_id, graph_id, ctrl_node_id, fam_node_id, "belongs_to", 1.0, timestamp),
        )
        edge_count += 1

        # Edges: control -> framework (satisfies)
        weight = PRIORITY_WEIGHTS.get(priority, 0.5)

        # Boolean framework keys
        for fw_key in FRAMEWORK_BOOL_KEYS:
            if entry.get(fw_key):
                fw_node_id = _fw_id(fw_key)
                sat_edge_id = _edge_id(ctrl_node_id, fw_node_id, "satisfies")
                edge_props = json.dumps({"priority": priority}, ensure_ascii=False)
                conn.execute(
                    """INSERT OR REPLACE INTO kg_edges
                       (id, graph_id, source_id, target_id, relationship,
                        weight, properties, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        sat_edge_id,
                        graph_id,
                        ctrl_node_id,
                        fw_node_id,
                        "satisfies",
                        weight,
                        edge_props,
                        timestamp,
                    ),
                )
                edge_count += 1
                framework_controls[fw_key].add(ctrl_id_str)

        # ID-based framework keys (non-null means mapped)
        for fw_key in FRAMEWORK_ID_KEYS:
            mapped_id = entry.get(fw_key)
            if mapped_id:
                fw_node_id = _fw_id(fw_key)
                sat_edge_id = _edge_id(ctrl_node_id, fw_node_id, "satisfies")
                edge_props = json.dumps(
                    {"priority": priority, "mapped_id": mapped_id},
                    ensure_ascii=False,
                )
                conn.execute(
                    """INSERT OR REPLACE INTO kg_edges
                       (id, graph_id, source_id, target_id, relationship,
                        weight, properties, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        sat_edge_id,
                        graph_id,
                        ctrl_node_id,
                        fw_node_id,
                        "satisfies",
                        weight,
                        edge_props,
                        timestamp,
                    ),
                )
                edge_count += 1
                framework_controls[fw_key].add(ctrl_id_str)

    # --- Create overlaps_with edges between frameworks sharing controls ---
    fw_keys_list = list(framework_controls.keys())
    for i, fw_a in enumerate(fw_keys_list):
        for fw_b in fw_keys_list[i + 1 :]:
            shared = framework_controls[fw_a] & framework_controls[fw_b]
            if shared:
                overlap_weight = len(shared) / max(
                    len(framework_controls[fw_a]),
                    len(framework_controls[fw_b]),
                    1,
                )
                fw_a_node = _fw_id(fw_a)
                fw_b_node = _fw_id(fw_b)
                ov_edge_id = _edge_id(fw_a_node, fw_b_node, "overlaps_with")
                ov_props = json.dumps(
                    {"shared_controls": len(shared), "overlap_ratio": round(overlap_weight, 4)},
                    ensure_ascii=False,
                )
                conn.execute(
                    """INSERT OR REPLACE INTO kg_edges
                       (id, graph_id, source_id, target_id, relationship,
                        weight, properties, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        ov_edge_id,
                        graph_id,
                        fw_a_node,
                        fw_b_node,
                        "overlaps_with",
                        round(overlap_weight, 4),
                        ov_props,
                        timestamp,
                    ),
                )
                edge_count += 1

    # Update graph counts
    conn.execute(
        "UPDATE kg_graphs SET entity_count = %s, edge_count = %s, updated_at = %s WHERE id = %s",
        (node_count, edge_count, timestamp, graph_id),
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "graph_id": graph_id,
        "graph_name": GRAPH_NAME,
        "project_id": project_id,
        "node_count": node_count,
        "edge_count": edge_count,
        "framework_count": len(all_fw_keys),
        "family_count": len(families),
        "control_count": len(crosswalk),
        "overlap_edges": sum(
            1
            for i, a in enumerate(fw_keys_list)
            for b in fw_keys_list[i + 1 :]
            if framework_controls[a] & framework_controls[b]
        )
        if "fw_keys_list" in dir()
        else 0,
        "built_at": timestamp,
    }


# ---------------------------------------------------------------------------
# Crosswalk path (BFS)
# ---------------------------------------------------------------------------


def get_crosswalk_path(
    source_control: str,
    target_framework: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Find a path from a NIST control to a target framework via KG edges.

    Uses BFS on the knowledge graph to find the shortest path from the
    source control node to the target framework node.

    Args:
        source_control: NIST 800-53 control ID (e.g., "AC-2").
        target_framework: Framework key or alias (e.g., "cmmc", "fedramp").
        db_path: Optional database path override.

    Returns:
        Dict with path details and intermediate nodes.
    """
    conn = _get_db(db_path)
    _ensure_tables(conn)

    # Resolve framework alias to actual keys
    target_keys = FRAMEWORK_CLI_ALIASES.get(
        target_framework.lower(),
        [target_framework.lower()],
    )

    # Find the source control node
    source_node_id = _ctrl_id(source_control)

    # Check source exists
    source_row = conn.execute(
        "SELECT id, label, properties FROM kg_nodes WHERE id = %s",
        (source_node_id,),
    ).fetchone()

    if not source_row:
        conn.close()
        return {
            "status": "error",
            "error": f"Control node not found: {source_control}",
            "hint": "Run --build first to populate the compliance graph",
        }

    # Get target framework node IDs
    target_node_ids = set()
    for tk in target_keys:
        tid = _fw_id(tk)
        row = conn.execute("SELECT id FROM kg_nodes WHERE id = %s", (tid,)).fetchone()
        if row:
            target_node_ids.add(tid)

    if not target_node_ids:
        conn.close()
        return {
            "status": "error",
            "error": f"Framework node not found: {target_framework}",
            "available_frameworks": list(FRAMEWORK_CLI_ALIASES.keys()),
        }

    # Find graph_id from source node
    graph_id = conn.execute("SELECT graph_id FROM kg_nodes WHERE id = %s", (source_node_id,)).fetchone()
    if not graph_id:
        conn.close()
        return {"status": "error", "error": "Source node has no graph"}
    graph_id = graph_id[0] if isinstance(graph_id, tuple) else graph_id["graph_id"]

    # Load all edges for this graph into adjacency list
    edges = conn.execute(
        """SELECT source_id, target_id, relationship, weight, properties
           FROM kg_edges WHERE graph_id = %s""",
        (graph_id,),
    ).fetchall()

    adj: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in edges:
        e_dict = dict(e)
        adj[e_dict["source_id"]].append(
            {
                "target": e_dict["target_id"],
                "relationship": e_dict["relationship"],
                "weight": e_dict["weight"],
                "properties": e_dict["properties"],
            }
        )
        adj[e_dict["target_id"]].append(
            {
                "target": e_dict["source_id"],
                "relationship": e_dict["relationship"],
                "weight": e_dict["weight"],
                "properties": e_dict["properties"],
            }
        )

    # BFS from source_node_id to any target_node_id
    visited: Set[str] = {source_node_id}
    queue: deque = deque()
    queue.append((source_node_id, [source_node_id], []))

    found_path: Optional[List[str]] = None
    found_edges: Optional[List[Dict]] = None

    while queue:
        current, path, path_edges = queue.popleft()

        if current in target_node_ids:
            found_path = path
            found_edges = path_edges
            break

        for neighbor in adj.get(current, []):
            next_id = neighbor["target"]
            if next_id not in visited:
                visited.add(next_id)
                queue.append(
                    (
                        next_id,
                        path + [next_id],
                        path_edges
                        + [
                            {
                                "from": current,
                                "to": next_id,
                                "relationship": neighbor["relationship"],
                                "weight": neighbor["weight"],
                            }
                        ],
                    )
                )

    if not found_path:
        conn.close()
        return {
            "status": "ok",
            "source": source_control,
            "target": target_framework,
            "path_found": False,
            "message": f"No path from {source_control} to {target_framework}",
        }

    # Enrich path nodes with labels
    path_nodes = []
    for nid in found_path:
        row = conn.execute(
            "SELECT label, entity_type, properties FROM kg_nodes WHERE id = %s",
            (nid,),
        ).fetchone()
        if row:
            row_dict = dict(row)
            path_nodes.append(
                {
                    "id": nid,
                    "label": row_dict["label"],
                    "entity_type": row_dict["entity_type"],
                    "properties": json.loads(row_dict.get("properties", "{}") or "{}"),
                }
            )

    conn.close()

    return {
        "status": "ok",
        "source": source_control,
        "target": target_framework,
        "path_found": True,
        "path_length": len(found_path),
        "path_nodes": path_nodes,
        "path_edges": found_edges,
    }


# ---------------------------------------------------------------------------
# Framework coverage
# ---------------------------------------------------------------------------


def get_framework_coverage(
    framework: str,
    project_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Query KG to get coverage of controls linked to a framework.

    Groups controls by family and returns a summary.

    Args:
        framework: Framework key or alias (e.g., "fedramp", "cmmc_level_2").
        project_id: Optional project filter for graph selection.
        db_path: Optional database path override.

    Returns:
        Coverage summary dict grouped by control family.
    """
    conn = _get_db(db_path)
    _ensure_tables(conn)

    # Resolve framework alias
    fw_keys = FRAMEWORK_CLI_ALIASES.get(
        framework.lower(),
        [framework.lower()],
    )

    # Collect target framework node IDs
    fw_node_ids = []
    for fk in fw_keys:
        fid = _fw_id(fk)
        row = conn.execute("SELECT id, label FROM kg_nodes WHERE id = %s", (fid,)).fetchone()
        if row:
            fw_node_ids.append({"id": fid, "label": dict(row)["label"]})

    if not fw_node_ids:
        conn.close()
        return {
            "status": "error",
            "error": f"Framework not found in graph: {framework}",
            "available_frameworks": list(FRAMEWORK_CLI_ALIASES.keys()),
        }

    # Find the compliance-crosswalk graph
    if project_id:
        graph_row = conn.execute(
            "SELECT id FROM kg_graphs WHERE name = %s AND project_id = %s",
            (GRAPH_NAME, project_id),
        ).fetchone()
    else:
        graph_row = conn.execute("SELECT id FROM kg_graphs WHERE name = %s", (GRAPH_NAME,)).fetchone()

    if not graph_row:
        conn.close()
        return {
            "status": "error",
            "error": "Compliance graph not found. Run --build first.",
        }
    graph_id = dict(graph_row)["id"]

    # Find all controls that satisfy these frameworks
    fw_id_list = [f["id"] for f in fw_node_ids]
    placeholders = ",".join("?" for _ in fw_id_list)

    rows = conn.execute(
        f"""SELECT n.id, n.label, n.properties
            FROM kg_nodes n
            JOIN kg_edges e ON e.source_id = n.id
            WHERE e.graph_id = %s
              AND e.target_id IN ({placeholders})
              AND e.relationship = 'satisfies'
              AND n.entity_type = 'control'""",  # nosec B608 -- table/column names are internal constants, not user input
        [graph_id] + fw_id_list,
    ).fetchall()

    # Group by family (deduplicate controls that satisfy multiple sub-frameworks)
    family_groups: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    total_controls = 0

    for row in rows:
        row_dict = dict(row)
        props = json.loads(row_dict.get("properties", "{}") or "{}")
        family_code = props.get("family", "??")
        ctrl_id = props.get("control_id", "")
        if ctrl_id not in family_groups[family_code]:
            family_groups[family_code][ctrl_id] = {
                "control_id": ctrl_id,
                "title": props.get("title", ""),
                "priority": props.get("priority", ""),
            }
            total_controls += 1

    # Sort families and controls
    coverage_by_family = []
    for fam_code in sorted(family_groups.keys()):
        controls = sorted(family_groups[fam_code].values(), key=lambda c: c["control_id"])
        coverage_by_family.append(
            {
                "family": fam_code,
                "control_count": len(controls),
                "controls": controls,
            }
        )

    conn.close()

    return {
        "status": "ok",
        "framework": framework,
        "framework_nodes": [f["label"] for f in fw_node_ids],
        "total_controls": total_controls,
        "family_count": len(coverage_by_family),
        "coverage_by_family": coverage_by_family,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Compliance Framework Crosswalk Knowledge Graph",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build or rebuild the compliance crosswalk graph",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Optional project ID to associate with graph",
    )
    parser.add_argument(
        "--crosswalk",
        default=None,
        metavar="CONTROL",
        help="Find crosswalk path from a control (e.g., AC-2)",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Target framework for crosswalk path (e.g., cmmc, fedramp)",
    )
    parser.add_argument(
        "--coverage",
        default=None,
        metavar="FRAMEWORK",
        help="Get coverage report for a framework (e.g., fedramp, cmmc_level_2)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional database path override",
    )

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    if args.build:
        result = build_compliance_graph(
            project_id=args.project_id,
            db_path=db_path,
        )
    elif args.crosswalk:
        if not args.target:
            parser.error("--crosswalk requires --target")
        result = get_crosswalk_path(
            source_control=args.crosswalk,
            target_framework=args.target,
            db_path=db_path,
        )
    elif args.coverage:
        result = get_framework_coverage(
            framework=args.coverage,
            project_id=args.project_id,
            db_path=db_path,
        )
    else:
        parser.print_help()
        return

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
