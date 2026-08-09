#!/usr/bin/env python3
# CUI // SP-CTI
"""Reverse Cascade Inference — given an observed conflict event, walk the supply
chain dependency graph upstream to infer the most likely disruption that preceded it.

Algorithm
---------
1. Load sg_sc_nodes / sg_sc_edges from DB; fall back to embedded demo graph.
2. Reverse BFS from unit_id following upstream edges (source→unit reversed).
3. Score each candidate node:
     confidence = propagated_score_fit × lag_fit × recency_weight × node_criticality
   where:
     propagated_score_fit  = EVENT_SUPPLY_AFFINITY[event_type][node_type] × HOP_DECAY^(hop-1)
     lag_fit               = NODE_DISRUPTION_PROFILES[node_type]["lag_weight"]
     recency_weight        = exp(-inferred_lag_days / RECENCY_HALFLIFE_DAYS)
     node_criticality      = CRITICALITY_SCORES[node.criticality]
4. Return top-3 CandidateDisruption objects.
5. Auto-insert highest-confidence candidate as EEI into sg_pir_requirements.

CLI
---
  python tools/strategos/reverse_cascade_inference.py \\
      --event offensive_pause --unit "36th CAA" --date 2026-04-21 --json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection, is_pg  # noqa: E402

logger = get_logger(__name__)

# ── domain constants ──────────────────────────────────────────────────────────

EVENT_TYPES = (
    "offensive_pause",
    "defensive_consolidation",
    "desperate_offensive",
    "capitulation_risk",
    "equipment_destruction",
)

NODE_TYPES = ("unit", "depot", "warehouse", "raw_material", "factory", "port")

# Per node-type disruption onset profile.
# lag_mean: expected days between disruption and observable combat effect.
# lag_std:  standard deviation (days).
# lag_weight: P(event | disruption of this type) — captures onset reliability.
NODE_DISRUPTION_PROFILES: dict[str, dict[str, Any]] = {
    "depot":        {"degradation_onset": "immediate",   "lag_mean": 2,  "lag_std": 2,  "lag_weight": 0.90},
    "warehouse":    {"degradation_onset": "delayed_7d",  "lag_mean": 7,  "lag_std": 3,  "lag_weight": 0.75},
    "raw_material": {"degradation_onset": "delayed_30d", "lag_mean": 30, "lag_std": 7,  "lag_weight": 0.55},
    "factory":      {"degradation_onset": "delayed_14d", "lag_mean": 14, "lag_std": 5,  "lag_weight": 0.65},
    "port":         {"degradation_onset": "delayed_7d",  "lag_mean": 7,  "lag_std": 3,  "lag_weight": 0.70},
    "unit":         {"degradation_onset": "immediate",   "lag_mean": 1,  "lag_std": 1,  "lag_weight": 0.80},
}

# P(event_type explained by disruption at node_type).
EVENT_SUPPLY_AFFINITY: dict[str, dict[str, float]] = {
    "offensive_pause": {
        "depot": 0.90, "warehouse": 0.80, "raw_material": 0.50,
        "factory": 0.60, "port": 0.70, "unit": 0.30,
    },
    "defensive_consolidation": {
        "depot": 0.70, "warehouse": 0.65, "raw_material": 0.45,
        "factory": 0.50, "port": 0.55, "unit": 0.60,
    },
    "desperate_offensive": {
        "depot": 0.80, "warehouse": 0.70, "raw_material": 0.55,
        "factory": 0.55, "port": 0.60, "unit": 0.45,
    },
    "capitulation_risk": {
        "depot": 0.90, "warehouse": 0.88, "raw_material": 0.75,
        "factory": 0.70, "port": 0.80, "unit": 0.50,
    },
    "equipment_destruction": {
        "depot": 0.95, "warehouse": 0.65, "raw_material": 0.40,
        "factory": 0.90, "port": 0.50, "unit": 0.60,
    },
}

CRITICALITY_SCORES: dict[str, float] = {
    "critical": 1.00,
    "high":     0.75,
    "medium":   0.50,
    "low":      0.25,
}

# Score decay per BFS hop (mirrors forward cascade 0.8 factor).
HOP_DECAY: float = 0.80
# Extra propagation days added per hop to account for transit lag.
HOP_LAG_DAYS: int = 2
# Recency half-life: disruptions inferred > N days ago are down-weighted.
RECENCY_HALFLIFE_DAYS: float = 14.0

# ── demo / seed graph ─────────────────────────────────────────────────────────
# Used when sg_sc_nodes / sg_sc_edges tables are empty.

_DEMO_NODES: list[dict[str, Any]] = [
    {"id": "36th-caa",       "node_type": "unit",         "label": "36th CAA",            "criticality": "critical"},
    {"id": "belgorod-2",     "node_type": "depot",        "label": "Belgorod-2 Depot",     "criticality": "critical"},
    {"id": "belgorod-wh",    "node_type": "warehouse",    "label": "Belgorod Warehouse",   "criticality": "high"},
    {"id": "voronezh-wh",    "node_type": "warehouse",    "label": "Voronezh Warehouse",   "criticality": "high"},
    {"id": "lipetsk-fac",    "node_type": "factory",      "label": "Lipetsk Factory",      "criticality": "critical"},
    {"id": "kursk-rm",       "node_type": "raw_material", "label": "Kursk Raw Material",   "criticality": "medium"},
    {"id": "bryansk-port",   "node_type": "port",         "label": "Bryansk Rail Port",    "criticality": "medium"},
]

# Edges: source supplies target (source → target).
_DEMO_EDGES: list[dict[str, Any]] = [
    {"source_id": "belgorod-2",   "target_id": "36th-caa",    "lag_days": 1},
    {"source_id": "belgorod-wh",  "target_id": "belgorod-2",  "lag_days": 2},
    {"source_id": "voronezh-wh",  "target_id": "36th-caa",    "lag_days": 2},
    {"source_id": "lipetsk-fac",  "target_id": "voronezh-wh", "lag_days": 3},
    {"source_id": "kursk-rm",     "target_id": "belgorod-wh", "lag_days": 5},
    {"source_id": "bryansk-port", "target_id": "voronezh-wh", "lag_days": 2},
]

# ── types ─────────────────────────────────────────────────────────────────────


class CandidateDisruption:
    __slots__ = (
        "node_id", "node_type", "node_label", "inferred_date",
        "confidence", "lag_days", "evidence_narrative",
    )

    def __init__(
        self,
        node_id: str,
        node_type: str,
        node_label: str,
        inferred_date: str,
        confidence: float,
        lag_days: int,
        evidence_narrative: str,
    ) -> None:
        self.node_id = node_id
        self.node_type = node_type
        self.node_label = node_label
        self.inferred_date = inferred_date
        self.confidence = confidence
        self.lag_days = lag_days
        self.evidence_narrative = evidence_narrative

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id":            self.node_id,
            "node_type":          self.node_type,
            "node_label":         self.node_label,
            "inferred_date":      self.inferred_date,
            "confidence":         round(self.confidence, 4),
            "lag_days":           self.lag_days,
            "evidence_narrative": self.evidence_narrative,
        }


# ── graph loading ─────────────────────────────────────────────────────────────


def _load_graph(unit_id: str) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Load nodes and reverse-adjacency list from DB or demo data.

    Returns:
        nodes: {node_id → node_dict}
        rev_adj: {node_id → [upstream_node_ids]} (reverse of supply direction)
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    try:
        conn = get_connection()
        try:
            node_rows = conn.execute(
                "SELECT id, node_type, label, criticality "
                "FROM sg_sc_nodes"
            ).fetchall()
            edge_rows = conn.execute(
                "SELECT source_id, target_id, lag_days "
                "FROM sg_sc_edges"
            ).fetchall()

            def _d(row: Any, keys: list[str]) -> dict:
                if isinstance(row, dict):
                    return dict(row)
                return dict(zip(keys, row))

            for r in node_rows:
                n = _d(r, ["id", "node_type", "label", "criticality"])
                nodes[n["id"]] = n
            for r in edge_rows:
                edges.append(_d(r, ["source_id", "target_id", "lag_days"]))
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("DB load failed, using demo graph: %s", exc)

    if not nodes:
        logger.debug("No nodes in DB — using embedded demo graph")
        for n in _DEMO_NODES:
            nodes[n["id"]] = n
        edges = list(_DEMO_EDGES)

    # Build reverse adjacency: for each edge (src → tgt), record src as upstream of tgt.
    rev_adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in edges:
        src, tgt = e["source_id"], e["target_id"]
        if tgt in rev_adj and src in nodes:
            rev_adj[tgt].append(src)

    return nodes, rev_adj


# ── reverse BFS ───────────────────────────────────────────────────────────────


def _reverse_bfs(
    start_id: str,
    nodes: dict[str, dict],
    rev_adj: dict[str, list[str]],
    max_depth: int = 6,
) -> list[tuple[str, int]]:
    """BFS upstream from start_id.

    Returns list of (node_id, hop_depth) for all reachable upstream nodes,
    excluding the start node itself.
    """
    visited: set[str] = {start_id}
    queue: deque[tuple[str, int]] = deque([(start_id, 0)])
    result: list[tuple[str, int]] = []

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for upstream_id in rev_adj.get(current, []):
            if upstream_id not in visited:
                visited.add(upstream_id)
                hop = depth + 1
                result.append((upstream_id, hop))
                queue.append((upstream_id, hop))

    return result


# ── scoring ───────────────────────────────────────────────────────────────────


def _score_candidate(
    node: dict[str, Any],
    hop: int,
    event_type: str,
    observed_date: datetime,
) -> tuple[float, int, str]:
    """Compute (confidence, lag_days, inferred_date_iso) for one upstream node.

    confidence = propagated_score_fit × lag_fit × recency_weight × node_criticality
    """
    node_type = node.get("node_type", "depot")
    profile = NODE_DISRUPTION_PROFILES.get(node_type, NODE_DISRUPTION_PROFILES["depot"])
    affinity = EVENT_SUPPLY_AFFINITY.get(event_type, EVENT_SUPPLY_AFFINITY["offensive_pause"])

    # propagated_score_fit: affinity decayed by hop distance.
    base_affinity = affinity.get(node_type, 0.5)
    propagated_score_fit = base_affinity * (HOP_DECAY ** (hop - 1))

    # lag_fit: how strongly this node type's onset matches observed event timing.
    lag_fit = profile["lag_weight"]

    # Total estimated lag = node onset mean + extra propagation per hop.
    lag_days = profile["lag_mean"] + (hop - 1) * HOP_LAG_DAYS
    inferred_dt = observed_date - timedelta(days=lag_days)
    inferred_date_iso = inferred_dt.strftime("%Y-%m-%d")

    # recency_weight: penalise disruptions inferred far in the past.
    recency_weight = math.exp(-lag_days / RECENCY_HALFLIFE_DAYS)

    # node_criticality: label → float.
    criticality_label = node.get("criticality", "medium")
    node_criticality = CRITICALITY_SCORES.get(criticality_label, 0.50)

    confidence = propagated_score_fit * lag_fit * recency_weight * node_criticality

    return confidence, lag_days, inferred_date_iso


def _build_narrative(
    node: dict[str, Any],
    event_type: str,
    unit_label: str,
    observed_date_iso: str,
    confidence: float,
    lag_days: int,
    inferred_date_iso: str,
) -> str:
    profile = NODE_DISRUPTION_PROFILES.get(
        node.get("node_type", "depot"), NODE_DISRUPTION_PROFILES["depot"]
    )
    onset_label = profile["degradation_onset"].replace("_", " ")
    return (
        f"Observed {unit_label} {event_type.replace('_', ' ')} {observed_date_iso} → "
        f"most likely: {node['label']} disruption ~{inferred_date_iso} "
        f"(P={confidence:.2f}, lag={lag_days}d, onset={onset_label}). "
        f"EEI auto-raised: confirm {node['node_type']} status via ISR."
    )


# ── PIR auto-insert ───────────────────────────────────────────────────────────


def _insert_eei(node: dict[str, Any], inferred_date_iso: str, confidence: float) -> str | None:
    """Insert highest-confidence candidate as EEI into sg_pir_requirements.

    Returns the new EEI id, or None on failure.
    """
    pir_id = f"eei-rci-{uuid.uuid4().hex[:8]}"
    topic = f"Confirm disruption at {node['label']}"
    description = (
        f"Reverse cascade inference inferred {node['node_type']} disruption "
        f"at {node['label']} (~{inferred_date_iso}) with confidence {confidence:.2f}. "
        f"ISR tasking required to confirm or deny."
    )
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        conn = get_connection()
        try:
            if is_pg():
                conn.execute(
                    "INSERT INTO sg_pir_requirements "
                    "(id, pir_type, topic, description, collection_priority, "
                    " status, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (pir_id, "EEI", topic, description, 2, "active", now_iso, now_iso),
                )
            else:
                conn.execute(
                    "INSERT INTO sg_pir_requirements "
                    "(id, pir_type, topic, description, collection_priority, "
                    " status, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (pir_id, "EEI", topic, description, 2, "active", now_iso, now_iso),
                )
            conn.commit()
            logger.info("EEI inserted: %s — %s", pir_id, topic)
            return pir_id
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Could not insert EEI into sg_pir_requirements: %s", exc)
        return None


# ── public API ────────────────────────────────────────────────────────────────


def infer(
    event_type: str,
    unit_id: str,
    observed_date: str,
    top_n: int = 3,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run reverse cascade inference.

    Parameters
    ----------
    event_type:    One of EVENT_TYPES.
    unit_id:       Unit node ID or label (matched case-insensitively if not found directly).
    observed_date: ISO-8601 date string (YYYY-MM-DD) of the observed conflict event.
    top_n:         Number of candidates to return (default 3).
    dry_run:       If True, skip EEI insert.

    Returns
    -------
    dict with keys: event_type, unit_id, observed_date, candidates (list),
                    eei_id (str|None), summary (str).
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"event_type must be one of {EVENT_TYPES}")

    # Parse observed date.
    try:
        obs_dt = datetime.strptime(observed_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"observed_date must be YYYY-MM-DD, got {observed_date!r}") from exc

    nodes, rev_adj = _load_graph(unit_id)

    # Resolve unit_id: exact match first, then case-insensitive label match.
    resolved_unit_id = unit_id
    if unit_id not in nodes:
        lower_target = unit_id.lower()
        for nid, n in nodes.items():
            if n.get("label", "").lower() == lower_target or nid.lower() == lower_target:
                resolved_unit_id = nid
                break

    unit_node = nodes.get(resolved_unit_id, {"id": resolved_unit_id, "label": unit_id,
                                              "node_type": "unit", "criticality": "critical"})
    unit_label = unit_node.get("label", unit_id)

    upstream = _reverse_bfs(resolved_unit_id, nodes, rev_adj)

    candidates: list[CandidateDisruption] = []
    for node_id, hop in upstream:
        node = nodes[node_id]
        confidence, lag_days, inferred_date_iso = _score_candidate(
            node, hop, event_type, obs_dt
        )
        narrative = _build_narrative(
            node, event_type, unit_label, observed_date[:10],
            confidence, lag_days, inferred_date_iso,
        )
        candidates.append(CandidateDisruption(
            node_id=node_id,
            node_type=node.get("node_type", "unknown"),
            node_label=node.get("label", node_id),
            inferred_date=inferred_date_iso,
            confidence=confidence,
            lag_days=lag_days,
            evidence_narrative=narrative,
        ))

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    top_candidates = candidates[:top_n]

    eei_id: str | None = None
    if top_candidates and not dry_run:
        best = top_candidates[0]
        eei_id = _insert_eei(nodes.get(best.node_id, {"label": best.node_label,
                                                        "node_type": best.node_type}),
                              best.inferred_date, best.confidence)

    summary = (
        f"No upstream nodes found for unit '{unit_id}'."
        if not top_candidates
        else top_candidates[0].evidence_narrative
    )

    return {
        "event_type":     event_type,
        "unit_id":        unit_id,
        "observed_date":  observed_date[:10],
        "candidates":     [c.to_dict() for c in top_candidates],
        "eei_id":         eei_id,
        "summary":        summary,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reverse Cascade Inference: given an observed conflict event, "
            "infer the most likely upstream supply chain disruption. "
            "CUI // SP-CTI"
        )
    )
    parser.add_argument(
        "--event", required=True,
        choices=list(EVENT_TYPES),
        help="Observed conflict event type",
    )
    parser.add_argument(
        "--unit", required=True,
        help="Unit node ID or label (e.g. '36th CAA')",
    )
    parser.add_argument(
        "--date", required=True,
        help="Observed event date YYYY-MM-DD (e.g. 2026-04-21)",
    )
    parser.add_argument(
        "--top-n", type=int, default=3,
        help="Number of candidates to return (default: 3)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip EEI insert into sg_pir_requirements",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Output result as JSON",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    result = infer(
        event_type=args.event,
        unit_id=args.unit,
        observed_date=args.date,
        top_n=args.top_n,
        dry_run=args.dry_run,
    )

    if args.as_json:
        print(json.dumps(result, indent=2))
        return

    print("\nReverse Cascade Inference — CUI // SP-CTI")
    print(f"Event      : {result['event_type']}")
    print(f"Unit       : {result['unit_id']}")
    print(f"Observed   : {result['observed_date']}")
    print()
    if not result["candidates"]:
        print("No upstream candidate disruptions found.")
        return

    print(f"Top-{len(result['candidates'])} Candidate Disruptions:")
    for i, c in enumerate(result["candidates"], 1):
        print(f"  [{i}] {c['node_label']} ({c['node_type']})")
        print(f"       Inferred date : {c['inferred_date']}")
        print(f"       Confidence    : {c['confidence']:.4f}")
        print(f"       Lag           : {c['lag_days']}d")
        print(f"       Narrative     : {c['evidence_narrative']}")
        print()

    if result.get("eei_id"):
        print(f"EEI auto-raised: {result['eei_id']}")
    elif args.dry_run:
        print("(dry-run: EEI not inserted)")

    print(f"\nSummary: {result['summary']}")


if __name__ == "__main__":
    main()
