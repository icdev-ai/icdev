#!/usr/bin/env python3
# CUI // SP-CTI
"""COA Simulation Runner — Lanchester attrition with supply-degradation coefficients.

Replaces hardcoded beta/rho attrition coefficients with dynamic per-unit values
from interdiction_ranker.get_degradation_coefficient(unit_id), which queries the
KG for supply disruption and returns an adjusted float in [0.0, 1.0].
"""

from __future__ import annotations

from typing import Any

from tools.strategos.interdiction_ranker import get_degradation_coefficient
from tools.strategos.ooda import lanchester_linear, lanchester_square

_ATTRITION_FALLBACK = 0.01  # used only when unit strength is zero


def _mean_coefficient(units: list[dict], top_n: int) -> float:
    """Return average supply-degradation coefficient across a force's units."""
    if not units:
        return _ATTRITION_FALLBACK
    total = sum(
        get_degradation_coefficient(u["unit_id"], top_n=top_n) for u in units
    )
    return total / len(units)


def run_coa_attrition(
    blue_units: list[dict],
    red_units: list[dict],
    model: str = "square",
    dt: float = 1.0,
    max_steps: int = 500,
    top_n: int = 10,
) -> dict[str, Any]:
    """Run Lanchester attrition using supply-degradation coefficients per unit.

    Args:
        blue_units: list of {"unit_id": str, "strength": float}
        red_units:  list of {"unit_id": str, "strength": float}
        model:      "square" (default) or "linear"
        dt:         time step per iteration
        max_steps:  simulation upper bound
        top_n:      number of top interdiction targets to consider for KG query

    Returns:
        Lanchester result dict extended with per-side attrition coefficients and
        a "coefficient_source" field confirming dynamic KG-based derivation.
    """
    blue_coeff = _mean_coefficient(blue_units, top_n)
    red_coeff = _mean_coefficient(red_units, top_n)

    b0 = sum(float(u.get("strength", 100)) for u in blue_units) or 100.0
    r0 = sum(float(u.get("strength", 100)) for u in red_units) or 100.0

    fn = lanchester_linear if model == "linear" else lanchester_square
    result = fn(b0, r0, beta=blue_coeff, rho=red_coeff, dt=dt, max_steps=max_steps)

    result["blue_attrition_coefficient"] = round(blue_coeff, 4)
    result["red_attrition_coefficient"] = round(red_coeff, 4)
    result["coefficient_source"] = "supply_degradation_kg"
    return result
