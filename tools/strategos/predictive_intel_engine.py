#!/usr/bin/env python3
# CUI // SP-CTI
"""Predictive Intelligence Engine — OSINT data mesh → leadership briefings.

Aggregates the full OSINT data mesh (sg_raw_signals, sg_conflict_events,
sg_prioritized_signals) and runs the SIO Oracle to produce forward-looking
predictive assessments and auto-generated leadership briefings.

Public API:
    generate_leadership_brief(theater, save) → dict
    list_leadership_briefs(theater, limit)   → list[dict]
    get_leadership_brief(brief_id)           → dict | None
    run_predictive_analysis(theater)         → dict   (no DB write)

CLI:
    python tools/strategos/predictive_intel_engine.py --theater global --json
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection, is_pg
from tools.llm.router import LLMRouter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLASSIFICATION = "CUI // SP-CTI"

# Forecast horizon labels and lookback for velocity
_HORIZONS = {
    "24h":  {"hours": 24,  "label": "Next 24 Hours"},
    "72h":  {"hours": 72,  "label": "Next 72 Hours"},
    "7d":   {"hours": 168, "label": "Next 7 Days"},
}

# SIO composite → threat tier mapping
_THREAT_TIERS = [
    (8.0, "CRITICAL",  "#dc2626"),
    (6.0, "HIGH",      "#ea580c"),
    (4.0, "ELEVATED",  "#d97706"),
    (2.0, "GUARDED",   "#2563eb"),
    (0.0, "LOW",       "#16a34a"),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_str(val) -> str:
    """Coerce datetime or str to ISO string."""
    if val is None:
        return ""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _safe_count(conn, query: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(query, params).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _safe_texts(conn, query: str, params: tuple = (), limit: int = 5) -> list[str]:
    try:
        rows = conn.execute(query, params).fetchmany(limit)
        return [str(r[0]) for r in rows if r and r[0]]
    except Exception:
        return []


def _threat_tier(score: float) -> tuple[str, str]:
    for threshold, label, color in _THREAT_TIERS:
        if score >= threshold:
            return label, color
    return "LOW", "#16a34a"


# ---------------------------------------------------------------------------
# Data mesh aggregation
# ---------------------------------------------------------------------------

def _aggregate_mesh(theater: str, lookback_hours: int = 24) -> dict:
    """Pull key metrics from the OSINT data mesh tables."""
    conn = get_connection()
    ph = "%s" if is_pg() else "?"

    if is_pg():
        ts_filter = f"created_at > NOW() - INTERVAL '{lookback_hours} hours'"
        ts_filter_event = f"event_ts > NOW() - INTERVAL '{lookback_hours} hours'"
    else:
        ts_filter = f"created_at > datetime('now', '-{lookback_hours} hours')"
        ts_filter_event = f"event_ts > datetime('now', '-{lookback_hours} hours')"

    try:
        raw_count = _safe_count(
            conn,
            f"SELECT COUNT(*) FROM sg_raw_signals WHERE {ts_filter}",  # nosec B608
        )
        prioritized_count = _safe_count(
            conn,
            f"SELECT COUNT(*) FROM sg_prioritized_signals WHERE {ts_filter}",  # nosec B608
        )

        # Conflict events — optionally filtered by AOI / theater
        if theater and theater != "global":
            event_count = _safe_count(
                conn,
                f"SELECT COUNT(*) FROM sg_conflict_events "  # nosec B608
                f"WHERE {ts_filter_event} AND aoi = {ph}",
                (theater,),
            )
        else:
            event_count = _safe_count(
                conn,
                f"SELECT COUNT(*) FROM sg_conflict_events WHERE {ts_filter_event}",  # nosec B608
            )

        # High-priority signals (composite_score > 0.7)
        high_priority_count = _safe_count(
            conn,
            f"SELECT COUNT(*) FROM sg_prioritized_signals "  # nosec B608
            f"WHERE {ts_filter} AND composite_score > 0.7",
        )

        # Recent signal snippets for narrative context
        snippets = _safe_texts(
            conn,
            "SELECT title FROM sg_raw_signals "
            f"WHERE {ts_filter} ORDER BY created_at DESC",  # nosec B608
            limit=6,
        )

        # Average Goldstein scale from recent conflict events (hostility proxy)
        goldstein_avg = 0.0
        try:
            if theater and theater != "global":
                row = conn.execute(
                    f"SELECT AVG(goldstein_scale) FROM sg_conflict_events "  # nosec B608
                    f"WHERE {ts_filter_event} AND aoi = {ph}",
                    (theater,),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT AVG(goldstein_scale) FROM sg_conflict_events "  # nosec B608
                    f"WHERE {ts_filter_event}",
                ).fetchone()
            goldstein_avg = float(row[0]) if row and row[0] is not None else 0.0
        except Exception:
            pass

        # DTI score (Diplomatic Tension Index)
        dti_score = 0.0
        try:
            t_id = theater if theater and theater != "global" else "global"
            row = conn.execute(
                "SELECT dti_score FROM sg_dat_dti_snapshots "
                f"WHERE theater_id = {ph} ORDER BY computed_at DESC LIMIT 1",  # nosec B608
                (t_id,),
            ).fetchone()
            dti_score = float(row[0]) if row and row[0] is not None else 0.0
        except Exception:
            pass

    finally:
        conn.close()

    return {
        "raw_signal_count": raw_count,
        "prioritized_count": prioritized_count,
        "conflict_event_count": event_count,
        "high_priority_count": high_priority_count,
        "goldstein_avg": round(goldstein_avg, 2),
        "dti_score": round(dti_score, 1),
        "signal_snippets": snippets,
        "lookback_hours": lookback_hours,
    }


def _compute_signal_velocity(theater: str) -> float:
    """Compute signal velocity: (last 24h count) / (prior 24h count) - 1."""
    conn = get_connection()
    if is_pg():
        recent_filter = "created_at > NOW() - INTERVAL '24 hours'"
        prior_filter  = "created_at BETWEEN NOW() - INTERVAL '48 hours' AND NOW() - INTERVAL '24 hours'"
    else:
        recent_filter = "created_at > datetime('now', '-24 hours')"
        prior_filter  = "created_at BETWEEN datetime('now', '-48 hours') AND datetime('now', '-24 hours')"
    try:
        recent = _safe_count(conn, f"SELECT COUNT(*) FROM sg_raw_signals WHERE {recent_filter}")  # nosec B608
        prior  = _safe_count(conn, f"SELECT COUNT(*) FROM sg_raw_signals WHERE {prior_filter}")   # nosec B608
    finally:
        conn.close()

    if prior == 0:
        return 0.0
    return round((recent - prior) / prior, 3)


def _get_bayesian_posterior(theater: str) -> float:
    """Read latest P(war) posterior for this theater."""
    conn = get_connection()
    ph = "%s" if is_pg() else "?"
    try:
        row = conn.execute(
            "SELECT posterior FROM sg_bayesian_war_posteriors "
            f"WHERE theater_id = {ph} ORDER BY updated_at DESC LIMIT 1",  # nosec B608
            (theater if theater and theater != "global" else "global",),
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.05
    except Exception:
        return 0.05
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SIO Oracle integration
# ---------------------------------------------------------------------------

def _run_oracle() -> dict:
    """Run SIO Oracle or return fallback."""
    try:
        from intelligence.oracle.sio_engine import SIOEngine
        assessment = SIOEngine().run_all()
        return {
            "composite_score": assessment.composite_score,
            "iw_triggered": assessment.iw_triggered,
            "top_narrative": assessment.top_narrative,
            "lenses": {
                k: {
                    "score": float(v.get("score", 0.0)),
                    "nato_reliability": v.get("nato_reliability", "F6"),
                    "narrative": v.get("narrative", ""),
                }
                for k, v in assessment.lenses.items()
            },
        }
    except Exception:
        return {
            "composite_score": 5.0,
            "iw_triggered": False,
            "top_narrative": "SIO Oracle data unavailable — using baseline estimate.",
            "lenses": {},
        }


# ---------------------------------------------------------------------------
# Predictive forecasting
# ---------------------------------------------------------------------------

def _forecast_horizon(
    sio_score: float,
    signal_velocity: float,
    p_war: float,
    horizon_hours: int,
) -> dict:
    """Generate a probabilistic forecast for a given time horizon."""
    # Decay factor: nearer horizons are more confident
    horizon_days = horizon_hours / 24
    confidence_decay = max(0.4, 1.0 - (horizon_days * 0.08))

    # Escalation probability: weighted composite
    escalation_prob = min(0.99, (
        (sio_score / 10.0) * 0.45 +
        p_war * 0.35 +
        max(0.0, min(1.0, signal_velocity + 0.5)) * 0.20
    ) * confidence_decay)

    # De-escalation probability (inverse)
    deescalation_prob = max(0.01, 1.0 - escalation_prob - 0.10)

    # Status quo
    status_quo_prob = max(0.0, 1.0 - escalation_prob - deescalation_prob)

    # Normalize
    total = escalation_prob + deescalation_prob + status_quo_prob
    if total > 0:
        escalation_prob   = round(escalation_prob / total, 3)
        deescalation_prob = round(deescalation_prob / total, 3)
        status_quo_prob   = round(status_quo_prob / total, 3)

    # Dominant scenario
    probs = {
        "escalation": escalation_prob,
        "status_quo": status_quo_prob,
        "de_escalation": deescalation_prob,
    }
    dominant = max(probs, key=probs.__getitem__)

    return {
        "horizon_hours": horizon_hours,
        "confidence_factor": round(confidence_decay, 3),
        "probabilities": probs,
        "dominant_scenario": dominant,
        "escalation_pct": int(escalation_prob * 100),
        "status_quo_pct": int(status_quo_prob * 100),
        "de_escalation_pct": int(deescalation_prob * 100),
    }


# ---------------------------------------------------------------------------
# Narrative generation
# ---------------------------------------------------------------------------

_BRIEF_SYSTEM_PROMPT = (
    "You are an intelligence analyst producing executive-level strategic briefings "
    "for senior leadership. Write in formal intelligence prose. Be concise, direct, "
    "and actionable. Use classification markings. Avoid speculation beyond the data. "
    "Structure output with: SITUATION SUMMARY, KEY INDICATORS, PREDICTIVE ASSESSMENT, "
    "RECOMMENDED DECISION POINTS. Each section 2–4 sentences."
)


def _generate_narrative(
    theater: str,
    oracle: dict,
    mesh: dict,
    forecasts: dict,
    signal_velocity: float,
    p_war: float,
) -> str:
    tier, _ = _threat_tier(oracle["composite_score"])
    dominant_24h = forecasts["24h"]["dominant_scenario"].replace("_", " ").upper()

    user_prompt = (
        f"Theater: {theater.upper()}\n"
        f"SIO Composite Score: {oracle['composite_score']:.1f}/10 ({tier})\n"
        f"I&W Status: {'TRIGGERED' if oracle['iw_triggered'] else 'NOMINAL'}\n"
        f"Bayesian P(war/90d): {p_war*100:.1f}%\n"
        f"Signal velocity (24h delta): {signal_velocity:+.1%}\n"
        f"Raw signals ingested (24h): {mesh['raw_signal_count']}\n"
        f"Conflict events (24h): {mesh['conflict_event_count']}\n"
        f"Goldstein hostility avg: {mesh['goldstein_avg']}\n"
        f"Diplomatic Tension Index: {mesh['dti_score']}/100\n"
        f"24h dominant scenario: {dominant_24h} ({forecasts['24h']['escalation_pct']}% escalation)\n"
        f"72h dominant scenario: {forecasts['72h']['dominant_scenario'].replace('_', ' ').upper()} ({forecasts['72h']['escalation_pct']}% escalation)\n"
        f"7d dominant scenario: {forecasts['7d']['dominant_scenario'].replace('_', ' ').upper()} ({forecasts['7d']['escalation_pct']}% escalation)\n\n"
        f"Top OSINT signals:\n" +
        "\n".join(f"  - {s}" for s in mesh["signal_snippets"][:5]) +
        f"\n\nTop narrative: {oracle['top_narrative']}\n\n"
        "Generate a leadership briefing structured as described."
    )

    try:
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        response = router.invoke(
            "intelligence_report",
            LLMRequest(
                system_prompt=_BRIEF_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            ),
        )
        return (response.content or "").strip() or _fallback_narrative(theater, oracle, mesh, forecasts)
    except Exception:
        return _fallback_narrative(theater, oracle, mesh, forecasts)


def _fallback_narrative(theater: str, oracle: dict, mesh: dict, forecasts: dict) -> str:
    tier, _ = _threat_tier(oracle["composite_score"])
    return (
        f"**SITUATION SUMMARY ({CLASSIFICATION})**\n\n"
        f"Theater {theater.upper()} — Threat Assessment: **{tier}** "
        f"(SIO composite {oracle['composite_score']:.1f}/10). "
        f"I&W status: {'**TRIGGERED**' if oracle['iw_triggered'] else 'nominal'}. "
        f"{mesh['raw_signal_count']} OSINT signals ingested in the past 24 hours with "
        f"{mesh['conflict_event_count']} conflict events recorded.\n\n"
        f"**KEY INDICATORS**\n\n"
        f"Signal velocity: {_pct_str(forecasts['24h']['escalation_pct'])} escalation probability "
        f"over the next 24 hours. Goldstein hostility index: {mesh['goldstein_avg']:.1f}. "
        f"Diplomatic Tension Index: {mesh['dti_score']}/100.\n\n"
        f"**PREDICTIVE ASSESSMENT**\n\n"
        f"24h forecast: {forecasts['24h']['dominant_scenario'].replace('_', ' ')} scenario most probable "
        f"({forecasts['24h']['escalation_pct']}% escalation / {forecasts['24h']['status_quo_pct']}% status quo). "
        f"72h forecast: {forecasts['72h']['dominant_scenario'].replace('_', ' ')} "
        f"({forecasts['72h']['escalation_pct']}% escalation). "
        f"7-day outlook: {forecasts['7d']['dominant_scenario'].replace('_', ' ')} "
        f"({forecasts['7d']['escalation_pct']}% escalation).\n\n"
        f"**RECOMMENDED DECISION POINTS**\n\n"
        f"Monitor high-priority signals ({mesh['high_priority_count']} flagged). "
        f"Review SIO lens assessments for {_top_lens(oracle)}. "
        f"Consider activating CCIR review cycle if I&W remains elevated."
    )


def _pct_str(pct: int) -> str:
    return f"{pct}%"


def _top_lens(oracle: dict) -> str:
    if not oracle.get("lenses"):
        return "all lenses"
    top = max(oracle["lenses"], key=lambda k: oracle["lenses"][k].get("score", 0))
    return top.replace("_", " ")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_predictive_analysis(theater: str = "global") -> dict:
    """Run full predictive analysis pipeline without DB persistence."""
    mesh = _aggregate_mesh(theater, lookback_hours=24)
    oracle = _run_oracle()
    signal_velocity = _compute_signal_velocity(theater)
    p_war = _get_bayesian_posterior(theater)

    forecasts = {
        horizon: _forecast_horizon(
            oracle["composite_score"], signal_velocity, p_war, cfg["hours"]
        )
        for horizon, cfg in _HORIZONS.items()
    }

    tier, tier_color = _threat_tier(oracle["composite_score"])

    return {
        "theater": theater,
        "generated_at": _now_utc(),
        "classification": CLASSIFICATION,
        "oracle": oracle,
        "mesh": mesh,
        "signal_velocity": signal_velocity,
        "p_war": p_war,
        "forecasts": forecasts,
        "threat_tier": tier,
        "threat_tier_color": tier_color,
    }


def generate_leadership_brief(theater: str = "global", save: bool = True) -> dict:
    """Generate a full leadership briefing and optionally persist to DB."""
    import time
    t0 = time.monotonic()

    analysis = run_predictive_analysis(theater)
    oracle = analysis["oracle"]
    mesh = analysis["mesh"]
    forecasts = analysis["forecasts"]
    signal_velocity = analysis["signal_velocity"]
    p_war = analysis["p_war"]

    narrative_md = _generate_narrative(
        theater, oracle, mesh, forecasts, signal_velocity, p_war
    )

    brief_id = str(uuid.uuid4())
    latency_ms = int((time.monotonic() - t0) * 1000)

    result = {
        "brief_id": brief_id,
        "theater": theater,
        "classification": CLASSIFICATION,
        "sio_composite_score": oracle["composite_score"],
        "iw_triggered": oracle["iw_triggered"],
        "threat_tier": analysis["threat_tier"],
        "threat_tier_color": analysis["threat_tier_color"],
        "signal_count_24h": mesh["raw_signal_count"],
        "conflict_event_count": mesh["conflict_event_count"],
        "signal_velocity": signal_velocity,
        "p_war": p_war,
        "forecasts": forecasts,
        "narrative_md": narrative_md,
        "lenses": oracle.get("lenses", {}),
        "generated_at": analysis["generated_at"],
        "latency_ms": latency_ms,
    }

    if save:
        _persist_brief(result, mesh)

    return result


def _persist_brief(brief: dict, mesh: dict) -> None:
    conn = get_connection()
    ph = "%s" if is_pg() else "?"
    try:
        conn.execute(
            f"INSERT INTO sg_leadership_briefs "  # nosec B608
            f"(id, theater, sio_composite_score, iw_triggered, threat_tier, "
            f"signal_count_24h, conflict_event_count, signal_velocity, "
            f"p_war_posterior, goldstein_avg, dti_score, "
            f"forecast_24h_json, forecast_72h_json, forecast_7d_json, "
            f"narrative_md, generated_at, classification) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                brief["brief_id"],
                brief["theater"],
                brief["sio_composite_score"],
                1 if brief["iw_triggered"] else 0,
                brief["threat_tier"],
                brief["signal_count_24h"],
                brief["conflict_event_count"],
                brief["signal_velocity"],
                brief["p_war"],
                mesh.get("goldstein_avg", 0.0),
                mesh.get("dti_score", 0.0),
                json.dumps(brief["forecasts"]["24h"]),
                json.dumps(brief["forecasts"]["72h"]),
                json.dumps(brief["forecasts"]["7d"]),
                brief["narrative_md"],
                brief["generated_at"],
                brief["classification"],
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def list_leadership_briefs(theater: str = "global", limit: int = 20) -> list[dict]:
    conn = get_connection()
    ph = "%s" if is_pg() else "?"
    try:
        if theater and theater != "global":
            rows = conn.execute(
                f"SELECT id, theater, sio_composite_score, iw_triggered, threat_tier, "  # nosec B608
                f"signal_count_24h, signal_velocity, p_war_posterior, generated_at "
                f"FROM sg_leadership_briefs WHERE theater = {ph} "
                f"ORDER BY generated_at DESC LIMIT {ph}",
                (theater, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, theater, sio_composite_score, iw_triggered, threat_tier, "  # nosec B608
                f"signal_count_24h, signal_velocity, p_war_posterior, generated_at "
                f"FROM sg_leadership_briefs ORDER BY generated_at DESC LIMIT {ph}",
                (limit,),
            ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    cols = ("id", "theater", "sio_composite_score", "iw_triggered", "threat_tier",
            "signal_count_24h", "signal_velocity", "p_war_posterior", "generated_at")
    result = []
    for r in rows:
        row = dict(zip(cols, r))
        row["generated_at"] = _to_str(row.get("generated_at"))
        result.append(row)
    return result


def get_leadership_brief(brief_id: str) -> dict | None:
    conn = get_connection()
    ph = "%s" if is_pg() else "?"
    try:
        row = conn.execute(
            f"SELECT id, theater, sio_composite_score, iw_triggered, threat_tier, "  # nosec B608
            f"signal_count_24h, conflict_event_count, signal_velocity, p_war_posterior, "
            f"goldstein_avg, dti_score, forecast_24h_json, forecast_72h_json, "
            f"forecast_7d_json, narrative_md, generated_at, classification "
            f"FROM sg_leadership_briefs WHERE id = {ph}",
            (brief_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()

    if not row:
        return None

    cols = ("id", "theater", "sio_composite_score", "iw_triggered", "threat_tier",
            "signal_count_24h", "conflict_event_count", "signal_velocity", "p_war_posterior",
            "goldstein_avg", "dti_score", "forecast_24h_json", "forecast_72h_json",
            "forecast_7d_json", "narrative_md", "generated_at", "classification")
    brief = dict(zip(cols, row))

    for key in ("forecast_24h_json", "forecast_72h_json", "forecast_7d_json"):
        try:
            brief[key] = json.loads(brief[key]) if brief[key] else {}
        except Exception:
            brief[key] = {}

    brief["generated_at"] = _to_str(brief.get("generated_at"))

    # Remap forecast keys to match API response shape
    brief["forecasts"] = {
        "24h": brief.pop("forecast_24h_json", {}),
        "72h": brief.pop("forecast_72h_json", {}),
        "7d":  brief.pop("forecast_7d_json", {}),
    }
    brief["p_war"] = brief.pop("p_war_posterior", 0.05)

    return brief


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Predictive Intelligence Engine")
    parser.add_argument("--theater", default="global", help="Theater name (e.g. ukraine, pacific, global)")
    parser.add_argument("--no-save", action="store_true", help="Run analysis without saving to DB")
    parser.add_argument("--list", action="store_true", help="List recent briefs")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.list:
        briefs = list_leadership_briefs(theater=args.theater, limit=10)
        if args.json:
            print(json.dumps({"briefs": briefs}, indent=2))
        else:
            for b in briefs:
                print(f"[{b['generated_at']}] {b['theater']} | {b['threat_tier']} | SIO {b['sio_composite_score']:.1f} | {b['id']}")
        sys.exit(0)

    result = generate_leadership_brief(theater=args.theater, save=not args.no_save)
    if args.json:
        out = {k: v for k, v in result.items() if k != "narrative_md"}
        out["narrative_md"] = result["narrative_md"][:500] + "…" if len(result.get("narrative_md", "")) > 500 else result.get("narrative_md", "")
        print(json.dumps(out, indent=2))
    else:
        print(f"Brief ID:  {result['brief_id']}")
        print(f"Theater:   {result['theater']}")
        print(f"SIO Score: {result['sio_composite_score']:.1f}/10 ({result['threat_tier']})")
        print(f"I&W:       {'TRIGGERED' if result['iw_triggered'] else 'nominal'}")
        print(f"P(war):    {result['p_war']*100:.1f}%")
        print(f"Velocity:  {result['signal_velocity']:+.1%}")
        print(f"\n--- BRIEFING ---\n{result['narrative_md']}")
