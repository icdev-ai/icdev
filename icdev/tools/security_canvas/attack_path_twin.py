# [CUI // SP-CTI]
"""ICDEV™ SDC Attack Path Twin — BAS-style replay on attack graph.

Formalizes the STRIDE/attack graph as a queryable structure where:
  - Nodes = assets with classification levels
  - Edges = exploitable transitions annotated with MITRE ATT&CK TTP,
            confidence score (0-1), and prerequisites

Provides:
  build_attack_graph()   — convert raw SDC graph_data into formal attack graph
  replay_attack_paths()  — Dijkstra min-cost path enumeration (BAS-style replay)
  query_attack_paths()   — IQE-compatible predicate filter

No Flask, no LLM — pure deterministic functions.
"""

from __future__ import annotations

import heapq
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.security_canvas.attack_path_twin")

# ── MITRE ATT&CK TTP assignment rules ────────────────────────────────────────
# Each rule: (condition_fn, ttp_id, ttp_name, tactic, confidence_penalty)
# condition_fn receives (src_type, tgt_type, edge) and returns bool.
# First matching rule wins.

_TTP_RULES: list[tuple[Any, str, str, str, float]] = [
    # Initial Access — internet boundary → any asset
    (
        lambda s, t, e: s in ("boundary-internet", "asset-client") or t in ("boundary-internet",),
        "T1190",
        "Exploit Public-Facing Application",
        "initial_access",
        0.0,
    ),
    # Credential Access — unauthenticated edge to database/storage
    (
        lambda s, t, e: t in ("asset-database", "asset-storage") and not e.get("authenticated"),
        "T1552",
        "Unsecured Credentials",
        "credential_access",
        0.0,
    ),
    # Info Disclosure — unencrypted boundary crossing
    (
        lambda s, t, e: e.get("crosses_boundary") and not e.get("encrypted"),
        "T1040",
        "Network Sniffing",
        "collection",
        0.1,
    ),
    # Lateral Movement — asset-server to asset-server
    (
        lambda s, t, e: s.startswith("asset-") and t.startswith("asset-"),
        "T1021",
        "Remote Services",
        "lateral_movement",
        0.2,
    ),
    # Privilege Escalation — reaching privileged control nodes without PAM
    (
        lambda s, t, e: t in ("ctrl-kms", "ctrl-pam") and not e.get("authenticated"),
        "T1068",
        "Exploitation for Privilege Escalation",
        "privilege_escalation",
        0.0,
    ),
    # Defense Evasion — unmonitored path (no SIEM on hop)
    (
        lambda s, t, e: not e.get("monitored", False) and t.startswith("asset-"),
        "T1562",
        "Impair Defenses",
        "defense_evasion",
        0.1,
    ),
    # Exfiltration — reaching storage/database with outbound path
    (
        lambda s, t, e: t in ("asset-storage", "asset-database") and e.get("encrypted"),
        "T1041",
        "Exfiltration Over C2 Channel",
        "exfiltration",
        0.2,
    ),
    # Default — generic exploitation
    (
        lambda s, t, e: True,
        "T1059",
        "Command and Scripting Interpreter",
        "execution",
        0.3,
    ),
]

# ── Classification level ordering (higher = more sensitive) ──────────────────
_CLASS_ORDER = {"public": 0, "il2": 1, "cui": 2, "il4": 3, "il5": 4, "il6": 5, "secret": 5}

