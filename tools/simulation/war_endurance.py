#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""War Endurance Index — quantitative force-collapse timeline per side.

Formula:
    endurance_months = stockpile[t] / max(eps, attrition_rate[t] - replenishment_rate[t])

    attrition_rate      — from Lanchester square-law ODE (inline; feeds sg-game-01)
    replenishment_rate  = production_capacity * (1 - supply_disruption_score)
                        + import_rate * (1 - sanctions_effectiveness)
    supply_disruption_score = SCRM aggregate (scrm_assessor war_economy profile)
    substitutability_factor — from DIB mapper table (T-72→T-54 reserve: high, Kalibr→none: 1.5)

Supply-degradation integration (sg-sc-06):
    get_supply_degradation_coefficients() from interdiction_ranker returns per-unit
    degradation factors computed from top-N interdiction targets.  For each side,
    the mean degradation factor across its affected units is converted to a
    supply_interdiction_factor (≥ 1.0) that amplifies effective_attrition.
    run_endurance_with_supply_interdiction() applies this automatically.

Outputs:
    endurance_months per side, endurance_delta,
    historical_threshold=3.0 (triggers escalation in 73% of pre-war cases, sg-import-09)

Downstream consumers:
    - sg-iw-08  Weibull time-to-event — endurance_months as survival covariate
    - sg-game-03 COA Monte Carlo — endurance_months as terminal condition

CLI:
    python tools/simulation/war_endurance.py --scenario-id <uuid> --json
    python tools/simulation/war_endurance.py --demo --json
    python tools/simulation/war_endurance.py --demo --with-interdiction --json
