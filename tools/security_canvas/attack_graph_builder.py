# [CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Attack Graph Builder.

Converts STRIDE assessment results and attack_path_finder output into a
normalized attack graph: nodes represent assets, edges represent exploitable
transitions each annotated with a MITRE ATT&CK TTP ID.

Pure functions — no Flask, no LLM, no I/O.  Deterministic output.
"""

import json
from typing import Optional

from tools.security_canvas.constants import MITRE_ATTACK_TECHNIQUES

# ── STRIDE category → primary MITRE ATT&CK TTP ──────────────────────────────
# One canonical technique per threat category, resolved from constants.

_STRIDE_TTP: dict[str, tuple[str, str]] = {
    "S": ("initial_access",       "T1078"),  # Valid Accounts
    "T": ("execution",            "T1059"),  # Command and Scripting Interpreter
    "R": ("defense_evasion",      "T1070"),  # Indicator Removal
    "I": ("collection",           "T1005"),  # Data from Local System
    "D": ("impact",               "T1499"),  # Endpoint Denial of Service
    "E": ("privilege_escalation", "T1068"),  # Exploitation for Privilege Escalation
}

# ── Asset type → hop TTP for attack-path edges ───────────────────────────────
_ASSET_HOP_TTP: dict[str, tuple[str, str]] = {
    "asset-client":      ("initial_access",    "T1566"),
    "asset-server":      ("lateral_movement",  "T1021"),
    "asset-container":   ("lateral_movement",  "T1210"),
    "asset-database":    ("collection",        "T1005"),
    "asset-storage":     ("collection",        "T1530"),
    "asset-network":     ("discovery",         "T1046"),
    "ctrl-firewall":     ("defense_evasion",   "T1562"),
    "ctrl-idp":          ("credential_access", "T1110"),
    "ctrl-pam":          ("credential_access", "T1555"),
    "ctrl-ids":          ("defense_evasion",   "T1562"),
    "ctrl-siem":         ("defense_evasion",   "T1070"),
    "ctrl-edr":          ("defense_evasion",   "T1562"),
    "ctrl-kms":          ("credential_access", "T1552"),
    "ctrl-dlp":          ("exfiltration",      "T1041"),
    "boundary-internet": ("initial_access",    "T1190"),
}
_HOP_DEFAULT_TTP: tuple[str, str] = ("lateral_movement", "T1021")


# ── Public helpers ────────────────────────────────────────────────────────────


def get_ttp_for_stride_category(stride_category: str) -> dict:
    """Return MITRE ATT&CK TTP metadata for a STRIDE category letter.

    Args:
        stride_category: One of 'S','T','R','I','D','E'.

    Returns:
        Dict with tactic_id, tactic_name, ttp_id, ttp_name, severity.
        Returns empty-string fields if category is not recognised.
    """
    tactic_key, technique_id = _STRIDE_TTP.get(stride_category, ("", ""))
    if not tactic_key:
        return {"tactic_id": "", "tactic_name": "", "tactic_key": "",
                "ttp_id": "", "ttp_name": "", "severity": "medium"}
    return _resolve_ttp(tactic_key, technique_id)


def stride_rows_to_results(rows: list[dict]) -> dict:
    """Convert sc_threats DB rows into run_stride_analysis()-compatible dict.

    Args:
        rows: List of dicts whose keys match sc_threats columns
              (id, threat_category, title, description, affected_assets,
               likelihood, impact).

    Returns:
        Dict with 'threats', 'total', and 'by_category' — compatible with
        build_attack_graph(stride_results=...).
    """
    threats: list[dict] = []
    by_category: dict[str, int] = {}

    for row in rows:
        cat = row.get("threat_category", "")
        raw_assets = row.get("affected_assets", "[]")
        if isinstance(raw_assets, str):
            try:
                affected = json.loads(raw_assets)
            except (json.JSONDecodeError, ValueError):
                affected = []
        else:
            affected = list(raw_assets)

        threats.append({
            "id": row.get("id", ""),
            "category": cat,
            "category_name": cat,
            "title": row.get("title", ""),
            "description": row.get("description", ""),
            "affected_assets": affected,
            "crosses_boundary": bool(row.get("crosses_boundary", False)),
            "nist_controls": [],
            "likelihood": row.get("likelihood", "medium"),
            "impact": row.get("impact", "medium"),
        })
        by_category[cat] = by_category.get(cat, 0) + 1

    return {"threats": threats, "total": len(threats), "by_category": by_category}


def build_attack_graph(
    stride_results: dict,
    attack_path_result: Optional[dict] = None,
    graph_data: Optional[dict] = None,
) -> dict:
    """Build an attack graph from STRIDE results + optional attack paths.

    Each STRIDE threat becomes an edge between two asset nodes annotated with
    the primary MITRE ATT&CK TTP for that threat category.  Attack path hops
    (from find_attack_paths) become additional edges annotated by target-node
    type.

    Args:
        stride_results:    Output of run_stride_analysis() or
                           stride_rows_to_results().  Must contain 'threats'.
        attack_path_result: Optional output of find_attack_paths().
        graph_data:        Optional original graph dict for node-type / label
                           resolution when STRIDE results lack that context.

    Returns:
        {
            "nodes":        list[dict],  # sorted by id, deduplicated
            "edges":        list[dict],  # sorted by id, deduplicated
            "total_nodes":  int,
            "total_edges":  int,
        }
    """
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    # Optional enrichment from original graph
    ntype_map: dict[str, str] = {}
    nlabel_map: dict[str, str] = {}
    if graph_data:
        for n in graph_data.get("nodes", []):
            ntype_map[n["id"]] = n.get("type", "")
            nlabel_map[n["id"]] = n.get("label", n["id"])

    def _upsert_node(
        asset_id: str,
        label: str,
        asset_type: str,
        stride_cat: Optional[str],
    ) -> None:
        if asset_id not in nodes:
            nodes[asset_id] = {
                "id": asset_id,
                "label": label,
                "node_type": "asset",
                "asset_type": asset_type,
                "stride_categories": [],
            }
        if stride_cat and stride_cat not in nodes[asset_id]["stride_categories"]:
            nodes[asset_id]["stride_categories"].append(stride_cat)

    # ── STRIDE threats → nodes + edges ───────────────────────────────────────
    for threat in sorted(stride_results.get("threats", []), key=lambda t: t.get("id", "")):
        cat = threat.get("category", "")
        affected = threat.get("affected_assets", [])
        if len(affected) < 2:
            continue

        src_id, tgt_id = str(affected[0]), str(affected[1])

        # Resolve label: prefer graph_data, then split threat 'affected' field
        affected_str = threat.get("affected", "")
        parts = [p.strip() for p in affected_str.split(",")]
        src_label = nlabel_map.get(src_id, parts[0] if parts else src_id)
        tgt_label = nlabel_map.get(tgt_id, parts[-1] if parts else tgt_id)
        src_type = ntype_map.get(src_id, "")
        tgt_type = ntype_map.get(tgt_id, "")

        _upsert_node(src_id, src_label, src_type, cat)
        _upsert_node(tgt_id, tgt_label, tgt_type, cat)

        tactic_key, technique_id = _STRIDE_TTP.get(cat, _HOP_DEFAULT_TTP)
        ttp_meta = _resolve_ttp(tactic_key, technique_id)
        confidence = 0.9 if threat.get("crosses_boundary") else 0.7

        edge_id = f"e-s-{threat.get('id', f'{src_id}-{tgt_id}-{cat}')}"
        edges[edge_id] = {
            "id": edge_id,
            "source": src_id,
            "target": tgt_id,
            "edge_type": "stride_threat",
            "stride_category": cat,
            "threat_title": threat.get("title", ""),
            **ttp_meta,
            "confidence": confidence,
        }

    # ── Attack path hops → nodes + edges ─────────────────────────────────────
    if attack_path_result:
        for path in attack_path_result.get("attack_paths", []):
            hops = path.get("hops", [])
            path_id = path.get("id", "")
            risk_score = float(path.get("risk_score", 50))
            confidence = round(max(0.1, 1.0 - risk_score / 100), 2)

            for i in range(len(hops) - 1):
                src_hop = hops[i]
                tgt_hop = hops[i + 1]
                src_id = src_hop["node_id"]
                tgt_id = tgt_hop["node_id"]
                src_label = src_hop.get("node_label", src_id)
                tgt_label = tgt_hop.get("node_label", tgt_id)
                src_type = src_hop.get("node_type", ntype_map.get(src_id, ""))
                tgt_type = tgt_hop.get("node_type", ntype_map.get(tgt_id, ""))

                _upsert_node(src_id, src_label, src_type, None)
                _upsert_node(tgt_id, tgt_label, tgt_type, None)

                tactic_key, technique_id = _ASSET_HOP_TTP.get(tgt_type, _HOP_DEFAULT_TTP)
                ttp_meta = _resolve_ttp(tactic_key, technique_id)

                edge_id = f"e-h-{path_id}-{i}"
                if edge_id not in edges:
                    edges[edge_id] = {
                        "id": edge_id,
                        "source": src_id,
                        "target": tgt_id,
                        "edge_type": "attack_hop",
                        "attack_path_id": path_id,
                        "hop_index": i,
                        "risk_score": risk_score,
                        **ttp_meta,
                        "confidence": confidence,
                    }

    sorted_nodes = sorted(nodes.values(), key=lambda n: n["id"])
    sorted_edges = sorted(edges.values(), key=lambda e: e["id"])

    return {
        "nodes": sorted_nodes,
        "edges": sorted_edges,
        "total_nodes": len(sorted_nodes),
        "total_edges": len(sorted_edges),
    }


# ── Private ───────────────────────────────────────────────────────────────────


def _resolve_ttp(tactic_key: str, technique_id: str) -> dict:
    """Look up full TTP metadata from MITRE_ATTACK_TECHNIQUES constants."""
    tactic = MITRE_ATTACK_TECHNIQUES.get(tactic_key, {})
    tactic_id = tactic.get("tactic_id", "")
    tactic_name = tactic.get("name", tactic_key)
    for tech in tactic.get("techniques", []):
        if tech["id"] == technique_id:
            return {
                "tactic_id": tactic_id,
                "tactic_name": tactic_name,
                "tactic_key": tactic_key,
                "ttp_id": technique_id,
                "ttp_name": tech["name"],
                "severity": tech.get("severity", "medium"),
            }
    return {
        "tactic_id": tactic_id,
        "tactic_name": tactic_name,
        "tactic_key": tactic_key,
        "ttp_id": technique_id,
        "ttp_name": technique_id,
        "severity": "medium",
    }
