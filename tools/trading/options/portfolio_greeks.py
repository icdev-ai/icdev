# CUI // SP-CTI
"""FathomDesk Phase 7.8 — Portfolio net Greeks aggregator.

Sums per-contract Greeks across a user's open option positions.
Each Greek is multiplied by 100 × signed qty (qty sign encodes long/short).
Positions without a cached last_greeks_json contribute zero and are counted
in stale_count so callers know how much of the book is estimated vs. fresh.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

_TARGETS_PATH = Path(__file__).parents[3] / "args" / "greek_targets.yaml"

_DEFAULTS: dict = {
    "delta_target": 0.0,
    "delta_tolerance": 0.10,
    "vega_max_per_10k": 0.05,
    "theta_floor": -0.02,
}


def load_greek_targets(path: Path | None = None) -> dict:
    """Load greek target thresholds from YAML, falling back to _DEFAULTS."""
    p = path if path is not None else _TARGETS_PATH
    try:
        with open(p, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
        return {**_DEFAULTS, **data}
    except (FileNotFoundError, yaml.YAMLError, OSError):
        return dict(_DEFAULTS)


def compute_portfolio_greeks(
    user_id: str = "default",
    conn=None,
) -> dict:
    """Aggregate Greeks across all open option positions for *user_id*.

    Returns:
        net_delta, net_gamma, net_theta, net_vega,
        net_vanna, net_charm, net_volga,
        position_count, stale_count, as_of
    """
    if conn is None:
        from tools.trading.db import get_conn
        conn = get_conn()

    rows = conn.execute(
        "SELECT qty, last_greeks_json FROM ad_sandbox_option_positions"
        " WHERE user_id = %s AND qty != 0",
        (user_id,),
    ).fetchall()

    totals = {
        "net_delta": 0.0,
        "net_gamma": 0.0,
        "net_theta": 0.0,
        "net_vega": 0.0,
        "net_vanna": 0.0,
        "net_charm": 0.0,
        "net_volga": 0.0,
    }
    stale_count = 0

    for row in rows:
        qty = float(row[0] if not hasattr(row, "keys") else row["qty"])
        greeks_raw = row[1] if not hasattr(row, "keys") else row["last_greeks_json"]

        greeks: dict = {}
        if greeks_raw:
            try:
                greeks = json.loads(greeks_raw)
            except (json.JSONDecodeError, TypeError):
                greeks = {}

        if not greeks:
            stale_count += 1
            continue

        mult = 100.0 * qty
        totals["net_delta"] += greeks.get("delta", 0.0) * mult
        totals["net_gamma"] += greeks.get("gamma", 0.0) * mult
        totals["net_theta"] += greeks.get("theta", 0.0) * mult
        totals["net_vega"] += greeks.get("vega", 0.0) * mult
        totals["net_vanna"] += greeks.get("vanna", 0.0) * mult
        totals["net_charm"] += greeks.get("charm", 0.0) * mult
        totals["net_volga"] += greeks.get("volga", 0.0) * mult

    return {
        **{k: round(v, 6) for k, v in totals.items()},
        "position_count": len(rows),
        "stale_count": stale_count,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