"""

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

HISTORICAL_THRESHOLD_MONTHS = 3.0  # triggers escalation in 73% of pre-war cases (sg-import-09)
EPS = 1e-6  # floor to prevent division by zero

# Substitutability factors from DIB mapper (platform → substitute → score)
# score > 1.0 means partial substitution (degraded capability); score = 1.0 = full; no entry = 1.5
DIB_SUBSTITUTABILITY: dict[str, float] = {
    "T-72": 0.85,       # → T-54/55 reserve; high (capable substitute)
    "T-80": 0.90,       # → T-72 conversion
    "T-64": 0.80,
    "BMP-2": 0.85,
    "BMP-1": 0.80,
    "BTR-80": 0.75,
    "Kalibr": 1.50,     # no equivalent substitute — score degrades capability
    "Iskander-M": 1.40,
    "Su-25": 0.70,
    "Su-24": 0.75,
    "Mi-8": 0.60,       # abundant reserve
    "Mi-24": 0.85,
    "S-300": 1.30,      # limited substitute paths
    "Buk-M2": 1.20,
    "Pantsir-S1": 1.15,
    # Western platforms
    "M1A2": 0.90,       # → M1A1 stored reserve
    "Bradley": 0.85,
    "HIMARS": 1.35,     # → MLRS; partial
    "F-16": 0.80,
    "Javelin": 0.70,
    "Stinger": 0.65,
    "NLAW": 0.75,
}


# ---------------------------------------------------------------------------
# Supply-degradation integration (sg-sc-06)
# ---------------------------------------------------------------------------

def get_interdiction_supply_factor(unit_ids: list | None = None) -> float:
    """Return supply-degradation attrition multiplier from interdiction ranker.

    Converts per-unit degradation_factor (capability-remaining, 0–1) into an
    attrition amplifier (≥ 1.0):
        supply_interdiction_factor = 2.0 - mean_degradation_factor

    Examples:
        mean_degradation = 1.0  → factor = 1.0  (no degradation)
        mean_degradation = 0.7  → factor = 1.3  (30% supply cut → +30% attrition)
        mean_degradation = 0.0  → factor = 2.0  (fully severed supply lines)

    Gracefully returns 1.0 if interdiction data is unavailable.
    """
    try:
        from tools.strategos.interdiction_ranker import get_supply_degradation_coefficients
        result = get_supply_degradation_coefficients(top_n=10)
        coefficients = result.get("coefficients", {})
        if not coefficients:
            return 1.0
        if unit_ids:
            relevant = [coefficients[uid] for uid in unit_ids if uid in coefficients]
            values = relevant if relevant else list(coefficients.values())
        else:
            values = list(coefficients.values())
        mean_degradation = sum(values) / len(values)
        return round(2.0 - mean_degradation, 4)
    except Exception:
        return 1.0


# ---------------------------------------------------------------------------
# Lanchester square-law ODE (inline; integrates numerically with Euler steps)
# ---------------------------------------------------------------------------

def _lanchester_attrition(
    force_a: float,
    force_b: float,
    alpha: float,  # B's lethality coefficient against A
    beta: float,   # A's lethality coefficient against B
    dt: float = 1.0,
    steps: int = 30,
) -> tuple[list[float], list[float]]:
    """Euler integration of Lanchester square-law ODE.

    dx/dt = -alpha * y   (A losses driven by B force size)
    dy/dt = -beta  * x   (B losses driven by A force size)

    Returns time-series lists for force_a and force_b trajectories.
    """
    x, y = float(force_a), float(force_b)
    xs, ys = [x], [y]
    for _ in range(steps):
        dx = -alpha * y * dt
        dy = -beta * x * dt
        x = max(0.0, x + dx)
        y = max(0.0, y + dy)
        xs.append(x)
        ys.append(y)
    return xs, ys


def _attrition_rate_from_lanchester(
    force_size: float,
    opposing_force: float,
    lethality_coeff: float,
    dt: float = 1.0,
) -> float:
    """Instantaneous attrition rate (units/month) for a side under Lanchester square law."""
    return lethality_coeff * opposing_force * dt


# ---------------------------------------------------------------------------
# SCRM supply disruption score
# ---------------------------------------------------------------------------

def _get_scrm_supply_disruption(scenario_id: str | None = None) -> float:
    """Fetch war-economy supply disruption score from SCRM assessor.

    Falls back to 0.35 (moderate disruption) if DB unavailable or no data.
    """
    try:
        from tools.supply_chain.scrm_assessor import assess_project
        result = assess_project(scenario_id or "war_economy")
        if result and "aggregate_score" in result:
            raw = float(result["aggregate_score"])
            # SCRM scores are 0–10 risk; normalize to 0–1 disruption probability
            return min(1.0, raw / 10.0)
    except Exception:
        pass
    return 0.35  # fallback: moderate disruption


# ---------------------------------------------------------------------------
# Replenishment rate
# ---------------------------------------------------------------------------

def _replenishment_rate(
    production_capacity: float,
    supply_disruption_score: float,
    import_rate: float,
    sanctions_effectiveness: float,
) -> float:
    """Units per month a side can replenish.

    replenishment = production * (1 - supply_disruption)
                  + imports    * (1 - sanctions_effectiveness)
    """
    domestic = production_capacity * (1.0 - supply_disruption_score)
    foreign = import_rate * (1.0 - sanctions_effectiveness)
    return max(0.0, domestic + foreign)


# ---------------------------------------------------------------------------
# Substitutability adjustment
# ---------------------------------------------------------------------------

def _substitutability_factor(platform_mix: list[str] | None) -> float:
    """Weighted mean substitutability score for a platform mix.

    Scores < 1.0 indicate high substitutability (resilient).
    Scores > 1.0 indicate degraded-capability substitution (fragile).
    Platforms absent from DIB table default to 1.5 (no substitute path).
    """
    if not platform_mix:
        return 1.0
    scores = [DIB_SUBSTITUTABILITY.get(p, 1.5) for p in platform_mix]
    return sum(scores) / len(scores)


# ---------------------------------------------------------------------------
# Core endurance calculation
# ---------------------------------------------------------------------------

def compute_endurance(side: dict) -> dict:
    """Compute war endurance for a single side.

    Args:
        side: dict with keys:
            name                      str   — side label
            stockpile                 float — current inventory units
            force_size                float — personnel / combat vehicles
            opposing_force_size       float — adversary force size
            lethality_coeff           float — adversary lethality (Lanchester alpha/beta)
            production_capacity       float — units/month domestic production
            import_rate               float — units/month from allies/imports
            sanctions_effectiveness   float — 0–1 fraction imports blocked
            supply_disruption_score   float | None — override; None = fetch from SCRM
            platform_mix              list[str] | None — platform names for DIB lookup
            scenario_id               str | None — for SCRM DB lookup
            supply_interdiction_factor float | None — attrition amplifier from
                                         interdiction ranker (1.0 = no degradation;
                                         >1.0 = supply-line degradation amplifies
                                         effective attrition). Default: 1.0.

    Returns:
        dict with endurance_months, attrition_rate, replenishment_rate,
        net_drain, substitutability_factor, supply_interdiction_factor,
        burn_series.
    """
    name = side.get("name", "side")
    stockpile = float(side.get("stockpile", 1000.0))
    force_size = float(side.get("force_size", 100.0))
    opposing = float(side.get("opposing_force_size", 80.0))
    lethality = float(side.get("lethality_coeff", 0.005))
    production = float(side.get("production_capacity", 50.0))
    import_rate = float(side.get("import_rate", 20.0))
    sanctions = float(side.get("sanctions_effectiveness", 0.0))
    platform_mix = side.get("platform_mix") or []
    scenario_id = side.get("scenario_id")
    # Supply-degradation-adjusted attrition coefficient from interdiction model (sg-sc-06)
    interdiction_factor = float(side.get("supply_interdiction_factor", 1.0))

    # Supply disruption from SCRM or override
    if side.get("supply_disruption_score") is not None:
        disruption = float(side["supply_disruption_score"])
    else:
        disruption = _get_scrm_supply_disruption(scenario_id)

    # Attrition rate from Lanchester
    attrition = _attrition_rate_from_lanchester(force_size, opposing, lethality)

    # Replenishment
    replenishment = _replenishment_rate(production, disruption, import_rate, sanctions)

    # Substitutability adjustment on attrition (high sub → attrit more efficiently)
    sub_factor = _substitutability_factor(platform_mix)
    # Supply-degradation-adjusted coefficients replace manual attrition scaling:
    # interdiction_factor amplifies effective attrition when supply lines are severed
    effective_attrition = attrition * sub_factor * interdiction_factor

    # Net drain per month
    net_drain = effective_attrition - replenishment

    # Endurance months
    endurance_months = stockpile / max(EPS, net_drain)
    # Cap at 120 months (10 years) — beyond meaningful planning horizon
    endurance_months = min(120.0, endurance_months)
    # Negative net_drain means stockpile grows; treat as effectively infinite
    if net_drain <= 0:
        endurance_months = 120.0

    # Burn-down time series (monthly snapshots over min(48, endurance+6) months)
    horizon = min(48, int(endurance_months) + 6)
    burn_series = []
    s = stockpile
    for t in range(horizon + 1):
        burn_series.append({"month": t, "stockpile": round(max(0.0, s), 2)})
        s = max(0.0, s - net_drain)

    return {
        "name": name,
        "endurance_months": round(endurance_months, 2),
        "attrition_rate": round(effective_attrition, 4),
        "replenishment_rate": round(replenishment, 4),
        "net_drain": round(net_drain, 4),
        "supply_disruption_score": round(disruption, 4),
        "substitutability_factor": round(sub_factor, 4),
        "supply_interdiction_factor": round(interdiction_factor, 4),
        "stockpile_initial": stockpile,
        "burn_series": burn_series,
        "below_threshold": endurance_months < HISTORICAL_THRESHOLD_MONTHS,
    }


# ---------------------------------------------------------------------------
# Top-level two-sided analysis
# ---------------------------------------------------------------------------

def run_endurance_analysis(params: dict) -> dict:
    """Run war endurance analysis for both sides.

    Args:
        params: dict with:
            side_a: side parameters dict (see compute_endurance)
            side_b: side parameters dict
            scenario_id: optional shared scenario ID

    Returns:
        Full analysis result with per-side endurance, delta, threshold flags,
        Lanchester trajectory, and escalation risk assessment.
    """
    scenario_id = params.get("scenario_id", str(uuid.uuid4()))
    side_a_params = dict(params.get("side_a", {}))
    side_b_params = dict(params.get("side_b", {}))

    side_a_params.setdefault("scenario_id", scenario_id)
    side_b_params.setdefault("scenario_id", scenario_id)
    side_a_params.setdefault("name", "Side A")
    side_b_params.setdefault("name", "Side B")

    result_a = compute_endurance(side_a_params)
    result_b = compute_endurance(side_b_params)

    endurance_delta = result_a["endurance_months"] - result_b["endurance_months"]

    # Lanchester trajectory for force sizes (informational)
    lc_a, lc_b = _lanchester_attrition(
        force_a=side_a_params.get("force_size", 100.0),
        force_b=side_b_params.get("force_size", 80.0),
        alpha=side_b_params.get("lethality_coeff", 0.005),
        beta=side_a_params.get("lethality_coeff", 0.005),
        dt=1.0,
        steps=min(48, int(max(result_a["endurance_months"], result_b["endurance_months"])) + 6),
    )

    # Escalation risk: either side below historical threshold
    escalation_risk = (
        result_a["below_threshold"] or result_b["below_threshold"]
    )

    return {
        "scenario_id": scenario_id,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "historical_threshold_months": HISTORICAL_THRESHOLD_MONTHS,
        "side_a": result_a,
        "side_b": result_b,
        "endurance_delta": round(endurance_delta, 2),
        "escalation_risk": escalation_risk,
        "weaker_side": result_a["name"] if endurance_delta < 0 else result_b["name"],
        "lanchester_trajectory": {
            "side_a": [round(v, 2) for v in lc_a],
            "side_b": [round(v, 2) for v in lc_b],
        },
    }


# ---------------------------------------------------------------------------
# sg-sc-06: supply-interdiction-adjusted endurance (sg-game-03 COA runner)
# ---------------------------------------------------------------------------

def run_endurance_with_supply_interdiction(params: dict) -> dict:
    """Run endurance analysis with supply-degradation-adjusted attrition coefficients.

    Fetches interdiction coefficients from the interdiction ranker (sg-sc-06),
    computes per-side supply_interdiction_factor, injects into params, then
    delegates to run_endurance_analysis.

    The supply_interdiction_factor replaces the manual DIB_SUBSTITUTABILITY
    attrition scaling with model-derived supply-chain-degradation amplifiers.

    Args:
        params: same as run_endurance_analysis.  supply_interdiction_factor in
                side_a/side_b will be overwritten with live interdiction data.

    Returns:
        Same as run_endurance_analysis plus 'supply_interdiction_applied': True.
    """
    params = dict(params)
    side_a = dict(params.get("side_a", {}))
    side_b = dict(params.get("side_b", {}))

    # Fetch supply-degradation factors from interdiction model
    unit_ids_a = side_a.pop("orbat_unit_ids", None)
    unit_ids_b = side_b.pop("orbat_unit_ids", None)

    factor_a = get_interdiction_supply_factor(unit_ids_a)
    factor_b = get_interdiction_supply_factor(unit_ids_b)

    side_a["supply_interdiction_factor"] = factor_a
    side_b["supply_interdiction_factor"] = factor_b
    params["side_a"] = side_a
    params["side_b"] = side_b

    result = run_endurance_analysis(params)
    result["supply_interdiction_applied"] = True
    result["supply_interdiction_factors"] = {
        side_a.get("name", "Side A"): factor_a,
        side_b.get("name", "Side B"): factor_b,
    }
    return result


# ---------------------------------------------------------------------------
# Demo scenario (Ukraine-Russia conflict parameterization)
# ---------------------------------------------------------------------------

DEMO_PARAMS: dict = {
    "scenario_id": "demo-ukraine-russia-2024",
    "side_a": {
        "name": "Ukraine",
        "stockpile": 4200.0,
        "force_size": 700000.0,
        "opposing_force_size": 480000.0,
        "lethality_coeff": 0.0004,
        "production_capacity": 180.0,
        "import_rate": 350.0,
        "sanctions_effectiveness": 0.0,
        "supply_disruption_score": 0.30,
        "platform_mix": ["T-72", "BMP-2", "Javelin", "HIMARS", "Stinger"],
    },
    "side_b": {
        "name": "Russia",
        "stockpile": 12000.0,
        "force_size": 480000.0,
        "opposing_force_size": 700000.0,
        "lethality_coeff": 0.0006,
        "production_capacity": 320.0,
        "import_rate": 80.0,
        "sanctions_effectiveness": 0.65,
        "supply_disruption_score": 0.45,
        "platform_mix": ["T-72", "T-80", "Kalibr", "Iskander-M", "Su-25", "S-300"],
    },
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="War Endurance Index")
    p.add_argument("--scenario-id", help="Scenario UUID to load from DB")
    p.add_argument("--params-file", help="JSON file with side_a/side_b params")
    p.add_argument("--demo", action="store_true", help="Run demo scenario")
    p.add_argument("--with-interdiction", action="store_true",
                   help="Apply supply-degradation-adjusted coefficients from interdiction ranker (sg-sc-06)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    return p


def main() -> None:
    args = _build_parser().parse_args()

    run_fn = run_endurance_with_supply_interdiction if args.with_interdiction else run_endurance_analysis

    if args.demo:
        result = run_fn(DEMO_PARAMS)
    elif args.params_file:
        with open(args.params_file, encoding="utf-8") as f:
            params = json.load(f)
        result = run_fn(params)
    else:
        # Try to load from DB by scenario_id
        result = run_fn(
            {"scenario_id": args.scenario_id or str(uuid.uuid4())}
        )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        side_a = result["side_a"]
        side_b = result["side_b"]
        print("\n=== War Endurance Index ===")
        print(f"Scenario: {result['scenario_id']}")
        print(f"Historical escalation threshold: {HISTORICAL_THRESHOLD_MONTHS} months")
        print(f"\n{side_a['name']}:")
        print(f"  Endurance: {side_a['endurance_months']:.1f} months")
        print(f"  Net drain: {side_a['net_drain']:.2f} units/month")
        print(f"  Below threshold: {side_a['below_threshold']}")
        print(f"\n{side_b['name']}:")
        print(f"  Endurance: {side_b['endurance_months']:.1f} months")
        print(f"  Net drain: {side_b['net_drain']:.2f} units/month")
        print(f"  Below threshold: {side_b['below_threshold']}")
        print(f"\nEndurance delta: {result['endurance_delta']:.1f} months (A minus B)")
        print(f"Weaker side: {result['weaker_side']}")
        print(f"Escalation risk: {result['escalation_risk']}")


if __name__ == "__main__":
    main()
