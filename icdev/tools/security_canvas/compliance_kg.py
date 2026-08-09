#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""SDC Compliance Knowledge Graph Builder.

Models the Security Design Canvas crosswalk as a traversable knowledge graph:

  STRIDE categories  --maps_to-->    NIST 800-53 controls
  SDC control types  --implements--> NIST 800-53 controls
  SDC threat types   --represents--> STRIDE categories
  NIST controls      --belongs_to--> NIST families
  NIST controls      --satisfies-->  compliance frameworks
  frameworks         --overlaps_with--> other frameworks

Graph name: sdc-compliance-kg
Node types: stride_category | nist_control | nist_family | framework |
            sdc_control_type | sdc_threat_type
Edge types: maps_to | implements | represents | belongs_to | satisfies |
            overlaps_with | mitigates

Usage:
    python tools/security_canvas/compliance_kg.py --build --json
    python tools/security_canvas/compliance_kg.py --node-info AC-2 --json
    python tools/security_canvas/compliance_kg.py --path-from ctrl-firewall --to fedramp --json
    python tools/security_canvas/compliance_kg.py --stride-coverage S --json
    python tools/security_canvas/compliance_kg.py --sdc-ctrl-coverage ctrl-kms --json
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

logger = get_logger("icdev.security_canvas.compliance_kg")

GRAPH_NAME = "sdc-compliance-kg"

# ---------------------------------------------------------------------------
# SDC ↔ Compliance mappings
# ---------------------------------------------------------------------------

# SDC palette control type → NIST 800-53 control IDs
SDC_CTRL_TO_NIST: Dict[str, List[str]] = {
    "ctrl-firewall": ["SC-7", "SC-5", "AC-4", "SC-10", "CM-7"],
    "ctrl-idp": ["IA-2", "IA-3", "IA-5", "IA-8", "AC-2", "SC-23"],
    "ctrl-kms": ["SC-12", "SC-13", "SC-17", "MP-3", "SC-28"],
    "ctrl-siem": ["AU-2", "AU-3", "AU-6", "AU-10", "AU-12", "IR-4", "IR-5", "SI-4"],
    "ctrl-ids": ["SI-4", "SC-7", "IR-4", "AU-12", "RA-5"],
    "ctrl-pam": ["AC-6", "AC-2", "AC-17", "IA-2", "AC-3"],
    "ctrl-scanner": ["RA-5", "SI-2", "CA-7", "CA-2", "SI-3"],
    "ctrl-encryption": ["SC-8", "SC-13", "SC-28", "SC-12", "MP-5"],
}

SDC_CTRL_LABELS: Dict[str, str] = {
    "ctrl-firewall": "Firewall / WAF",
    "ctrl-idp": "IdP / MFA",
    "ctrl-kms": "KMS / HSM",
    "ctrl-siem": "SIEM / SOC",
    "ctrl-ids": "IDS / IPS",
    "ctrl-pam": "PAM",
    "ctrl-scanner": "Vulnerability Scanner",
    "ctrl-encryption": "Encryptor",
}

# SDC threat type → STRIDE category codes
SDC_THREAT_TO_STRIDE: Dict[str, List[str]] = {
    "threat-actor": ["S", "E"],
    "threat-malware": ["T", "D", "E"],
    "threat-phishing": ["S", "R"],
    "threat-exploit": ["T", "E", "I"],
    "threat-dos": ["D"],
    "threat-supply": ["T", "I", "E"],
    "threat-insider": ["R", "I", "E"],
}

SDC_THREAT_LABELS: Dict[str, str] = {
    "threat-actor": "Threat Actor",
    "threat-malware": "Malware",
    "threat-phishing": "Phishing",
    "threat-exploit": "Exploit",
    "threat-dos": "DoS / DDoS",
    "threat-supply": "Supply Chain Attack",
    "threat-insider": "Insider Threat",
}

