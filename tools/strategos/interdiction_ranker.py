#!/usr/bin/env python3
# CUI // SP-CTI
"""Logistics Interdiction COA Ranker — sg-sc-06.

For each adversary logistics node in sg_supply_nodes:
  1. propagate_impact_sg(node_id, severity='critical')
       → blast_radius = count of degraded frontline units
  2. composite_score = blast_radius × criticality_score × substitutability_inverse
  3. rank_interdiction_targets(top_n=10) → priority list

ConflictEvent integration:
  write_interdicts_edge(event_id, supply_node_id) writes an INTERDICTS edge
  to sg_kg_edges whenever a ConflictEvent targets a supply node.

COA runner integration:
  get_supply_degradation_coefficients(top_n=10) returns per-unit attrition
  adjustment factors derived from the top-N interdiction targets.

Tables:
  sg_supply_nodes           — adversary logistics nodes
  sg_supply_edges           — dependency edges between supply nodes
  sg_interdiction_results   — ranked results (append-only)
  sg_kg_nodes               — Strategos KG node registry
  sg_kg_edges               — Strategos KG edge registry (INTERDICTS written here)
  sg_orbat_units            — frontline unit registry
  sg_conflict_events        — event log (read-only in this module)

CLI:
  python tools/strategos/interdiction_ranker.py --rank --top-n 10 --json
  python tools/strategos/interdiction_ranker.py --propagate <node_id> --json
  python tools/strategos/interdiction_ranker.py --write-interdict --event-id <id> --node-id <id>
  python tools/strategos/interdiction_ranker.py --add-node --label "Kramatorsk Rail Hub" ...
  python tools/strategos/interdiction_ranker.py --coefficients --top-n 10 --json
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NODE_TYPES = (
    "depot", "rail_hub", "port", "fuel_dump", "ammo_cache",
    "bridge", "road_junction", "airfield", "pipeline", "command_post",
)

CRITICALITY_SCORE: dict[str, float] = {
    "critical": 1.0,
    "high": 0.75,
    "medium": 0.5,
    "low": 0.25,
}

EDGE_TYPES = ("SUPPLIES", "DEPENDS_ON", "ROUTES_THROUGH", "BACKS_UP")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Supply node management
# ---------------------------------------------------------------------------


def add_supply_node(
    label: str,
    node_type: str = "depot",
    criticality: str = "medium",
    substitutability: float = 0.5,
    controlled_by: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    metadata: dict | None = None,
) -> dict:
    """Insert a new adversary logistics node."""
    if node_type not in NODE_TYPES:
        raise ValueError(f"node_type must be one of {NODE_TYPES}")
    if criticality not in CRITICALITY_SCORE:
        raise ValueError(f"criticality must be one of {list(CRITICALITY_SCORE)}")
    if not (0.0 <= substitutability <= 1.0):
        raise ValueError("substitutability must be between 0.0 and 1.0")

    node_id = str(uuid.uuid4())
    now = _now()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO sg_supply_nodes
               (node_id, label, node_type, criticality, substitutability,
                controlled_by, lat, lon, metadata_json, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                node_id, label, node_type, criticality, substitutability,
                controlled_by, lat, lon,
                json.dumps(metadata) if metadata else None,
                now, now,
            ),
        )
        # Mirror to sg_kg_nodes for KG visualiser
        conn.execute(
            """INSERT OR IGNORE INTO sg_kg_nodes
               (node_id, node_type, label, created_at)
               VALUES (%s, %s, %s, %s)""",
            (node_id, "supply", label, now),
        )
        conn.commit()
        return {"node_id": node_id, "label": label, "node_type": node_type}
    finally:
        conn.close()


def add_supply_edge(
    source_id: str,
    target_id: str,
    edge_type: str = "SUPPLIES",
    weight: float = 1.0,
) -> dict:
    """Add a directed dependency edge between two supply nodes."""
    if edge_type not in EDGE_TYPES:
        raise ValueError(f"edge_type must be one of {EDGE_TYPES}")
    edge_id = str(uuid.uuid4())
    now = _now()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO sg_supply_edges (id, source_id, target_id, edge_type, weight, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (edge_id, source_id, target_id, edge_type, weight, now),
        )
        # Mirror to sg_kg_edges
        conn.execute(
            """INSERT OR IGNORE INTO sg_kg_edges (id, source_id, target_id, relation, created_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (str(uuid.uuid4()), source_id, target_id, edge_type, now),
        )
        conn.commit()
        return {"edge_id": edge_id, "source_id": source_id, "target_id": target_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Impact propagation
# ---------------------------------------------------------------------------


def propagate_impact_sg(node_id: str, severity: str = "critical") -> dict:
    """Propagate interdiction impact from a supply node through the logistics graph.

    Traces:
      1. Downstream supply nodes degraded via sg_supply_edges (BFS, 0.8 decay/hop)
      2. Frontline units that depend on any degraded supply node via sg_kg_edges
         with relation='SUPPLIED_BY'

    blast_radius = count of unique frontline units degraded.

    Returns:
        dict with node_id, severity, blast_radius, degraded_supply_nodes,
        degraded_units, composite inputs.
    """
    severity_values = {"critical": 10.0, "high": 7.5, "medium": 5.0, "low": 2.5}
    base_score = severity_values.get(severity, 10.0)
    decay = 0.8

    conn = get_connection()
    try:
        # --- Load supply graph edges ---
        supply_rows = conn.execute(
            "SELECT source_id, target_id, weight FROM sg_supply_edges"
        ).fetchall()
        forward: dict[str, list[str]] = {}
        for r in supply_rows:
            forward.setdefault(r["source_id"], []).append(r["target_id"])

        # --- BFS downstream through supply network ---
        degraded_supply: list[dict] = []
        seen = {node_id}
        queue: deque = deque([(node_id, 0, base_score)])
        while queue:
            current, depth, score = queue.popleft()
            for neighbor in forward.get(current, []):
                if neighbor not in seen:
                    seen.add(neighbor)
                    decayed = round(score * decay, 3)
                    degraded_supply.append(
                        {"node_id": neighbor, "hop": depth + 1, "score": decayed}
                    )
                    queue.append((neighbor, depth + 1, decayed))

        all_degraded_supply_ids = list(seen)  # includes root node

        # --- Find frontline units supplied by any degraded supply node ---
        # sg_kg_edges with relation='SUPPLIED_BY': source=unit, target=supply_node
        degraded_unit_ids: set[str] = set()
        if all_degraded_supply_ids:
            placeholders = ",".join("?" * len(all_degraded_supply_ids))
            unit_rows = conn.execute(
                f"SELECT DISTINCT source_id FROM sg_kg_edges "
                f"WHERE relation='SUPPLIED_BY' AND target_id IN ({placeholders})",  # nosec B608
                all_degraded_supply_ids,
            ).fetchall()
            for r in unit_rows:
                degraded_unit_ids.add(r["source_id"])

        # Enrich unit labels from sg_orbat_units
        degraded_units = []
        if degraded_unit_ids:
            for uid in degraded_unit_ids:
                row = conn.execute(
                    "SELECT unit_name, unit_type, nation FROM sg_orbat_units WHERE id=%s",
                    (uid,),
                ).fetchone()
                if row:
                    degraded_units.append(
                        {"unit_id": uid, "unit_name": row["unit_name"],
                         "unit_type": row["unit_type"], "nation": row["nation"]}
                    )
                else:
                    degraded_units.append({"unit_id": uid, "unit_name": uid})

        # Fetch node metadata for composite scoring inputs
        node_row = conn.execute(
            "SELECT criticality, substitutability FROM sg_supply_nodes WHERE node_id=%s",
            (node_id,),
        ).fetchone()
        criticality = node_row["criticality"] if node_row else "medium"
        substitutability = node_row["substitutability"] if node_row else 0.5

        return {
            "node_id": node_id,
            "severity": severity,
            "blast_radius": len(degraded_unit_ids),
            "degraded_supply_nodes": degraded_supply,
            "degraded_units": degraded_units,
            "criticality": criticality,
            "criticality_score": CRITICALITY_SCORE.get(criticality, 0.5),
            "substitutability": substitutability,
            "substitutability_inverse": round(1.0 - substitutability, 3),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def rank_interdiction_targets(top_n: int = 10) -> dict:
    """Rank all supply nodes by composite interdiction value.

    composite_score = blast_radius × criticality_score × substitutability_inverse

    Returns:
        dict with run_id, ranked_targets (list), computed_at.
    """
    conn = get_connection()
    try:
        nodes = conn.execute(
            "SELECT node_id, label, node_type, criticality, substitutability, "
            "lat, lon, controlled_by FROM sg_supply_nodes"
        ).fetchall()
    finally:
        conn.close()

    if not nodes:
        return {"run_id": None, "ranked_targets": [], "computed_at": _now()}

    results = []
    for n in nodes:
        impact = propagate_impact_sg(n["node_id"], severity="critical")
        blast = impact["blast_radius"]
        crit_score = CRITICALITY_SCORE.get(n["criticality"], 0.5)
        sub_inv = round(1.0 - float(n["substitutability"]), 3)
        composite = round(blast * crit_score * sub_inv, 4)
        results.append({
            "node_id": n["node_id"],
            "label": n["label"],
            "node_type": n["node_type"],
            "criticality": n["criticality"],
            "substitutability": n["substitutability"],
            "lat": n["lat"],
            "lon": n["lon"],
            "controlled_by": n["controlled_by"],
            "blast_radius": blast,
            "criticality_score": crit_score,
            "substitutability_inverse": sub_inv,
            "composite_score": composite,
            "degraded_units": impact["degraded_units"],
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    top = results[:top_n]

    run_id = str(uuid.uuid4())
    now = _now()
    _persist_results(run_id, top, now)

    return {
        "run_id": run_id,
        "ranked_targets": top,
        "total_nodes_analyzed": len(results),
        "computed_at": now,
    }


def _persist_results(run_id: str, ranked: list[dict], computed_at: str) -> None:
    """Persist ranked results to sg_interdiction_results (append-only)."""
    conn = get_connection()
    try:
        for rank, item in enumerate(ranked, start=1):
            conn.execute(
                """INSERT INTO sg_interdiction_results
                   (id, run_id, node_id, rank, blast_radius,
                    criticality_score, substitutability_inverse, composite_score,
                    affected_units_json, computed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    str(uuid.uuid4()),
                    run_id,
                    item["node_id"],
                    rank,
                    item["blast_radius"],
                    item["criticality_score"],
                    item["substitutability_inverse"],
                    item["composite_score"],
                    json.dumps([u["unit_id"] for u in item.get("degraded_units", [])]),
                    computed_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_latest_ranked_targets(top_n: int = 10) -> list[dict]:
    """Return the most-recent ranked interdiction results from the DB."""
    conn = get_connection()
    try:
        run = conn.execute(
            "SELECT run_id FROM sg_interdiction_results ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        if not run:
            return []
        rows = conn.execute(
            """SELECT r.rank, r.node_id, r.blast_radius, r.criticality_score,
                      r.substitutability_inverse, r.composite_score,
                      r.affected_units_json, r.computed_at,
                      n.label, n.node_type, n.criticality, n.lat, n.lon, n.controlled_by
               FROM sg_interdiction_results r
               LEFT JOIN sg_supply_nodes n ON n.node_id = r.node_id
               WHERE r.run_id = %s
               ORDER BY r.rank
               LIMIT %s""",
            (run["run_id"], top_n),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# KG edge writing — INTERDICTS
# ---------------------------------------------------------------------------


def write_interdicts_edge(event_id: str, supply_node_id: str) -> dict:
    """Write an INTERDICTS edge to sg_kg_edges when a ConflictEvent targets a supply node.

    Ensures the supply_node_id exists as a sg_kg_nodes entry.
    Ensures the event_id exists as a sg_kg_nodes entry of type 'event'.
    """
    now = _now()
    edge_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        # Upsert event node in KG
        conn.execute(
            """INSERT OR IGNORE INTO sg_kg_nodes (node_id, node_type, label, created_at)
               VALUES (%s, %s, %s, %s)""",
            (event_id, "event", f"ConflictEvent:{event_id[:8]}", now),
        )
        # Upsert supply node in KG (may already exist)
        node_row = conn.execute(
            "SELECT label FROM sg_supply_nodes WHERE node_id=%s", (supply_node_id,)
        ).fetchone()
        if node_row:
            conn.execute(
                """INSERT OR IGNORE INTO sg_kg_nodes (node_id, node_type, label, created_at)
                   VALUES (%s, %s, %s, %s)""",
                (supply_node_id, "supply", node_row["label"], now),
            )
        # Write INTERDICTS edge
        conn.execute(
            """INSERT OR IGNORE INTO sg_kg_edges
               (id, source_id, target_id, relation, created_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (edge_id, event_id, supply_node_id, "INTERDICTS", now),
        )
        conn.commit()
        return {"edge_id": edge_id, "source_id": event_id, "target_id": supply_node_id,
                "relation": "INTERDICTS"}
    finally:
        conn.close()


def auto_write_interdicts_from_conflict_events() -> int:
    """Scan sg_conflict_events for supply-targeting events and write INTERDICTS edges.

    Matches events where metadata_json contains 'target_node_id' referencing a
    sg_supply_nodes entry.  Skips already-recorded edges.

    Returns count of new INTERDICTS edges written.
    """
    conn = get_connection()
    try:
        events = conn.execute(
            "SELECT id, metadata_json FROM sg_conflict_events "
            "WHERE metadata_json IS NOT NULL"
        ).fetchall()
    except Exception:
        return 0
    finally:
        conn.close()

    written = 0
    for ev in events:
        try:
            meta = json.loads(ev["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        target_node = meta.get("target_node_id")
        if not target_node:
            continue
        # Check supply node exists
        conn2 = get_connection()
        try:
            exists = conn2.execute(
                "SELECT 1 FROM sg_supply_nodes WHERE node_id=%s", (target_node,)
            ).fetchone()
            if not exists:
                continue
            already = conn2.execute(
                "SELECT 1 FROM sg_kg_edges WHERE source_id=%s AND target_id=%s AND relation='INTERDICTS'",
                (ev["id"], target_node),
            ).fetchone()
        finally:
            conn2.close()
        if already:
            continue
        write_interdicts_edge(ev["id"], target_node)
        written += 1
    return written


# ---------------------------------------------------------------------------
# COA runner integration
# ---------------------------------------------------------------------------


def get_supply_degradation_coefficients(top_n: int = 10) -> dict:
    """Return supply-degradation-adjusted attrition coefficients for the COA runner.

    For each frontline unit in the top-N interdiction blast radius, compute a
    degradation factor:
        degradation_factor = 1.0 - min(1.0, sum_composite / 10.0)

    where sum_composite is the total composite_score across all top-N supply
    nodes that directly degrade that unit.

    Returns:
        dict with 'coefficients': {unit_id: degradation_factor},
        'run_summary': {run_id, computed_at, top_n}
    """
    result = rank_interdiction_targets(top_n=top_n)
    targets = result.get("ranked_targets", [])

    unit_score: dict[str, float] = {}
    for target in targets:
        composite = target.get("composite_score", 0.0)
        for unit in target.get("degraded_units", []):
            uid = unit.get("unit_id", "")
            unit_score[uid] = unit_score.get(uid, 0.0) + composite

    coefficients: dict[str, float] = {}
    for uid, total_score in unit_score.items():
        coefficients[uid] = round(1.0 - min(1.0, total_score / 10.0), 4)

    return {
        "coefficients": coefficients,
        "run_summary": {
            "run_id": result.get("run_id"),
            "computed_at": result.get("computed_at"),
            "top_n": top_n,
            "units_affected": len(coefficients),
        },
    }


def get_degradation_coefficient(unit_id: str, top_n: int = 10) -> float:
    """Return the supply-degradation attrition coefficient for a single unit.

    Queries the KG for supply disruption affecting *unit_id* and returns the
    adjusted float coefficient in [0.0, 1.0].  Units not in the blast radius
    of any top-N interdiction target receive the default coefficient 1.0
    (no supply degradation).
    """
    result = get_supply_degradation_coefficients(top_n=top_n)
    return result.get("coefficients", {}).get(unit_id, 1.0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Logistics Interdiction COA Ranker (sg-sc-06)"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Ranking
    parser.add_argument("--rank", action="store_true", help="Rank all interdiction targets")
    parser.add_argument("--top-n", type=int, default=10, help="Top N targets (default 10)")

    # Impact propagation
    parser.add_argument("--propagate", metavar="NODE_ID", help="Propagate impact from node")
    parser.add_argument("--severity", choices=("critical", "high", "medium", "low"),
                        default="critical")

    # INTERDICTS edge
    parser.add_argument("--write-interdict", action="store_true",
                        help="Write INTERDICTS edge from ConflictEvent to supply node")
    parser.add_argument("--event-id", help="ConflictEvent ID")
    parser.add_argument("--node-id", help="Supply node ID")

    # Auto-scan
    parser.add_argument("--auto-interdict", action="store_true",
                        help="Auto-scan sg_conflict_events and write INTERDICTS edges")

    # COA coefficients
    parser.add_argument("--coefficients", action="store_true",
                        help="Get supply-degradation attrition coefficients")

    # Add supply node
    parser.add_argument("--add-node", action="store_true", help="Add a supply node")
    parser.add_argument("--label", help="Node label")
    parser.add_argument("--node-type", choices=NODE_TYPES, default="depot")
    parser.add_argument("--criticality",
                        choices=list(CRITICALITY_SCORE.keys()), default="medium")
    parser.add_argument("--substitutability", type=float, default=0.5)
    parser.add_argument("--controlled-by", default=None)
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lon", type=float, default=None)

    # Add supply edge
    parser.add_argument("--add-edge", action="store_true", help="Add a supply dependency edge")
    parser.add_argument("--source", help="Source node ID")
    parser.add_argument("--target", help="Target node ID")
    parser.add_argument("--edge-type", choices=EDGE_TYPES, default="SUPPLIES")
    parser.add_argument("--weight", type=float, default=1.0)

    args = parser.parse_args()

    result: Any = None

    if args.rank:
        result = rank_interdiction_targets(top_n=args.top_n)
    elif args.propagate:
        result = propagate_impact_sg(args.propagate, severity=args.severity)
    elif args.write_interdict:
        if not (args.event_id and args.node_id):
            parser.error("--write-interdict requires --event-id and --node-id")
        result = write_interdicts_edge(args.event_id, args.node_id)
    elif args.auto_interdict:
        count = auto_write_interdicts_from_conflict_events()
        result = {"interdicts_written": count}
    elif args.coefficients:
        result = get_supply_degradation_coefficients(top_n=args.top_n)
    elif args.add_node:
        if not args.label:
            parser.error("--add-node requires --label")
        result = add_supply_node(
            label=args.label,
            node_type=args.node_type,
            criticality=args.criticality,
            substitutability=args.substitutability,
            controlled_by=args.controlled_by,
            lat=args.lat,
            lon=args.lon,
        )
    elif args.add_edge:
        if not (args.source and args.target):
            parser.error("--add-edge requires --source and --target")
        result = add_supply_edge(
            source_id=args.source,
            target_id=args.target,
            edge_type=args.edge_type,
            weight=args.weight,
        )
    else:
        parser.print_help()
        sys.exit(0)

    if result is not None:
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_human(result)


def _print_human(data: Any) -> None:
    if "ranked_targets" in data:
        targets = data["ranked_targets"]
        print(f"Interdiction Priority (run {data.get('run_id','?')[:8]})")
        print(f"  Analyzed: {data.get('total_nodes_analyzed', len(targets))} nodes  "
              f"Top: {len(targets)}  At: {data.get('computed_at','')[:19]}")
        print()
        for t in targets:
            print(f"  #{t['rank']:2d}  {t['label']:<35} "
                  f"blast={t['blast_radius']:3d}  "
                  f"crit={t['criticality_score']:.2f}  "
                  f"sub_inv={t['substitutability_inverse']:.2f}  "
                  f"score={t['composite_score']:.4f}")
    elif "blast_radius" in data:
        print(f"Impact from {data['node_id'][:8]}…  severity={data['severity']}")
        print(f"  blast_radius={data['blast_radius']} units")
        print(f"  degraded supply nodes: {len(data.get('degraded_supply_nodes', []))}")
        for u in data.get("degraded_units", []):
            print(f"    {u.get('unit_name', u['unit_id'])}")
    elif "coefficients" in data:
        coefs = data["coefficients"]
        rs = data.get("run_summary", {})
        print(f"Supply Degradation Coefficients  (top-{rs.get('top_n',10)}, "
              f"{rs.get('units_affected',0)} units)")
        for uid, factor in sorted(coefs.items(), key=lambda x: x[1]):
            print(f"  {uid[:16]}  →  {factor:.4f}")
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
