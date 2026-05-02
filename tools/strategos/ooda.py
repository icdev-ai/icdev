#!/usr/bin/env python3
# CUI // SP-CTI
"""OODA Tempo Analyzer — measures decision-cycle speed for Blue and Red forces.

Domains tracked: land, air, sea, cyber, ew, space
Metrics per side:
  - observe_latency_s  : mean seconds from event occurrence to detection
  - orient_latency_s   : mean seconds from detection to analysis complete
  - decide_latency_s   : mean seconds from analysis to decision issued
  - act_latency_s      : mean seconds from decision to action executed
  - cycle_time_s       : sum of four phases
  - tempo_score        : 0–1 inverse normalized (higher = faster decision cycle)
  - command_interval_s : mean seconds between successive command events
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection, is_pg

# ── Constants ─────────────────────────────────────────────────────────────────

DOMAINS = ("land", "air", "sea", "cyber", "ew", "space")

PHASES = ("observe", "orient", "decide", "act")

# Fallback baselines (seconds) used when no DB data exists.
# Seeded from publicly available doctrine estimates for reference-scenario use.
_BASELINE_BLUE: dict[str, dict[str, float]] = {
    "land":  {"observe": 120, "orient": 180, "decide": 300, "act": 240},
    "air":   {"observe":  30, "orient":  60, "decide":  90, "act":  45},
    "sea":   {"observe":  90, "orient": 150, "decide": 240, "act": 180},
    "cyber": {"observe":  15, "orient":  45, "decide":  60, "act":  20},
    "ew":    {"observe":  10, "orient":  30, "decide":  45, "act":  15},
    "space": {"observe": 300, "orient": 480, "decide": 600, "act": 360},
}
_BASELINE_RED: dict[str, dict[str, float]] = {
    "land":  {"observe": 180, "orient": 240, "decide": 480, "act": 300},
    "air":   {"observe":  45, "orient":  90, "decide": 150, "act":  60},
    "sea":   {"observe": 120, "orient": 200, "decide": 360, "act": 240},
    "cyber": {"observe":  30, "orient":  60, "decide":  90, "act":  40},
    "ew":    {"observe":  20, "orient":  45, "decide":  75, "act":  30},
    "space": {"observe": 420, "orient": 600, "decide": 840, "act": 480},
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _fetch_phase_latencies(wargame_id: str | None = None) -> dict[str, dict[str, dict[str, float]]]:
    """Return {side: {domain: {phase: mean_seconds}}} from sg_ooda_events."""
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if wargame_id:
            rows = conn.execute(
                f"SELECT side, domain, phase, latency_s "  # nosec B608
                f"FROM sg_ooda_events WHERE wargame_id = {ph}",
                (wargame_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT side, domain, phase, latency_s FROM sg_ooda_events"
            ).fetchall()
    except Exception:
        return {}
    finally:
        conn.close()

    # Aggregate: sum + count per (side, domain, phase)
    agg: dict[tuple, list[float]] = {}
    for r in rows:
        key = (r[0], r[1], r[2])
        agg.setdefault(key, []).append(float(r[3] or 0))

    result: dict[str, dict[str, dict[str, float]]] = {}
    for (side, domain, phase), vals in agg.items():
        result.setdefault(side, {}).setdefault(domain, {})[phase] = (
            sum(vals) / len(vals)
        )
    return result


def _fetch_command_intervals(wargame_id: str | None = None) -> dict[str, dict[str, float]]:
    """Return {side: {domain: mean_interval_s}} from sg_ooda_events where phase='act'."""
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if wargame_id:
            rows = conn.execute(
                f"SELECT side, domain, event_ts "  # nosec B608
                f"FROM sg_ooda_events WHERE phase='act' AND wargame_id = {ph} "
                f"ORDER BY side, domain, event_ts",
                (wargame_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT side, domain, event_ts "
                "FROM sg_ooda_events WHERE phase='act' "
                "ORDER BY side, domain, event_ts"
            ).fetchall()
    except Exception:
        return {}
    finally:
        conn.close()

    # Group and diff consecutive timestamps
    groups: dict[tuple, list[str]] = {}
    for r in rows:
        groups.setdefault((r[0], r[1]), []).append(r[2])

    result: dict[str, dict[str, float]] = {}
    for (side, domain), timestamps in groups.items():
        if len(timestamps) < 2:
            continue
        diffs: list[float] = []
        for i in range(1, len(timestamps)):
            try:
                t0 = datetime.fromisoformat(str(timestamps[i - 1]))
                t1 = datetime.fromisoformat(str(timestamps[i]))
                diffs.append(abs((t1 - t0).total_seconds()))
            except Exception:
                pass
        if diffs:
            result.setdefault(side, {})[domain] = sum(diffs) / len(diffs)
    return result


# ── Core computation ───────────────────────────────────────────────────────────

def _tempo_score(cycle_time_s: float, max_cycle: float) -> float:
    """Higher score = faster (shorter cycle). Normalized 0–1."""
    if max_cycle <= 0:
        return 0.5
    return round(1.0 - min(cycle_time_s / max_cycle, 1.0), 4)


def compute_tempo(wargame_id: str | None = None) -> dict[str, Any]:
    """Compute per-domain OODA tempo for Blue and Red forces.

    Returns:
        {
          "domains": [...],
          "blue": { domain: { phase_latencies, cycle_time_s, tempo_score,
                               command_interval_s } },
          "red":  { ... },
          "advantage": { domain: "blue"|"red"|"parity" },
          "radar_data": { "labels": [...], "blue": [...], "red": [...] }
        }
    """
    db_latencies = _fetch_phase_latencies(wargame_id)
    db_intervals = _fetch_command_intervals(wargame_id)

    sides_out: dict[str, dict[str, Any]] = {"blue": {}, "red": {}}
    baselines = {"blue": _BASELINE_BLUE, "red": _BASELINE_RED}
    all_cycles: list[float] = []

    for side in ("blue", "red"):
        for domain in DOMAINS:
            db_d = db_latencies.get(side, {}).get(domain, {})
            bl_d = baselines[side].get(domain, {})
            phase_vals: dict[str, float] = {}
            for phase in PHASES:
                phase_vals[phase] = db_d.get(phase, bl_d.get(phase, 120.0))

            cycle = sum(phase_vals.values())
            all_cycles.append(cycle)

            cmd_interval = (
                db_intervals.get(side, {}).get(domain)
                or (cycle * 1.5)  # heuristic fallback
            )

            sides_out[side][domain] = {
                "observe_latency_s":  round(phase_vals["observe"], 1),
                "orient_latency_s":   round(phase_vals["orient"],  1),
                "decide_latency_s":   round(phase_vals["decide"],  1),
                "act_latency_s":      round(phase_vals["act"],     1),
                "cycle_time_s":       round(cycle, 1),
                "command_interval_s": round(cmd_interval, 1),
            }

    max_cycle = max(all_cycles) if all_cycles else 1.0

    # Tempo scores and advantage
    advantage: dict[str, str] = {}
    for domain in DOMAINS:
        for side in ("blue", "red"):
            c = sides_out[side][domain]["cycle_time_s"]
            sides_out[side][domain]["tempo_score"] = _tempo_score(c, max_cycle)

        b_score = sides_out["blue"][domain]["tempo_score"]
        r_score = sides_out["red"][domain]["tempo_score"]
        delta = abs(b_score - r_score)
        if delta < 0.05:
            advantage[domain] = "parity"
        elif b_score > r_score:
            advantage[domain] = "blue"
        else:
            advantage[domain] = "red"

    # Radar chart arrays (scores * 100 for display)
    radar_labels = [d.upper() for d in DOMAINS]
    radar_blue = [round(sides_out["blue"][d]["tempo_score"] * 100, 1) for d in DOMAINS]
    radar_red  = [round(sides_out["red"][d]["tempo_score"] * 100, 1)  for d in DOMAINS]

    return {
        "domains":    list(DOMAINS),
        "blue":       sides_out["blue"],
        "red":        sides_out["red"],
        "advantage":  advantage,
        "radar_data": {
            "labels": radar_labels,
            "blue":   radar_blue,
            "red":    radar_red,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Lanchester attrition ───────────────────────────────────────────────────────

def lanchester_square(
    b0: float, r0: float,
    beta: float = 0.01, rho: float = 0.01,
    dt: float = 1.0, max_steps: int = 500,
) -> dict[str, Any]:
    """Square-law Lanchester model (modern ranged combat).

    dB/dt = -rho * R
    dR/dt = -beta * B

    Returns time-series arrays and outcome summary.
    """
    b, r = float(b0), float(r0)
    t_series, b_series, r_series = [0.0], [b], [r]

    for step in range(1, max_steps + 1):
        db = -rho * r * dt
        dr = -beta * b * dt
        b = max(0.0, b + db)
        r = max(0.0, r + dr)
        t_series.append(float(step) * dt)
        b_series.append(round(b, 2))
        r_series.append(round(r, 2))
        if b <= 0 or r <= 0:
            break

    if b > r:
        winner, margin = "blue", round((b - r) / b0 * 100, 1)
    elif r > b:
        winner, margin = "red",  round((r - b) / r0 * 100, 1)
    else:
        winner, margin = "parity", 0.0

    return {
        "model":    "lanchester_square",
        "b0": b0, "r0": r0,
        "beta": beta, "rho": rho,
        "t_series": t_series,
        "b_series": b_series,
        "r_series": r_series,
        "winner":   winner,
        "margin_pct": margin,
        "final_b":  round(b, 2),
        "final_r":  round(r, 2),
    }


def lanchester_linear(
    b0: float, r0: float,
    beta: float = 0.01, rho: float = 0.01,
    dt: float = 1.0, max_steps: int = 500,
) -> dict[str, Any]:
    """Linear-law Lanchester model (guerrilla / area fire).

    dB/dt = -rho * R * B / b0
    dR/dt = -beta * B * R / r0
    """
    b, r = float(b0), float(r0)
    t_series, b_series, r_series = [0.0], [b], [r]

    for step in range(1, max_steps + 1):
        db = -rho * r * (b / b0) * dt
        dr = -beta * b * (r / r0) * dt
        b = max(0.0, b + db)
        r = max(0.0, r + dr)
        t_series.append(float(step) * dt)
        b_series.append(round(b, 2))
        r_series.append(round(r, 2))
        if b <= 0 or r <= 0:
            break

    if b > r:
        winner, margin = "blue", round((b - r) / b0 * 100, 1)
    elif r > b:
        winner, margin = "red",  round((r - b) / r0 * 100, 1)
    else:
        winner, margin = "parity", 0.0

    return {
        "model":    "lanchester_linear",
        "b0": b0, "r0": r0,
        "beta": beta, "rho": rho,
        "t_series": t_series,
        "b_series": b_series,
        "r_series": r_series,
        "winner":   winner,
        "margin_pct": margin,
        "final_b":  round(b, 2),
        "final_r":  round(r, 2),
    }


# ── Lanchester Monte Carlo ────────────────────────────────────────────────────

def lanchester_monte_carlo(
    b0: float, r0: float,
    beta: float = 0.01, rho: float = 0.01,
    iterations: int = 500, sigma: float = 0.15,
) -> dict[str, Any]:
    """Monte Carlo Lanchester square-law simulation with Gaussian noise on attrition rates.

    Each iteration perturbs beta and rho independently:
        beta_i = beta * (1 + N(0, sigma))
        rho_i  = rho  * (1 + N(0, sigma))

    Returns time-series percentile bands (p5/p50/p95) and blue victory probability.
    """
    import random

    all_b_series: list[list[float]] = []
    all_r_series: list[list[float]] = []
    final_blues: list[float] = []
    final_reds: list[float] = []
    blue_victories = 0

    for _ in range(iterations):
        beta_i = beta * (1.0 + random.gauss(0.0, sigma))
        rho_i  = rho  * (1.0 + random.gauss(0.0, sigma))
        run = lanchester_square(b0, r0, beta_i, rho_i)
        all_b_series.append(run["b_series"])
        all_r_series.append(run["r_series"])
        fb = run["final_b"]
        fr = run["final_r"]
        final_blues.append(fb)
        final_reds.append(fr)
        if fb > 0 and fr == 0:
            blue_victories += 1

    # Pad all runs to the same length using their final value
    max_len = max(len(s) for s in all_b_series)
    for i in range(iterations):
        b_last = all_b_series[i][-1]
        r_last = all_r_series[i][-1]
        if len(all_b_series[i]) < max_len:
            all_b_series[i] = all_b_series[i] + [b_last] * (max_len - len(all_b_series[i]))
        if len(all_r_series[i]) < max_len:
            all_r_series[i] = all_r_series[i] + [r_last] * (max_len - len(all_r_series[i]))

    def _pct(series: list[float], p: float) -> float:
        sorted_s = sorted(series)
        idx = (len(sorted_s) - 1) * p / 100.0
        lo, hi = int(idx), min(int(idx) + 1, len(sorted_s) - 1)
        frac = idx - lo
        return round(sorted_s[lo] * (1 - frac) + sorted_s[hi] * frac, 2)

    def _ts_pct(all_series: list[list[float]], p: float) -> list[float]:
        result: list[float] = []
        for t in range(max_len):
            vals = sorted(s[t] for s in all_series)
            n = len(vals)
            idx = (n - 1) * p / 100.0
            lo, hi = int(idx), min(int(idx) + 1, n - 1)
            frac = idx - lo
            result.append(round(vals[lo] * (1 - frac) + vals[hi] * frac, 2))
        return result

    t_series = [float(i) for i in range(max_len)]

    return {
        "model":              "lanchester_monte_carlo",
        "b0": b0, "r0": r0,
        "beta": beta, "rho": rho,
        "iterations":         iterations,
        "sigma":              sigma,
        # Time-series percentile bands for chart rendering
        "t_series":           t_series,
        "p5_blue_series":     _ts_pct(all_b_series, 5),
        "p50_blue_series":    _ts_pct(all_b_series, 50),
        "p95_blue_series":    _ts_pct(all_b_series, 95),
        "p5_red_series":      _ts_pct(all_r_series, 5),
        "p50_red_series":     _ts_pct(all_r_series, 50),
        "p95_red_series":     _ts_pct(all_r_series, 95),
        # Scalar final percentiles (backward compat)
        "p5_blue":            _pct(final_blues, 5),
        "p50_blue":           _pct(final_blues, 50),
        "p95_blue":           _pct(final_blues, 95),
        "p5_red":             _pct(final_reds,  5),
        "p50_red":            _pct(final_reds,  50),
        "p95_red":            _pct(final_reds,  95),
        "victory_prob_blue":  round(blue_victories / iterations, 4),
    }


# ── Colonel Blotto ────────────────────────────────────────────────────────────

def blotto_expected_payoff(
    blue_forces: list[float],
    red_forces: list[float],
) -> dict[str, Any]:
    """Colonel Blotto: compare force allocations across battlefields.

    Args:
        blue_forces: list of troop counts per battlefield (Blue side)
        red_forces:  list of troop counts per battlefield (Red side)

    Returns per-battlefield results and aggregate payoff.
    """
    k = min(len(blue_forces), len(red_forces))
    if k == 0:
        return {"error": "no battlefields"}

    fields: list[dict] = []
    blue_wins = red_wins = ties = 0
    for i in range(k):
        b_i = float(blue_forces[i])
        r_i = float(red_forces[i])
        if b_i > r_i:
            outcome = "blue"
            blue_wins += 1
        elif r_i > b_i:
            outcome = "red"
            red_wins += 1
        else:
            outcome = "tie"
            ties += 1
        fields.append({
            "battlefield": i + 1,
            "blue_allocation": b_i,
            "red_allocation":  r_i,
            "winner": outcome,
        })

    total = blue_wins + red_wins + ties
    return {
        "battlefields": fields,
        "blue_wins":  blue_wins,
        "red_wins":   red_wins,
        "ties":       ties,
        "blue_score": round(blue_wins / total * 100, 1) if total else 0.0,
        "red_score":  round(red_wins  / total * 100, 1) if total else 0.0,
        "overall_winner": (
            "blue" if blue_wins > red_wins else
            "red"  if red_wins > blue_wins else "parity"
        ),
    }


# ── Nash equilibrium (2×2) ────────────────────────────────────────────────────

def find_nash_2x2(
    payoff_blue: list[list[float]],
    payoff_red:  list[list[float]],
    strategy_names: list[list[str]] | None = None,
) -> dict[str, Any]:
    """Find pure-strategy Nash equilibria in a 2×2 (or n×m) bimatrix game.

    Args:
        payoff_blue: Row-player (Blue) payoff matrix  [rows][cols]
        payoff_red:  Col-player (Red) payoff matrix   [rows][cols]
        strategy_names: [["B_strat1","B_strat2",...],["R_strat1","R_strat2",...]]

    Returns pure Nash equilibria (row, col) pairs and the payoff values.
    """
    rows = len(payoff_blue)
    cols = len(payoff_blue[0]) if rows else 0
    if rows == 0 or cols == 0:
        return {"error": "empty payoff matrix"}

    equilibria: list[dict] = []
    for r in range(rows):
        for c in range(cols):
            # Blue best-response: for fixed c, no row gives Blue a higher payoff
            blue_br = all(
                payoff_blue[r][c] >= payoff_blue[r2][c] for r2 in range(rows)
            )
            # Red best-response: for fixed r, no col gives Red a higher payoff
            red_br = all(
                payoff_red[r][c] >= payoff_red[r][c2] for c2 in range(cols)
            )
            if blue_br and red_br:
                b_names = (strategy_names or [[], []])[0]
                r_names = (strategy_names or [[], []])[1]
                equilibria.append({
                    "row": r,
                    "col": c,
                    "blue_strategy": b_names[r] if r < len(b_names) else f"B{r+1}",
                    "red_strategy":  r_names[c] if c < len(r_names) else f"R{c+1}",
                    "blue_payoff": payoff_blue[r][c],
                    "red_payoff":  payoff_red[r][c],
                })

    # Mixed strategy for 2×2
    mixed: dict | None = None
    if rows == 2 and cols == 2:
        try:
            # Blue mixes: p such that Red is indifferent
            a11, a12 = payoff_red[0][0], payoff_red[0][1]
            a21, a22 = payoff_red[1][0], payoff_red[1][1]
            denom_b = (a11 - a12 - a21 + a22)
            p_blue = (a22 - a21) / denom_b if abs(denom_b) > 1e-9 else None

            b11, b12 = payoff_blue[0][0], payoff_blue[0][1]
            b21, b22 = payoff_blue[1][0], payoff_blue[1][1]
            denom_r = (b11 - b12 - b21 + b22)
            p_red = (b22 - b21) / denom_r if abs(denom_r) > 1e-9 else None

            if p_blue is not None and p_red is not None:
                p_blue = max(0.0, min(1.0, p_blue))
                p_red  = max(0.0, min(1.0, p_red))
                mixed = {
                    "blue_mix_p0": round(p_blue, 4),
                    "blue_mix_p1": round(1.0 - p_blue, 4),
                    "red_mix_p0":  round(p_red, 4),
                    "red_mix_p1":  round(1.0 - p_red, 4),
                }
        except Exception:
            pass

    return {
        "rows": rows,
        "cols": cols,
        "payoff_blue": payoff_blue,
        "payoff_red":  payoff_red,
        "pure_equilibria": equilibria,
        "mixed_equilibrium": mixed,
    }


# ── COA comparison ────────────────────────────────────────────────────────────

COA_CRITERIA = ("speed", "surprise", "mass", "economy_of_force", "maneuver", "sustainability")


def score_coa(
    coas: list[dict],
    weights: dict[str, float] | None = None,
) -> list[dict]:
    """Score and rank Courses of Action.

    Each COA dict must have a 'name' key and numeric values for each criterion.
    Missing criteria default to 0.5.

    Args:
        coas: list of COA dicts
        weights: criterion -> weight (default equal weights)

    Returns sorted list of COAs with added 'composite_score' and 'rank'.
    """
    if weights is None:
        weights = {c: 1.0 / len(COA_CRITERIA) for c in COA_CRITERIA}

    scored: list[dict] = []
    for coa in coas:
        composite = sum(
            float(coa.get(criterion, 0.5)) * weights.get(criterion, 0.0)
            for criterion in COA_CRITERIA
        )
        scored.append({**coa, "composite_score": round(composite, 4)})

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    for rank, coa in enumerate(scored, 1):
        coa["rank"] = rank
    return scored


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="OODA Tempo Analyzer")
    parser.add_argument("--wargame-id", default=None)
    parser.add_argument("--lanchester", action="store_true")
    parser.add_argument("--b0", type=float, default=1000)
    parser.add_argument("--r0", type=float, default=800)
    parser.add_argument("--beta", type=float, default=0.01)
    parser.add_argument("--rho",  type=float, default=0.01)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.lanchester:
        result = lanchester_square(args.b0, args.r0, args.beta, args.rho)
    else:
        result = compute_tempo(args.wargame_id)

    if args.json:
        print(_json.dumps(result, indent=2))
    else:
        import pprint
        pprint.pprint(result)