# STRIDE → NIST 800-53 controls (from constants.py)
STRIDE_TO_NIST: Dict[str, List[str]] = {
    "S": ["IA-2", "IA-3", "IA-5", "IA-8", "SC-23"],  # Spoofing
    "T": ["SI-7", "SC-8", "SC-28", "AU-10", "CM-3"],  # Tampering
    "R": ["AU-2", "AU-3", "AU-6", "AU-10", "AU-12"],  # Repudiation
    "I": ["SC-8", "SC-13", "SC-28", "AC-3", "AC-4"],  # Information Disclosure
    "D": ["SC-5", "CP-7", "CP-8", "CP-10", "SI-17"],  # Denial of Service
    "E": ["AC-6", "AC-2", "CM-5", "CM-7", "SC-4"],  # Elevation of Privilege
}

STRIDE_LABELS: Dict[str, str] = {
    "S": "Spoofing",
    "T": "Tampering",
    "R": "Repudiation",
    "I": "Information Disclosure",
    "D": "Denial of Service",
    "E": "Elevation of Privilege",
}

STRIDE_DESCRIPTIONS: Dict[str, str] = {
    "S": "Pretending to be something or someone other than yourself.",
    "T": "Modifying data or code without authorization.",
    "R": "Claiming to not have performed an action.",
    "I": "Exposing information to unauthorized individuals.",
    "D": "Denying or degrading service to valid users.",
    "E": "Gaining capabilities without proper authorization.",
}

# Framework keys from control_crosswalk.json (boolean + ID-based)
FRAMEWORK_BOOL_KEYS = [
    "fedramp_moderate",
    "fedramp_high",
    "il4_required",
    "il5_required",
    "il6_required",
]
FRAMEWORK_ID_KEYS = ["nist_800_171", "cmmc_level_2", "cmmc_level_3"]
FRAMEWORK_LABELS: Dict[str, str] = {
    "fedramp_moderate": "FedRAMP Moderate",
    "fedramp_high": "FedRAMP High",
    "nist_800_171": "NIST 800-171",
    "cmmc_level_2": "CMMC Level 2",
    "cmmc_level_3": "CMMC Level 3",
    "il4_required": "DoD IL4",
    "il5_required": "DoD IL5",
    "il6_required": "DoD IL6",
}

PRIORITY_WEIGHTS = {"P1": 1.0, "P2": 0.7, "P3": 0.4}

