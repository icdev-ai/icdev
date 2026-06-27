#!/usr/bin/env python3
# CUI // SP-CTI
"""Targeting Package Optimizer — sg-sc-13a.

Greedy hill-climb + 1-opt local search for optimal interdiction package selection.

Objectives:
    maximize_blast_radius    — maximize unique frontline units degraded
    minimize_time_to_effect  — minimize P50 lag days across selected nodes
    maximize_endurance_delta — maximize adversary endurance reduction (months)

Synergy:
    synergy(A, B) = |units(A) ∪ units(B)| − |units(A)| − |units(B)|
    Positive synergy = complementary targets (different unit sets).
    Negative synergy = redundant overlap.

Algorithm:
    1. Pre-compute blast profiles (propagate_impact_sg) for all candidates
    2. Greedy: seed highest composite-score node; add synergy-adjusted best each step
    3. 1-opt: sweep all single-swap alternatives; accept improving swaps

CLI:
    python tools/strategos/targeting_package_optimizer.py --budget 5 --objective maximize_blast_radius --json
    python tools/strategos/targeting_package_optimizer.py --budget 3 --objective minimize_time_to_effect --theater Russia
    python tools/strategos/targeting_package_optimizer.py --budget 4 --objective maximize_endurance_delta --json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.strategos.interdiction_ranker import propagate_impact_sg  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OBJECTIVES = (
    "maximize_blast_radius",
    "minimize_time_to_effect",
    "maximize_endurance_delta",
)

CRITICALITY_SCORE: dict[str, float] = {
    "critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25,
}

# P50 lag (days) per supply node type — derived from NODE_DISRUPTION_PROFILES
_LAG_MEAN: dict[str, float] = {
    "depot":         2.0,
    "rail_hub":      7.0,
    "port":          7.0,
    "fuel_dump":     2.0,
    "ammo_cache":    2.0,
    "bridge":        5.0,
    "road_junction": 4.0,
    "airfield":      3.0,
    "pipeline":      14.0,
    "command_post":  1.0,
    "warehouse":     7.0,
    "raw_material":  30.0,
    "factory":       14.0,
    "unit":          1.0,
}
_DEFAULT_LAG = 7.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# OptimalPackage dataclass
# ---------------------------------------------------------------------------

@dataclass
class OptimalPackage:
    nodes: list[dict] = field(default_factory=list)
    combined_blast_radius: int = 0
    blast_pct: float = 0.0
    time_to_effect_p50_days: float = 0.0
    endurance_delta_months: float = 0.0
    marginal_value_per_node: dict[str, float] = field(default_factory=dict)
    synergy_pairs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_candidates(theater: str | None = None) -> list[dict]:
    """Load supply node candidates, optionally filtered by controlled_by == theater."""
    conn = get_connection()
    try:
        if theater:
            rows = conn.execute(
                "SELECT node_id, label, node_type, criticality, substitutability, "
                "controlled_by, lat, lon FROM sg_supply_nodes WHERE controlled_by = %s",
                (theater,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT node_id, label, node_type, criticality, substitutability, "
                "controlled_by, lat, lon FROM sg_supply_nodes"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _total_orbat_units() -> int:
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM sg_orbat_units").fetchone()
        return max(1, int(row["c"])) if row else 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Blast profile pre-computation
# ---------------------------------------------------------------------------

def _build_blast_profiles(candidates: list[dict]) -> dict[str, dict]:
    """Pre-compute blast impact for each candidate node.

    Returns: node_id → {blast_radius, degraded_unit_ids, lag_mean, composite_score}
    """
    profiles: dict[str, dict] = {}
    for node in candidates:
        nid = node["node_id"]
        impact = propagate_impact_sg(nid, severity="critical")
        unit_ids: set[str] = {u["unit_id"] for u in impact.get("degraded_units", [])}
        lag = _LAG_MEAN.get(node["node_type"], _DEFAULT_LAG)
        crit = CRITICALITY_SCORE.get(node["criticality"], 0.5)
        sub_inv = round(1.0 - float(node.get("substitutability", 0.5)), 3)
        composite = round(impact["blast_radius"] * crit * sub_inv, 4)
        profiles[nid] = {
            "blast_radius": impact["blast_radius"],
            "degraded_unit_ids": unit_ids,
            "lag_mean": lag,
            "composite_score": composite,
        }
    return profiles


# ---------------------------------------------------------------------------
# Synergy
# ---------------------------------------------------------------------------

def _synergy(a_units: set[str], b_units: set[str]) -> float:
    """delta_blast(A+B) - delta_blast(A) - delta_blast(B).

    Positive = complementary (hit disjoint unit sets).
    Negative = redundant (overlapping blast radii).
    """
    return float(len(a_units | b_units) - len(a_units) - len(b_units))


def _compute_synergy_pairs(
    selected_ids: list[str],
    profiles: dict[str, dict],
) -> list[dict]:
    """All pairwise synergy scores for nodes in the package."""
    pairs = []
    ids = list(selected_ids)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            syn = _synergy(profiles[a]["degraded_unit_ids"], profiles[b]["degraded_unit_ids"])
            pairs.append({"node_a": a, "node_b": b, "synergy_score": round(syn, 4)})
    return pairs


# ---------------------------------------------------------------------------
# Package metrics
# ---------------------------------------------------------------------------

def _package_blast_radius(selected_ids: list[str], profiles: dict[str, dict]) -> int:
    combined: set[str] = set()
    for nid in selected_ids:
        combined |= profiles[nid]["degraded_unit_ids"]
    return len(combined)


def _package_p50_lag(selected_ids: list[str], profiles: dict[str, dict]) -> float:
    if not selected_ids:
        return 0.0
    return float(median([profiles[nid]["lag_mean"] for nid in selected_ids]))


def _derive_interdiction_factor(selected_ids: list[str], profiles: dict[str, dict]) -> float:
    """Convert package blast profiles to supply_interdiction_factor (≥ 1.0)."""
    unit_score: dict[str, float] = {}
    for nid in selected_ids:
        p = profiles[nid]
        for uid in p["degraded_unit_ids"]:
            unit_score[uid] = unit_score.get(uid, 0.0) + p["composite_score"]
    if not unit_score:
        return 1.0
    coefficients = [round(1.0 - min(1.0, v / 10.0), 4) for v in unit_score.values()]
    mean_deg = sum(coefficients) / len(coefficients)
    return round(2.0 - mean_deg, 4)


def _package_endurance_delta(selected_ids: list[str], profiles: dict[str, dict]) -> float:
    """Adversary endurance reduction (months) from interdicting the selected package."""
    try:
        from tools.simulation.war_endurance import compute_endurance
    except ImportError:
        return 0.0

    _default_side = {
        "name": "adversary",
        "stockpile": 1000.0,
        "force_size": 100.0,
        "opposing_force_size": 80.0,
        "lethality_coeff": 0.005,
        "production_capacity": 50.0,
        "import_rate": 20.0,
        "sanctions_effectiveness": 0.0,
        "supply_disruption_score": 0.35,
    }
    baseline = compute_endurance({**_default_side, "supply_interdiction_factor": 1.0})
    if_factor = _derive_interdiction_factor(selected_ids, profiles)
    post = compute_endurance({**_default_side, "supply_interdiction_factor": if_factor})
    return round(baseline["endurance_months"] - post["endurance_months"], 4)


# ---------------------------------------------------------------------------
# Objective scoring (higher = better for all objectives)
# ---------------------------------------------------------------------------

def _score_package(
    selected_ids: list[str],
    profiles: dict[str, dict],
    objective: str,
) -> float:
    if objective == "maximize_blast_radius":
        return float(_package_blast_radius(selected_ids, profiles))
    if objective == "minimize_time_to_effect":
        p50 = _package_p50_lag(selected_ids, profiles)
        return -p50
    if objective == "maximize_endurance_delta":
        return _package_endurance_delta(selected_ids, profiles)
    raise ValueError(f"Unknown objective: {objective}")


# ---------------------------------------------------------------------------
# Marginal value
# ---------------------------------------------------------------------------

def _marginal_value(
    nid: str,
    current_ids: list[str],
    profiles: dict[str, dict],
    objective: str,
) -> float:
    """Incremental objective gain from adding nid to current_ids."""
    if objective == "maximize_blast_radius":
        existing: set[str] = set()
        for cid in current_ids:
            existing |= profiles[cid]["degraded_unit_ids"]
        return float(len(profiles[nid]["degraded_unit_ids"] - existing))

    if objective == "minimize_time_to_effect":
        before = _package_p50_lag(current_ids, profiles)
        after = _package_p50_lag(current_ids + [nid], profiles)
        return before - after

    if objective == "maximize_endurance_delta":
        before = _package_endurance_delta(current_ids, profiles)
        after = _package_endurance_delta(current_ids + [nid], profiles)
        return after - before

    raise ValueError(f"Unknown objective: {objective}")


def _synergy_adjusted_value(
    nid: str,
    current_ids: list[str],
    profiles: dict[str, dict],
    objective: str,
) -> float:
    """Marginal value with a synergy bonus (5% weight, blast-overlap tie-breaker)."""
    mv = _marginal_value(nid, current_ids, profiles, objective)
    if not current_ids or objective != "maximize_blast_radius":
        return mv
    syn_vals = [
        _synergy(profiles[cid]["degraded_unit_ids"], profiles[nid]["degraded_unit_ids"])
        for cid in current_ids
    ]
    avg_syn = sum(syn_vals) / len(syn_vals)
    return mv + 0.05 * avg_syn


# ---------------------------------------------------------------------------
# Greedy hill-climb
# ---------------------------------------------------------------------------

def greedy_select(
    candidates: list[dict],
    profiles: dict[str, dict],
    budget: int,
    objective: str,
) -> list[str]:
    """Seed highest composite-score node; greedily add synergy-adjusted best until budget."""
    if not candidates:
        return []
    remaining = sorted(
        [c["node_id"] for c in candidates],
        key=lambda nid: profiles[nid]["composite_score"],
        reverse=True,
    )
    selected: list[str] = [remaining[0]]
    remaining = remaining[1:]

    while remaining and len(selected) < budget:
        best = max(
            remaining,
            key=lambda nid: _synergy_adjusted_value(nid, selected, profiles, objective),
        )
        selected.append(best)
        remaining.remove(best)

    return selected


# ---------------------------------------------------------------------------
# 1-opt local search
# ---------------------------------------------------------------------------

def one_opt(
    selected_ids: list[str],
    all_node_ids: list[str],
    profiles: dict[str, dict],
    objective: str,
) -> list[str]:
    """Single-swap local search: iterate until no improving swap exists."""
    non_selected = [nid for nid in all_node_ids if nid not in selected_ids]
    current = list(selected_ids)
    improved = True

    while improved:
        improved = False
        current_score = _score_package(current, profiles, objective)
        for i, out_node in enumerate(current):
            for in_node in non_selected:
                candidate = current[:i] + [in_node] + current[i + 1:]
                if _score_package(candidate, profiles, objective) > current_score + 1e-9:
                    current = candidate
                    non_selected = [nid for nid in all_node_ids if nid not in current]
                    improved = True
                    break
            if improved:
                break

    return current


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def optimize_package(
    budget: int,
    objective: str,
    theater: str | None = None,
) -> OptimalPackage:
    """Run targeting package optimization.

    Args:
        budget:    Maximum number of nodes in the package.
        objective: Optimization objective (see OBJECTIVES).
        theater:   Optional controlled_by filter (adversary/nation).

    Returns:
        OptimalPackage with selected nodes and computed metrics.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {OBJECTIVES}")

    candidates = _load_candidates(theater)
    if not candidates:
        return OptimalPackage()

    profiles = _build_blast_profiles(candidates)
    selected_ids = greedy_select(candidates, profiles, budget, objective)
    all_ids = [c["node_id"] for c in candidates]
    selected_ids = one_opt(selected_ids, all_ids, profiles, objective)

    total_units = _total_orbat_units()
    combined_blast = _package_blast_radius(selected_ids, profiles)
    blast_pct = round(combined_blast / total_units * 100, 2)
    p50_lag = round(_package_p50_lag(selected_ids, profiles), 2)
    endurance_delta = round(_package_endurance_delta(selected_ids, profiles), 4)

    marginal_values: dict[str, float] = {}
    for nid in selected_ids:
        others = [s for s in selected_ids if s != nid]
        marginal_values[nid] = round(_marginal_value(nid, others, profiles, objective), 4)

    synergy_pairs = _compute_synergy_pairs(selected_ids, profiles)
    node_map = {c["node_id"]: c for c in candidates}
    nodes = [node_map[nid] for nid in selected_ids if nid in node_map]

    return OptimalPackage(
        nodes=nodes,
        combined_blast_radius=combined_blast,
        blast_pct=blast_pct,
        time_to_effect_p50_days=p50_lag,
        endurance_delta_months=endurance_delta,
        marginal_value_per_node=marginal_values,
        synergy_pairs=synergy_pairs,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Targeting Package Optimizer (sg-sc-13a)"
    )
    parser.add_argument(
        "--budget", type=int, default=5,
        help="Max nodes in the targeting package (default: 5)"
    )
    parser.add_argument(
        "--objective", choices=OBJECTIVES, default="maximize_blast_radius",
        help="Optimization objective (default: maximize_blast_radius)"
    )
    parser.add_argument(
        "--theater", default=None,
        help="Filter candidates by controlled_by (e.g. Russia)"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    import logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    pkg = optimize_package(
        budget=args.budget,
        objective=args.objective,
        theater=args.theater,
    )
    result = pkg.to_dict()
    result["computed_at"] = _now()
    result["params"] = {
        "budget": args.budget,
        "objective": args.objective,
        "theater": args.theater,
    }

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(pkg, args)


def _print_human(pkg: OptimalPackage, args: argparse.Namespace) -> None:
    print(f"Targeting Package Optimizer  [{args.objective}]")
    print(f"  Budget: {args.budget}  Theater: {args.theater or 'all'}")
    print(f"  Nodes selected: {len(pkg.nodes)}")
    print(f"  Combined blast radius:  {pkg.combined_blast_radius} units ({pkg.blast_pct:.1f}%)")
    print(f"  Time to effect P50:     {pkg.time_to_effect_p50_days:.1f} days")
    print(f"  Endurance delta:        {pkg.endurance_delta_months:.2f} months")
    print()
    print("  Selected nodes:")
    for node in pkg.nodes:
        nid = node["node_id"]
        mv = pkg.marginal_value_per_node.get(nid, 0.0)
        print(
            f"    {node.get('label', nid):<40} "
            f"type={node.get('node_type', '?'):<14} "
            f"crit={node.get('criticality', '?'):<9} "
            f"mv={mv:.4f}"
        )
    if pkg.synergy_pairs:
        print()
        print("  Synergy pairs:")
        for sp in sorted(pkg.synergy_pairs, key=lambda x: x["synergy_score"], reverse=True):
            print(
                f"    {sp['node_a'][:8]}↔{sp['node_b'][:8]}  "
                f"syn={sp['synergy_score']:.2f}"
            )


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