# Default classification by boundary type
_BOUNDARY_CLASS: dict[str, str] = {
    "boundary-internet": "public",
    "boundary-dmz": "il2",
    "boundary-authorization": "il4",
    "boundary-classification": "il5",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _assign_node_classification(node_id: str, boundaries: list[dict]) -> str:
    """Return the highest classification level of any boundary containing this node."""
    best = "public"
    best_ord = 0
    for b in boundaries:
        if node_id not in b.get("contained_assets", []):
            continue
        cls = b.get("classification") or _BOUNDARY_CLASS.get(b.get("type", ""), "public")
        cls = cls.lower()
        ord_ = _CLASS_ORDER.get(cls, 0)
        if ord_ > best_ord:
            best = cls
            best_ord = ord_
    return best


def _classify_edge(src: str, tgt: str, boundaries: list[dict]) -> tuple[bool, str | None, str | None]:
    """Return (crosses_boundary, src_class, tgt_class) for an edge."""
    src_cls = _assign_node_classification(src, boundaries)
    tgt_cls = _assign_node_classification(tgt, boundaries)
    crosses = src_cls != tgt_cls
    return crosses, src_cls, tgt_cls


def _assign_ttp(src_type: str, tgt_type: str, edge: dict) -> tuple[str, str, str]:
    """Return (ttp_id, ttp_name, tactic) by matching TTP rules."""
    for condition, ttp_id, ttp_name, tactic, _ in _TTP_RULES:
        try:
            if condition(src_type, tgt_type, edge):
                return ttp_id, ttp_name, tactic
        except Exception:
            continue
    return "T1059", "Command and Scripting Interpreter", "execution"


def _compute_confidence(edge: dict, src_type: str, tgt_type: str) -> float:
    """Compute edge exploitability confidence (0=easy, 1=hard)."""
    conf = 0.5  # baseline
    if edge.get("authenticated"):
        conf += 0.2
    if edge.get("encrypted"):
        conf += 0.1
    if tgt_type.startswith("ctrl-"):
        conf += 0.2
    # Apply TTP-specific penalty
    for condition, _, _, _, penalty in _TTP_RULES:
        try:
            if condition(src_type, tgt_type, edge):
                conf -= penalty
                break
        except Exception:
            continue
    return round(max(0.05, min(0.95, conf)), 3)


def _build_prerequisites(edge: dict, src_type: str, tgt_type: str) -> list[str]:
    """Return list of prerequisite conditions for this hop."""
    prereqs: list[str] = []
    if edge.get("authenticated"):
        prereqs.append("valid_credentials_on_source")
    if src_type == "asset-client":
        prereqs.append("attacker_controls_client")
    if src_type == "boundary-internet":
        prereqs.append("network_reachability")
    if tgt_type in ("asset-database", "asset-storage") and not edge.get("encrypted"):
        prereqs.append("cleartext_channel_available")
    if tgt_type.startswith("ctrl-"):
        prereqs.append("admin_interface_exposed")
    if not edge.get("encrypted") and edge.get("crosses_boundary"):
        prereqs.append("network_position_to_intercept")
    if not prereqs:
        prereqs.append("network_access_to_target")
    return prereqs


# ── Public API ────────────────────────────────────────────────────────────────


def build_attack_graph(graph_data: dict) -> dict:
    """Convert raw SDC graph_data into a formal, queryable attack graph.

    Each node gains a classification_level derived from the boundary zones
    it sits inside. Each edge gains TTP annotation, confidence score, cost
    (inverse confidence), and prerequisites.

    Args:
        graph_data: Dict with "nodes", "edges", "boundaries".

    Returns:
        {
          "nodes": [...extended with classification_level...],
          "edges": [...extended with ttp, confidence, cost, prerequisites, crosses_boundary...],
          "entry_points": [...node ids...],
          "high_value_targets": [...node ids...],
          "classification_summary": {...}
        }
    """
    raw_nodes: list[dict] = graph_data.get("nodes", [])
    raw_edges: list[dict] = graph_data.get("edges", [])
    boundaries: list[dict] = graph_data.get("boundaries", [])

    ntypes = {n["id"]: n.get("type", "") for n in raw_nodes}

    # ── Annotate nodes with classification ──
    ag_nodes = []
    for n in raw_nodes:
        cls = _assign_node_classification(n["id"], boundaries)
        ag_nodes.append({**n, "classification_level": cls})

    # ── Annotate edges with TTP, confidence, cost, prerequisites ──
    ag_edges = []
    for e in raw_edges:
        src, tgt = e["source"], e["target"]
        src_type = ntypes.get(src, "")
        tgt_type = ntypes.get(tgt, "")
        crosses, src_cls, tgt_cls = _classify_edge(src, tgt, boundaries)

        enriched_edge = {
            **e,
            "crosses_boundary": crosses,
            "src_classification": src_cls,
            "tgt_classification": tgt_cls,
        }

        ttp_id, ttp_name, tactic = _assign_ttp(src_type, tgt_type, enriched_edge)
        confidence = _compute_confidence(enriched_edge, src_type, tgt_type)
        cost = round(1.0 - confidence, 3)
        prereqs = _build_prerequisites(enriched_edge, src_type, tgt_type)

        ag_edges.append({
            **enriched_edge,
            "ttp": ttp_id,
            "ttp_name": ttp_name,
            "tactic": tactic,
            "confidence": confidence,
            "cost": cost,
            "prerequisites": prereqs,
        })

    # ── Identify entry points and high-value targets ──
    inet_ids = {n["id"] for n in raw_nodes if ntypes.get(n["id"]) == "boundary-internet"}
    entry_ids: set[str] = set()
    for e in raw_edges:
        if e["source"] in inet_ids:
            entry_ids.add(e["target"])
        if e["target"] in inet_ids:
            entry_ids.add(e["source"])
    for n in raw_nodes:
        if ntypes.get(n["id"]) == "asset-client":
            entry_ids.add(n["id"])

    target_ids = {n["id"] for n in raw_nodes if ntypes.get(n["id"]) in ("asset-database", "asset-storage")}

    # ── Classification summary ──
    class_dist: dict[str, int] = {}
    for n in ag_nodes:
        cls = n["classification_level"]
        class_dist[cls] = class_dist.get(cls, 0) + 1

    return {
        "nodes": ag_nodes,
        "edges": ag_edges,
        "entry_points": sorted(entry_ids),
        "high_value_targets": sorted(target_ids),
        "classification_summary": class_dist,
    }


def replay_attack_paths(
    graph_data: dict,
    *,
    max_paths_per_target: int = 5,
    max_hops: int = 10,
) -> dict:
    """BAS-style replay: enumerate min-cost attack paths via Dijkstra.

    Each path is an ordered sequence of hops; each hop names the MITRE
    ATT&CK TTP exploited to traverse that edge. Paths crossing classification
    boundaries are flagged as classification violations.

    Args:
        graph_data: Raw SDC graph_data dict.
        max_paths_per_target: Cap on paths per (entry, target) pair.
        max_hops: Maximum path length.

    Returns:
        {
          "replay_paths": [
            {
              "id": "RP-...",
              "entry": {...},
              "target": {...},
              "total_cost": float,
              "ttp_sequence": ["T1190", "T1021", ...],
              "hops": [
                {
                  "step": int,
                  "node_id": str,
                  "node_label": str,
                  "node_type": str,
                  "classification_level": str,
                  "ttp": str,
                  "ttp_name": str,
                  "tactic": str,
                  "confidence": float,
                  "prerequisites": [...],
                  "crosses_boundary": bool,
                  "classification_violation": bool,
                }
              ],
              "classification_violations": [...boundary-crossing details...],
              "risk_level": "critical"|"high"|"medium"|"low",
            }
          ],
          "total_paths": int,
          "critical_paths": int,
          "classification_violations_total": int,
        }
    """
    ag = build_attack_graph(graph_data)
    ag_nodes = ag["nodes"]
    ag_edges = ag["edges"]
    entry_ids = ag["entry_points"]
    target_ids = ag["high_value_targets"]

    node_map = {n["id"]: n for n in ag_nodes}
    labels = {n["id"]: n.get("label", n["id"]) for n in ag_nodes}

    # Build adjacency: {src: [(cost, tgt, edge_dict)]}
    adj: dict[str, list[tuple[float, str, dict]]] = {}
    for e in ag_edges:
        src, tgt = e["source"], e["target"]
        cost = e["cost"]
        adj.setdefault(src, []).append((cost, tgt, e))
        adj.setdefault(tgt, []).append((cost, src, e))  # undirected

    replay_paths: list[dict] = []

    for entry_id in sorted(entry_ids):
        for target_id in sorted(target_ids):
            if entry_id == target_id:
                continue

            # Dijkstra — find k shortest paths (Yen's k-shortest approximation via re-runs)
            found = 0
            visited_paths: set[tuple[str, ...]] = set()

            # State: (cost, node_id, path_nodes, hop_details)
            heap: list[tuple[float, str, list[str], list[dict]]] = [
                (0.0, entry_id, [entry_id], [])
            ]

            while heap and found < max_paths_per_target:
                cost_so_far, current, path_nodes, hops = heapq.heappop(heap)

                if current == target_id:
                    path_key = tuple(path_nodes)
                    if path_key in visited_paths:
                        continue
                    visited_paths.add(path_key)

                    # Build TTP sequence and detect classification violations
                    ttp_seq: list[str] = [h["ttp"] for h in hops]
                    violations: list[dict] = []
                    for h in hops:
                        if h.get("crosses_boundary") and _CLASS_ORDER.get(
                            h.get("tgt_classification", "public"), 0
                        ) > _CLASS_ORDER.get(h.get("src_classification", "public"), 0):
                            violations.append({
                                "step": h["step"],
                                "from_classification": h["src_classification"],
                                "to_classification": h["tgt_classification"],
                                "ttp": h["ttp"],
                                "description": (
                                    f"Step {h['step']}: path crosses from "
                                    f"{h['src_classification'].upper()} → "
                                    f"{h['tgt_classification'].upper()} "
                                    f"via {h['ttp']} ({h['ttp_name']})"
                                ),
                            })

                    # Compute risk level from total cost (lower cost = more dangerous)
                    risk_level = (
                        "critical" if cost_so_far < 1.0
                        else "high" if cost_so_far < 2.0
                        else "medium" if cost_so_far < 3.5
                        else "low"
                    )

                    path_id = f"RP-{entry_id[:6]}-{target_id[:6]}-{len(replay_paths)}"
                    entry_node = node_map.get(entry_id, {"id": entry_id})
                    target_node = node_map.get(target_id, {"id": target_id})

                    replay_paths.append({
                        "id": path_id,
                        "entry": {
                            "id": entry_id,
                            "label": labels.get(entry_id, entry_id),
                            "type": entry_node.get("type", ""),
                            "classification_level": entry_node.get("classification_level", "public"),
                        },
                        "target": {
                            "id": target_id,
                            "label": labels.get(target_id, target_id),
                            "type": target_node.get("type", ""),
                            "classification_level": target_node.get("classification_level", "public"),
                        },
                        "total_cost": round(cost_so_far, 3),
                        "ttp_sequence": ttp_seq,
                        "hops": hops,
                        "classification_violations": violations,
                        "has_classification_violation": len(violations) > 0,
                        "risk_level": risk_level,
                        "hop_count": len(path_nodes),
                    })
                    found += 1
                    continue

                if len(path_nodes) >= max_hops:
                    continue

                for edge_cost, neighbor, edge in adj.get(current, []):
                    if neighbor in path_nodes:
                        continue
                    neighbor_node = node_map.get(neighbor, {})
                    hop_detail = {
                        "step": len(hops) + 1,
                        "node_id": neighbor,
                        "node_label": labels.get(neighbor, neighbor),
                        "node_type": neighbor_node.get("type", ""),
                        "classification_level": neighbor_node.get("classification_level", "public"),
                        "ttp": edge.get("ttp", "T1059"),
                        "ttp_name": edge.get("ttp_name", ""),
                        "tactic": edge.get("tactic", "execution"),
                        "confidence": edge.get("confidence", 0.5),
                        "cost": edge.get("cost", 0.5),
                        "prerequisites": edge.get("prerequisites", []),
                        "crosses_boundary": edge.get("crosses_boundary", False),
                        "src_classification": edge.get("src_classification", "public"),
                        "tgt_classification": edge.get("tgt_classification", "public"),
                        "classification_violation": (
                            edge.get("crosses_boundary", False)
                            and _CLASS_ORDER.get(edge.get("tgt_classification", "public"), 0)
                            > _CLASS_ORDER.get(edge.get("src_classification", "public"), 0)
                        ),
                    }
                    heapq.heappush(
                        heap,
                        (
                            cost_so_far + edge_cost,
                            neighbor,
                            path_nodes + [neighbor],
                            hops + [hop_detail],
                        ),
                    )

    critical_paths = sum(1 for p in replay_paths if p["risk_level"] == "critical")
    violations_total = sum(1 for p in replay_paths if p["has_classification_violation"])

    return {
        "replay_paths": replay_paths,
        "total_paths": len(replay_paths),
        "critical_paths": critical_paths,
        "classification_violations_total": violations_total,
    }


# ── IQE Query Interface ───────────────────────────────────────────────────────
# Supported syntax:
#   foreach path in attack_paths [where <predicate>] select <fields>
# Predicate fields: risk_level, total_cost, hop_count, has_classification_violation,
#                   ttp_sequence (list), entry.type, target.type
# Fields: "all" or comma-separated names

_ALLOWED_PRED_FIELDS = {
    "risk_level", "total_cost", "hop_count",
    "has_classification_violation", "ttp_sequence",
}


def _safe_eval_predicate(predicate: str, path: dict) -> bool:
    """Evaluate a simple predicate string against a path dict (no exec, no eval)."""
    pred = predicate.strip()
    if not pred:
        return True

    # Support: field == value, field != value, field < value, field > value,
    #          field <= value, field >= value, value in field
    ops = [" == ", " != ", " <= ", " >= ", " < ", " > ", " in ", " not in "]
    for op in ops:
        if op not in pred:
            continue
        left, right = pred.split(op, 1)
        left, right = left.strip(), right.strip()

        # "value in field" pattern
        if op.strip() == "in":
            # Pattern: 'T1190' in ttp_sequence — left is literal, right is field
            try:
                lit_val = left.strip('"').strip("'")
                field_val = _resolve_field(right, path)
                if isinstance(field_val, list):
                    return lit_val in field_val
                return str(lit_val) == str(field_val)
            except Exception:
                return False

        lv = _resolve_field(left, path)
        rv = _resolve_literal(right)

        try:
            if op == " == ":
                return lv == rv
            if op == " != ":
                return lv != rv
            if op == " < ":
                return float(lv) < float(rv)
            if op == " > ":
                return float(lv) > float(rv)
            if op == " <= ":
                return float(lv) <= float(rv)
            if op == " >= ":
                return float(lv) >= float(rv)
        except (TypeError, ValueError):
            return False

    # Boolean field shorthand: "has_classification_violation"
    if pred in _ALLOWED_PRED_FIELDS:
        return bool(_resolve_field(pred, path))

    # Unknown/unrecognized predicate → fail-closed (exclude the path). In a
    # security attack-graph, silently widening results to include every path
    # would hide real exposure behind a malformed filter.
    logger.warning(
        "attack_path_twin: unrecognized predicate %r — excluding path (fail-closed)",
        pred,
    )
    return False


def _resolve_field(field: str, path: dict) -> Any:
    """Resolve dot-notation field against path dict."""
    parts = field.split(".")
    val: Any = path
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    return val


def _resolve_literal(s: str) -> Any:
    """Parse a literal value from string."""
    s = s.strip()
    if s.startswith(("'", '"')):
        return s.strip("'\"")
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _project_path(path: dict, fields: list[str]) -> dict:
    """Project selected fields from a path dict."""
    if not fields or fields == ["all"]:
        return path
    result = {}
    for f in fields:
        f = f.strip()
        val = _resolve_field(f, path)
        if val is not None:
            result[f] = val
    return result


def query_attack_paths(graph_data: dict, iqe_query: str) -> dict:
    """Execute an IQE-style query against the replay attack paths.

    Supported syntax:
      foreach path in attack_paths [where <predicate>] select <fields>

    Examples:
      foreach path in attack_paths where risk_level == 'critical' select all
      foreach path in attack_paths where total_cost < 1.5 select id, ttp_sequence, risk_level
      foreach path in attack_paths where has_classification_violation select id, classification_violations
      foreach path in attack_paths where 'T1190' in ttp_sequence select id, hop_count, risk_level

    Args:
        graph_data: Raw SDC graph_data dict.
        iqe_query: IQE query string.

    Returns:
        {"query": str, "results": [...], "count": int, "errors": [...]}
    """
    errors: list[str] = []
    query = iqe_query.strip()

    # Parse: foreach path in attack_paths [where <pred>] select <fields>
    where_clause = ""
    select_clause = "all"

    try:
        q_lower = query.lower()
        if "select" not in q_lower:
            errors.append("Missing 'select' clause")
            return {"query": query, "results": [], "count": 0, "errors": errors}

        # Extract select
        sel_idx = q_lower.rfind("select")
        select_clause = query[sel_idx + 6:].strip()
        remainder = query[:sel_idx].strip()

        # Extract where
        rem_lower = remainder.lower()
        if "where" in rem_lower:
            where_idx = rem_lower.index("where")
            where_clause = remainder[where_idx + 5:].strip()

        # Run replay
        replay_result = replay_attack_paths(graph_data)
        paths = replay_result["replay_paths"]

        # Apply predicate
        filtered = [p for p in paths if _safe_eval_predicate(where_clause, p)]

        # Project fields
        fields = [f.strip() for f in select_clause.split(",")]
        projected = [_project_path(p, fields) for p in filtered]

    except Exception as exc:
        errors.append(f"Query error: {exc}")
        projected = []

    return {
        "query": query,
        "results": projected,
        "count": len(projected),
        "errors": errors,
    }