# CLI aliases for framework names
FRAMEWORK_ALIASES: Dict[str, List[str]] = {
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
# Deterministic node/edge ID generators
# ---------------------------------------------------------------------------


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _stride_node_id(code: str) -> str:
    return f"sdc-stride-{_hash(code.upper())}"


def _sdc_ctrl_node_id(ctrl_type: str) -> str:
    return f"sdc-ctrl-{_hash(ctrl_type.lower())}"


def _sdc_threat_node_id(threat_type: str) -> str:
    return f"sdc-threat-{_hash(threat_type.lower())}"


def _ctrl_node_id(control_id: str) -> str:
    return f"kg-ctrl-{_hash(control_id.upper())}"


def _fam_node_id(family_code: str) -> str:
    return f"kg-fam-{_hash(family_code.upper())}"


def _fw_node_id(fw_key: str) -> str:
    return f"kg-fw-{_hash(fw_key.lower())}"


def _edge_id(src: str, tgt: str, rel: str) -> str:
    return f"sdc-edge-{_hash(f'{src}|{tgt}|{rel}')}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path) if db_path else None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
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
        );
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            label TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            embedding BLOB,
            centrality REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS kg_edges (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            source_id TEXT NOT NULL REFERENCES kg_nodes(id),
            target_id TEXT NOT NULL REFERENCES kg_nodes(id),
            relationship TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def _load_crosswalk_data() -> Dict[str, Any]:
    path = BASE_DIR / "context" / "compliance" / "control_crosswalk.json"
    if not path.exists():
        raise FileNotFoundError(f"Crosswalk data not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_sdc_kg(
    project_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build (or rebuild) the sdc-compliance-kg knowledge graph.

    Nodes: stride_category, nist_control, nist_family, framework,
           sdc_control_type, sdc_threat_type
    Edges: maps_to, implements, represents, belongs_to, satisfies,
           overlaps_with

    Args:
        project_id: Optional project ID to scope the graph.
        db_path: Optional DB path override.

    Returns:
        Summary dict with graph_id, node/edge counts.
    """
    data = _load_crosswalk_data()
    families = data.get("families", [])
    crosswalk = data.get("crosswalk", [])

    if not crosswalk:
        return {"status": "error", "error": "No crosswalk data found"}

    conn = _get_db(db_path)
    _ensure_tables(conn)
    ts = _now()

    graph_key = f"{GRAPH_NAME}|{project_id or 'global'}"
    graph_id = f"sdc-kg-{_hash(graph_key)}"

    # Upsert graph record (idempotent rebuild)
    existing = conn.execute("SELECT id FROM kg_graphs WHERE id = %s", (graph_id,)).fetchone()
    if existing:
        conn.execute("DELETE FROM kg_edges WHERE graph_id = %s", (graph_id,))
        conn.execute("DELETE FROM kg_nodes WHERE graph_id = %s", (graph_id,))
        conn.execute("UPDATE kg_graphs SET updated_at = %s WHERE id = %s", (ts, graph_id))
    else:
        conn.execute(
            """INSERT INTO kg_graphs
               (id, project_id, name, description, entity_count, edge_count,
                metadata, created_at, updated_at)
               VALUES (%s, %s, %s, %s, 0, 0, '{}', %s, %s)""",
            (
                graph_id,
                project_id,
                GRAPH_NAME,
                "SDC crosswalk KG: STRIDE→NIST→frameworks + SDC palette objects",
                ts,
                ts,
            ),
        )

    def _upsert_node(nid: str, label: str, etype: str, props: Dict) -> None:
        conn.execute(
            """INSERT OR REPLACE INTO kg_nodes
               (id, graph_id, label, entity_type, properties, centrality, created_at)
               VALUES (%s, %s, %s, %s, %s, 0.0, %s)""",
            (nid, graph_id, label, etype, json.dumps(props, ensure_ascii=False), ts),
        )

    def _upsert_edge(
        src: str,
        tgt: str,
        rel: str,
        weight: float = 1.0,
        props: Optional[Dict] = None,
    ) -> str:
        eid = _edge_id(src, tgt, rel)
        conn.execute(
            """INSERT OR REPLACE INTO kg_edges
               (id, graph_id, source_id, target_id, relationship, weight,
                properties, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                eid,
                graph_id,
                src,
                tgt,
                rel,
                weight,
                json.dumps(props or {}, ensure_ascii=False),
                ts,
            ),
        )
        return eid

    node_count = 0
    edge_count = 0

    # ── 1. NIST family nodes ─────────────────────────────────────────────────
    family_map: Dict[str, str] = {}
    for fam in families:
        code, name = fam["code"], fam["name"]
        family_map[code] = name
        _upsert_node(_fam_node_id(code), f"{code} — {name}", "nist_family", {"code": code, "name": name})
        node_count += 1

    # ── 2. Framework nodes ───────────────────────────────────────────────────
    all_fw_keys = set(FRAMEWORK_BOOL_KEYS + FRAMEWORK_ID_KEYS)
    for fw_key in all_fw_keys:
        label = FRAMEWORK_LABELS.get(fw_key, fw_key)
        _upsert_node(_fw_node_id(fw_key), label, "framework", {"key": fw_key, "label": label})
        node_count += 1

    # ── 3. STRIDE category nodes ─────────────────────────────────────────────
    for code, info in STRIDE_TO_NIST.items():
        label = STRIDE_LABELS[code]
        desc = STRIDE_DESCRIPTIONS[code]
        _upsert_node(
            _stride_node_id(code),
            f"STRIDE:{label}",
            "stride_category",
            {"code": code, "name": label, "description": desc, "nist_controls": info},
        )
        node_count += 1

    # ── 4. SDC control type nodes ────────────────────────────────────────────
    for ctrl_type, label in SDC_CTRL_LABELS.items():
        _upsert_node(
            _sdc_ctrl_node_id(ctrl_type),
            f"SDC:{label}",
            "sdc_control_type",
            {"type": ctrl_type, "label": label, "nist_controls": SDC_CTRL_TO_NIST.get(ctrl_type, [])},
        )
        node_count += 1

    # ── 5. SDC threat type nodes ─────────────────────────────────────────────
    for threat_type, label in SDC_THREAT_LABELS.items():
        _upsert_node(
            _sdc_threat_node_id(threat_type),
            f"SDC:{label}",
            "sdc_threat_type",
            {"type": threat_type, "label": label, "stride_codes": SDC_THREAT_TO_STRIDE.get(threat_type, [])},
        )
        node_count += 1

    # ── 6. NIST control nodes + edges ────────────────────────────────────────
    framework_controls: Dict[str, Set[str]] = defaultdict(set)
    # Build a set of all NIST controls referenced by STRIDE/SDC ctrl mappings
    all_referenced_controls: Set[str] = set()
    for ctrl_list in STRIDE_TO_NIST.values():
        all_referenced_controls.update(ctrl_list)
    for ctrl_list in SDC_CTRL_TO_NIST.values():
        all_referenced_controls.update(ctrl_list)

    # Build index of crosswalk entries by control ID
    crosswalk_by_id: Dict[str, Dict] = {e.get("nist_800_53", ""): e for e in crosswalk if e.get("nist_800_53")}

    for entry in crosswalk:
        ctrl_id = entry.get("nist_800_53", "")
        if not ctrl_id:
            continue

        family_code = entry.get("family", ctrl_id.split("-")[0])
        title = entry.get("title", "")
        priority = entry.get("priority", "P3")
        description = entry.get("description", "")
        baseline_level = {"P1": "low", "P2": "moderate", "P3": "high"}.get(priority, "moderate")

        ctrl_nid = _ctrl_node_id(ctrl_id)
        _upsert_node(
            ctrl_nid,
            f"{ctrl_id}: {title}",
            "nist_control",
            {
                "control_id": ctrl_id,
                "title": title,
                "priority": priority,
                "baseline": baseline_level,
                "family": family_code,
                "description": description[:200] if description else "",
            },
        )
        node_count += 1

        # Edge: control → family (belongs_to)
        edge_count += 1
        _upsert_edge(ctrl_nid, _fam_node_id(family_code), "belongs_to", 1.0)

        # Edges: control → framework (satisfies)
        weight = PRIORITY_WEIGHTS.get(priority, 0.5)
        for fw_key in FRAMEWORK_BOOL_KEYS:
            if entry.get(fw_key):
                _upsert_edge(ctrl_nid, _fw_node_id(fw_key), "satisfies", weight, {"priority": priority})
                edge_count += 1
                framework_controls[fw_key].add(ctrl_id)
        for fw_key in FRAMEWORK_ID_KEYS:
            mapped_id = entry.get(fw_key)
            if mapped_id:
                _upsert_edge(
                    ctrl_nid, _fw_node_id(fw_key), "satisfies", weight, {"priority": priority, "mapped_id": mapped_id}
                )
                edge_count += 1
                framework_controls[fw_key].add(ctrl_id)

    # ── 7. STRIDE → NIST edges (maps_to) ────────────────────────────────────
    for stride_code, ctrl_ids in STRIDE_TO_NIST.items():
        stride_nid = _stride_node_id(stride_code)
        for ctrl_id in ctrl_ids:
            ctrl_nid = _ctrl_node_id(ctrl_id)
            # Only create edge if the NIST control exists in crosswalk
            if ctrl_id in crosswalk_by_id:
                _upsert_edge(stride_nid, ctrl_nid, "maps_to", 1.0, {"stride": stride_code, "control": ctrl_id})
                edge_count += 1

    # ── 8. SDC control type → NIST edges (implements) ───────────────────────
    for ctrl_type, ctrl_ids in SDC_CTRL_TO_NIST.items():
        sdc_nid = _sdc_ctrl_node_id(ctrl_type)
        for ctrl_id in ctrl_ids:
            if ctrl_id in crosswalk_by_id:
                _upsert_edge(
                    sdc_nid, _ctrl_node_id(ctrl_id), "implements", 1.0, {"sdc_type": ctrl_type, "control": ctrl_id}
                )
                edge_count += 1

    # ── 9. SDC threat → STRIDE edges (represents) ───────────────────────────
    for threat_type, stride_codes in SDC_THREAT_TO_STRIDE.items():
        threat_nid = _sdc_threat_node_id(threat_type)
        for stride_code in stride_codes:
            _upsert_edge(
                threat_nid,
                _stride_node_id(stride_code),
                "represents",
                1.0,
                {"threat": threat_type, "stride": stride_code},
            )
            edge_count += 1

    # ── 10. Framework overlaps_with edges ────────────────────────────────────
    fw_keys_list = list(framework_controls.keys())
    for i, fw_a in enumerate(fw_keys_list):
        for fw_b in fw_keys_list[i + 1 :]:
            shared = framework_controls[fw_a] & framework_controls[fw_b]
            if shared:
                overlap = len(shared) / max(
                    len(framework_controls[fw_a]),
                    len(framework_controls[fw_b]),
                    1,
                )
                _upsert_edge(
                    _fw_node_id(fw_a),
                    _fw_node_id(fw_b),
                    "overlaps_with",
                    round(overlap, 4),
                    {"shared_controls": len(shared), "overlap_ratio": round(overlap, 4)},
                )
                edge_count += 1

    # Update graph counts
    conn.execute(
        "UPDATE kg_graphs SET entity_count = %s, edge_count = %s, updated_at = %s WHERE id = %s",
        (node_count, edge_count, ts, graph_id),
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
        "breakdown": {
            "nist_families": len(families),
            "frameworks": len(all_fw_keys),
            "stride_categories": len(STRIDE_TO_NIST),
            "sdc_control_types": len(SDC_CTRL_LABELS),
            "sdc_threat_types": len(SDC_THREAT_LABELS),
            "nist_controls": len(crosswalk),
        },
        "built_at": ts,
    }


# ---------------------------------------------------------------------------
# Graph query helpers
# ---------------------------------------------------------------------------


def _load_graph(
    conn: sqlite3.Connection,
    project_id: Optional[str] = None,
) -> Optional[str]:
    """Return graph_id for the sdc-compliance-kg, or None if not built."""
    if project_id:
        row = conn.execute(
            "SELECT id FROM kg_graphs WHERE name = %s AND project_id = %s",
            (GRAPH_NAME, project_id),
        ).fetchone()
    else:
        row = conn.execute("SELECT id FROM kg_graphs WHERE name = %s", (GRAPH_NAME,)).fetchone()
    return dict(row)["id"] if row else None


def _load_adj(
    conn: sqlite3.Connection,
    graph_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load all edges as bidirectional adjacency dict {node_id: [{target, rel, weight}]}."""
    edges = conn.execute(
        "SELECT source_id, target_id, relationship, weight, properties FROM kg_edges WHERE graph_id = %s",
        (graph_id,),
    ).fetchall()
    adj: Dict[str, List[Dict]] = defaultdict(list)
    for e in edges:
        d = dict(e)
        adj[d["source_id"]].append(
            {
                "target": d["target_id"],
                "rel": d["relationship"],
                "weight": d["weight"],
            }
        )
        adj[d["target_id"]].append(
            {
                "target": d["source_id"],
                "rel": d["relationship"],
                "weight": d["weight"],
            }
        )
    return adj


def _node_by_label(
    conn: sqlite3.Connection,
    graph_id: str,
    label_fragment: str,
) -> Optional[Dict[str, Any]]:
    """Find first node whose label contains label_fragment (case-insensitive)."""
    row = conn.execute(
        """SELECT id, label, entity_type, properties
           FROM kg_nodes
           WHERE graph_id = %s AND LOWER(label) LIKE %s
           LIMIT 1""",
        (graph_id, f"%{label_fragment.lower()}%"),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["properties"] = json.loads(d.get("properties") or "{}")
    return d


def _node_by_id(
    conn: sqlite3.Connection,
    node_id: str,
) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT id, label, entity_type, properties FROM kg_nodes WHERE id = %s",
        (node_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["properties"] = json.loads(d.get("properties") or "{}")
    return d


def _bfs_path(
    adj: Dict[str, List[Dict]],
    source_id: str,
    target_ids: Set[str],
    max_depth: int = 6,
) -> Optional[tuple]:
    """BFS from source_id to any target_id. Returns (path_ids, path_edges) or None."""
    visited: Set[str] = {source_id}
    queue: deque = deque([(source_id, [source_id], [])])
    while queue:
        current, path, edges = queue.popleft()
        if len(path) > max_depth:
            continue
        if current in target_ids:
            return path, edges
        for nbr in adj.get(current, []):
            nid = nbr["target"]
            if nid not in visited:
                visited.add(nid)
                queue.append(
                    (
                        nid,
                        path + [nid],
                        edges + [{"from": current, "to": nid, "rel": nbr["rel"], "weight": nbr["weight"]}],
                    )
                )
    return None


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------


def get_node_info(
    label_or_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return a node and its immediate neighbors.

    Args:
        label_or_id: NIST control ID (e.g. "AC-2"), STRIDE code ("S"),
                     SDC type ("ctrl-firewall"), or any label fragment.
        db_path: Optional DB path override.

    Returns:
        Dict with node details and neighbors grouped by relationship.
    """
    conn = _get_db(db_path)
    _ensure_tables(conn)
    graph_id = _load_graph(conn)
    if not graph_id:
        conn.close()
        return {"status": "error", "error": "Graph not built. Run --build first."}

    # Try to find by known ID patterns first
    candidate_id = None
    label_upper = label_or_id.strip().upper()

    # NIST control (e.g. "AC-2")
    if "-" in label_or_id and len(label_or_id) <= 8:
        candidate_id = _ctrl_node_id(label_upper)
    # STRIDE code (single letter)
    elif label_or_id.strip() in STRIDE_LABELS:
        candidate_id = _stride_node_id(label_or_id.strip())
    # SDC control type
    elif label_or_id.lower() in SDC_CTRL_LABELS:
        candidate_id = _sdc_ctrl_node_id(label_or_id.lower())
    # SDC threat type
    elif label_or_id.lower() in SDC_THREAT_LABELS:
        candidate_id = _sdc_threat_node_id(label_or_id.lower())

    node = None
    if candidate_id:
        node = _node_by_id(conn, candidate_id)
    if not node:
        node = _node_by_label(conn, graph_id, label_or_id)
    if not node:
        conn.close()
        return {"status": "error", "error": f"Node not found: {label_or_id}"}

    # Collect neighbors
    rows = conn.execute(
        """SELECT e.relationship, e.weight, n.id, n.label, n.entity_type
           FROM kg_edges e
           JOIN kg_nodes n ON (
               CASE WHEN e.source_id = %s THEN e.target_id ELSE e.source_id END = n.id
           )
           WHERE e.graph_id = %s AND (e.source_id = %s OR e.target_id = %s)""",
        (node["id"], graph_id, node["id"], node["id"]),
    ).fetchall()

    neighbors_by_rel: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        rd = dict(r)
        neighbors_by_rel[rd["relationship"]].append(
            {
                "id": rd["id"],
                "label": rd["label"],
                "entity_type": rd["entity_type"],
            }
        )

    conn.close()
    return {
        "status": "ok",
        "node": {
            "id": node["id"],
            "label": node["label"],
            "entity_type": node["entity_type"],
            "properties": node["properties"],
        },
        "neighbors_by_relationship": dict(neighbors_by_rel),
        "neighbor_count": sum(len(v) for v in neighbors_by_rel.values()),
    }


def get_path(
    source_label: str,
    target_label: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """BFS path between two nodes in the SDC compliance KG.

    Args:
        source_label: Source node label/ID (e.g. "ctrl-firewall", "AC-2").
        target_label: Target node label/ID (e.g. "fedramp", "CMMC Level 2").
        db_path: Optional DB path override.

    Returns:
        Dict with path_found, path_nodes, path_edges.
    """
    conn = _get_db(db_path)
    _ensure_tables(conn)
    graph_id = _load_graph(conn)
    if not graph_id:
        conn.close()
        return {"status": "error", "error": "Graph not built. Run --build first."}

    # Resolve source
    source_node = None
    if source_label.lower() in SDC_CTRL_LABELS:
        source_node = _node_by_id(conn, _sdc_ctrl_node_id(source_label.lower()))
    elif source_label.lower() in SDC_THREAT_LABELS:
        source_node = _node_by_id(conn, _sdc_threat_node_id(source_label.lower()))
    elif source_label.upper() in STRIDE_LABELS:
        source_node = _node_by_id(conn, _stride_node_id(source_label.upper()))
    elif "-" in source_label and len(source_label) <= 8:
        source_node = _node_by_id(conn, _ctrl_node_id(source_label.upper()))
    if not source_node:
        source_node = _node_by_label(conn, graph_id, source_label)
    if not source_node:
        conn.close()
        return {"status": "error", "error": f"Source node not found: {source_label}"}

    # Resolve target(s) — may be a framework alias expanding to multiple nodes
    target_ids: Set[str] = set()
    fw_keys = FRAMEWORK_ALIASES.get(target_label.lower(), [])
    for fk in fw_keys:
        target_ids.add(_fw_node_id(fk))
    if not target_ids:
        t_node = _node_by_label(conn, graph_id, target_label)
        if t_node:
            target_ids.add(t_node["id"])
    if not target_ids:
        conn.close()
        return {"status": "error", "error": f"Target node not found: {target_label}"}

    adj = _load_adj(conn, graph_id)
    result = _bfs_path(adj, source_node["id"], target_ids)

    if not result:
        conn.close()
        return {
            "status": "ok",
            "source": source_label,
            "target": target_label,
            "path_found": False,
        }

    path_ids, path_edges = result
    path_nodes = []
    for nid in path_ids:
        n = _node_by_id(conn, nid)
        if n:
            path_nodes.append({"id": nid, "label": n["label"], "entity_type": n["entity_type"]})

    conn.close()
    return {
        "status": "ok",
        "source": source_label,
        "target": target_label,
        "path_found": True,
        "path_length": len(path_ids),
        "path_nodes": path_nodes,
        "path_edges": path_edges,
    }


def get_stride_coverage(
    stride_code: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get NIST controls and frameworks linked to a STRIDE category.

    Args:
        stride_code: Single-letter STRIDE code (S, T, R, I, D, E) or full name.
        db_path: Optional DB path override.

    Returns:
        Dict with stride info, nist_controls, and framework coverage.
    """
    # Normalize input
    code_map = {v.lower(): k for k, v in STRIDE_LABELS.items()}
    code = stride_code.strip().upper()
    if code not in STRIDE_LABELS:
        code = code_map.get(stride_code.lower(), "")
    if not code:
        return {
            "status": "error",
            "error": f"Unknown STRIDE code: {stride_code}",
            "valid_codes": list(STRIDE_LABELS.keys()),
        }

    conn = _get_db(db_path)
    _ensure_tables(conn)
    graph_id = _load_graph(conn)
    if not graph_id:
        conn.close()
        return {"status": "error", "error": "Graph not built. Run --build first."}

    stride_nid = _stride_node_id(code)
    stride_node = _node_by_id(conn, stride_nid)
    if not stride_node:
        conn.close()
        return {"status": "error", "error": f"STRIDE node not found for: {code}"}

    # Get NIST controls directly linked via maps_to
    ctrl_rows = conn.execute(
        """SELECT n.id, n.label, n.properties
           FROM kg_edges e JOIN kg_nodes n ON e.target_id = n.id
           WHERE e.graph_id = %s AND e.source_id = %s AND e.relationship = 'maps_to'""",
        (graph_id, stride_nid),
    ).fetchall()

    nist_controls = []
    fw_coverage: Dict[str, int] = defaultdict(int)

    for row in ctrl_rows:
        d = dict(row)
        props = json.loads(d.get("properties") or "{}")
        ctrl_id = props.get("control_id", "")
        nist_controls.append(
            {
                "control_id": ctrl_id,
                "title": props.get("title", ""),
                "priority": props.get("priority", ""),
                "family": props.get("family", ""),
            }
        )
        # Check which frameworks this control satisfies
        fw_rows = conn.execute(
            """SELECT n.label FROM kg_edges e JOIN kg_nodes n ON e.target_id = n.id
               WHERE e.graph_id = %s AND e.source_id = %s AND e.relationship = 'satisfies'""",
            (graph_id, d["id"]),
        ).fetchall()
        for fw_row in fw_rows:
            fw_coverage[dict(fw_row)["label"]] += 1

    conn.close()
    return {
        "status": "ok",
        "stride": {
            "code": code,
            "name": STRIDE_LABELS[code],
            "description": STRIDE_DESCRIPTIONS[code],
        },
        "nist_controls": sorted(nist_controls, key=lambda c: c["control_id"]),
        "framework_coverage": dict(fw_coverage),
        "control_count": len(nist_controls),
    }


def get_sdc_ctrl_coverage(
    ctrl_type: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get NIST controls and frameworks linked to an SDC control type.

    Args:
        ctrl_type: SDC control type key (e.g. "ctrl-firewall", "ctrl-kms").
        db_path: Optional DB path override.

    Returns:
        Dict with sdc_control info, nist_controls, and framework coverage.
    """
    if ctrl_type not in SDC_CTRL_LABELS:
        # Try label-based match
        label_map = {v.lower(): k for k, v in SDC_CTRL_LABELS.items()}
        ctrl_type = label_map.get(ctrl_type.lower(), ctrl_type)
    if ctrl_type not in SDC_CTRL_LABELS:
        return {
            "status": "error",
            "error": f"Unknown SDC control type: {ctrl_type}",
            "valid_types": list(SDC_CTRL_LABELS.keys()),
        }

    conn = _get_db(db_path)
    _ensure_tables(conn)
    graph_id = _load_graph(conn)
    if not graph_id:
        conn.close()
        return {"status": "error", "error": "Graph not built. Run --build first."}

    sdc_nid = _sdc_ctrl_node_id(ctrl_type)

    ctrl_rows = conn.execute(
        """SELECT n.id, n.label, n.properties
           FROM kg_edges e JOIN kg_nodes n ON e.target_id = n.id
           WHERE e.graph_id = %s AND e.source_id = %s AND e.relationship = 'implements'""",
        (graph_id, sdc_nid),
    ).fetchall()

    nist_controls = []
    fw_coverage: Dict[str, int] = defaultdict(int)

    for row in ctrl_rows:
        d = dict(row)
        props = json.loads(d.get("properties") or "{}")
        ctrl_id = props.get("control_id", "")
        nist_controls.append(
            {
                "control_id": ctrl_id,
                "title": props.get("title", ""),
                "priority": props.get("priority", ""),
                "family": props.get("family", ""),
            }
        )
        fw_rows = conn.execute(
            """SELECT n.label FROM kg_edges e JOIN kg_nodes n ON e.target_id = n.id
               WHERE e.graph_id = %s AND e.source_id = %s AND e.relationship = 'satisfies'""",
            (graph_id, d["id"]),
        ).fetchall()
        for fw_row in fw_rows:
            fw_coverage[dict(fw_row)["label"]] += 1

    conn.close()
    return {
        "status": "ok",
        "sdc_control": {
            "type": ctrl_type,
            "label": SDC_CTRL_LABELS[ctrl_type],
            "nist_mappings": SDC_CTRL_TO_NIST.get(ctrl_type, []),
        },
        "nist_controls": sorted(nist_controls, key=lambda c: c["control_id"]),
        "framework_coverage": dict(fw_coverage),
        "control_count": len(nist_controls),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SDC Compliance Knowledge Graph Builder & Query Tool",
    )
    parser.add_argument("--build", action="store_true", help="Build/rebuild the sdc-compliance-kg graph")
    parser.add_argument("--project-id", default=None, help="Optional project ID to scope the graph")
    parser.add_argument(
        "--node-info", metavar="LABEL", help="Get node details + neighbors (e.g. AC-2, S, ctrl-firewall)"
    )
    parser.add_argument("--path-from", metavar="SOURCE", help="BFS path source (e.g. ctrl-firewall, threat-phishing)")
    parser.add_argument("--to", metavar="TARGET", help="BFS path target (e.g. fedramp, cmmc)")
    parser.add_argument("--stride-coverage", metavar="CODE", help="STRIDE coverage (S/T/R/I/D/E or full name)")
    parser.add_argument("--sdc-ctrl-coverage", metavar="TYPE", help="SDC control type coverage (e.g. ctrl-firewall)")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--db-path", default=None)

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    if args.build:
        result = build_sdc_kg(project_id=args.project_id, db_path=db_path)
    elif args.node_info:
        result = get_node_info(args.node_info, db_path=db_path)
    elif args.path_from:
        if not args.to:
            parser.error("--path-from requires --to")
        result = get_path(args.path_from, args.to, db_path=db_path)
    elif args.stride_coverage:
        result = get_stride_coverage(args.stride_coverage, db_path=db_path)
    elif args.sdc_ctrl_coverage:
        result = get_sdc_ctrl_coverage(args.sdc_ctrl_coverage, db_path=db_path)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
