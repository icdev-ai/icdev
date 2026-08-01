"""FathomDesk Expert Advisory agents — 6-agent CIS recommendation engine.

Each agent reads the latest signal + analyst run + macro context and applies
its own style bias. The Committee of Investment Specialists (CIS) then
synthesizes the 6 opinions into a final recommendation.

No LLM calls here — opinions are derived deterministically from existing
pipeline outputs so the advisor page is fast and the auto-trader can rely on
the result. When a full LLM-powered re-rater lands, it can layer on top.

Tables owned here (DDL is idempotent):
  ad_expert_opinions         — per-run per-agent opinion
  ad_cis_recommendations     — CIS-synthesized verdict
  ad_daily_briefs            — morning brief snapshot
  ad_risk_profiles           — conservative / moderate / aggressive / custom
  ad_expert_recommendations  — legacy rec table consumed by /advisor
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.trading.market_intel.expert_agents")


# =========================================================================
# Schema
# =========================================================================

_ADVISOR_TABLES = [
    """CREATE TABLE IF NOT EXISTS ad_expert_opinions (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        expert_key TEXT NOT NULL,
        expert_name TEXT NOT NULL,
        direction TEXT NOT NULL,
        conviction INTEGER NOT NULL,
        reasoning TEXT,
        risk_profile TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_cis_recommendations (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        final_direction TEXT NOT NULL,
        final_conviction INTEGER NOT NULL,
        expert_votes TEXT,
        synthesis TEXT,
        narrative TEXT,
        auto_trade INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_daily_briefs (
        id TEXT PRIMARY KEY,
        brief_date TEXT NOT NULL,
        market_summary TEXT,
        top_ideas TEXT,
        watchlist TEXT,
        risk_alerts TEXT,
        expert_highlights TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_risk_profiles (
        id TEXT PRIMARY KEY,
        profile_name TEXT NOT NULL UNIQUE,
        max_position_pct REAL DEFAULT 0.15,
        max_daily_budget REAL DEFAULT 10000,
        min_confidence_to_trade REAL DEFAULT 0.60,
        preferred_directions TEXT DEFAULT '["BUY","HOLD"]',
        max_portfolio_beta REAL DEFAULT 1.2,
        max_daily_orders INTEGER DEFAULT 10,
        is_active INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_expert_recommendations (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        direction TEXT NOT NULL,
        conviction INTEGER NOT NULL,
        source TEXT DEFAULT 'cis',
        narrative TEXT,
        risk_profile TEXT,
        expert_votes TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
]

_DEFAULT_PROFILES = [
    ("conservative", 0.08, 5000,  0.70, ["BUY"],          0.9, 5,  0),
    ("moderate",     0.15, 10000, 0.60, ["BUY", "HOLD"],  1.2, 10, 1),  # active by default
    ("aggressive",   0.25, 25000, 0.50, ["BUY", "SHORT"], 1.5, 20, 0),
    ("custom",       0.15, 10000, 0.60, ["BUY", "HOLD"],  1.2, 10, 0),
]


def _ensure_tables() -> None:
    conn = get_connection()
    try:
        for ddl in _ADVISOR_TABLES:
            conn.execute(ddl)
        # Backfill columns that didn't exist in earlier schema versions.
        # CREATE TABLE IF NOT EXISTS is a no-op when the table already has
        # the older shape, so we explicitly add any missing columns here.
        try:
            conn.execute("ALTER TABLE ad_risk_profiles ADD COLUMN IF NOT EXISTS max_daily_orders INTEGER DEFAULT 10")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        # Seed default profiles if the table is empty
        existing = conn.execute("SELECT COUNT(*) FROM ad_risk_profiles").fetchone()
        if (existing[0] if existing else 0) == 0:
            for name, pos_pct, budget, min_conf, prefs, beta, max_ord, active in _DEFAULT_PROFILES:
                conn.execute(
                    "INSERT INTO ad_risk_profiles "
                    "(id, profile_name, max_position_pct, max_daily_budget, min_confidence_to_trade, "
                    "preferred_directions, max_portfolio_beta, max_daily_orders, is_active) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (f"rp-{uuid.uuid4().hex[:12]}", name, pos_pct, budget, min_conf,
                     json.dumps(prefs), beta, max_ord, active),
                )
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# =========================================================================
# The 6 experts
# =========================================================================

EXPERT_AGENTS = {
    "fundamental": {
        "name": "Warren",
        "style": "value",
        "icon": "💎",
        "philosophy": "Buy wonderful businesses at fair prices. Focus on quality and moats.",
        "risk_profile": "conservative",
        "time_horizon": "3-5 years",
        "reads": "analysts.fundamental + analysts.quality",
    },
    "technical": {
        "name": "Nancy",
        "style": "momentum",
        "icon": "📈",
        "philosophy": "The trend is your friend. Ride momentum until it breaks.",
        "risk_profile": "moderate",
        "time_horizon": "1-8 weeks",
        "reads": "analysts.technical + price action",
    },
    "sentiment": {
        "name": "Howard",
        "style": "contrarian",
        "icon": "🔥",
        "philosophy": "Be fearful when others are greedy, greedy when others are fearful.",
        "risk_profile": "aggressive",
        "time_horizon": "2-6 months",
        "reads": "analysts.sentiment + news divergences",
    },
    "macro": {
        "name": "Ray",
        "style": "top-down",
        "icon": "🌍",
        "philosophy": "All-weather positioning — own the regime, not the narrative.",
        "risk_profile": "moderate",
        "time_horizon": "6-18 months",
        "reads": "macro context + regime + sector sensitivity",
    },
    "quant": {
        "name": "Jim",
        "style": "systematic",
        "icon": "📊",
        "philosophy": "Trust the statistics. Edge is in pattern persistence, not prediction.",
        "risk_profile": "moderate",
        "time_horizon": "1-12 weeks",
        "reads": "composite_score + confluence tier + historical hit rate",
    },
    "risk": {
        "name": "Seth",
        "style": "defensive",
        "icon": "🛡️",
        "philosophy": "Rule #1: never lose money. Rule #2: never forget rule #1.",
        "risk_profile": "conservative",
        "time_horizon": "always",
        "reads": "confidence + drawdown proximity + KG systemic exposure",
    },
}


# =========================================================================
# Data loading helpers
# =========================================================================

def _fetch_latest_signal(ticker: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT ticker, direction, composite_score, confidence, component_scores, status, created_at "
            "FROM ad_signals WHERE ticker = %s ORDER BY created_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("component_scores") and isinstance(d["component_scores"], str):
            try:
                d["component_scores"] = json.loads(d["component_scores"])
            except (json.JSONDecodeError, TypeError):
                d["component_scores"] = {}
        return d
    finally:
        conn.close()


def _fetch_latest_run(ticker: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, result_json, created_at FROM ad_analysis_runs "
            "WHERE ticker = %s AND status = 'completed' "
            "ORDER BY created_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("result_json") and isinstance(d["result_json"], str):
            try:
                d["result_json"] = json.loads(d["result_json"])
            except (json.JSONDecodeError, TypeError):
                d["result_json"] = {}
        return d
    finally:
        conn.close()


def _fetch_macro() -> dict:
    try:
        from tools.trading.data.macro_data import fetch_macro_context
        return fetch_macro_context()
    except Exception:
        return {}


def _fetch_news_impact(ticker: str) -> dict:
    """Recent aggregated news impact for this ticker (last 24h). Safe to
    call before the traces table exists — returns zeros."""
    try:
        from tools.trading.news.impact_store import get_recent_impact
        return get_recent_impact(ticker, hours=24)
    except Exception:
        return {"total_score": 0.0, "trace_count": 0}


def _fetch_geopolitical_danger() -> dict:
    try:
        from tools.trading.news.impact_store import geopolitical_danger_score
        return geopolitical_danger_score(hours=24)
    except Exception:
        return {"danger_score": 0, "country_events": 0}


# =========================================================================
# Per-agent scoring
# =========================================================================

def _dir_from_score(score: float) -> str:
    if score >= 65:
        return "BUY"
    if score >= 55:
        return "HOLD"
    if score <= 35:
        return "SELL"
    return "HOLD"


def _conviction(score: float, confidence: float | None = None) -> int:
    """Map 0-100 score (+ optional confidence) to 1-10 conviction."""
    c = confidence if confidence is not None else 0.5
    # Distance from 50 is directional strength; confidence multiplies
    strength = abs(score - 50) / 5.0   # 0..10
    weighted = strength * (0.5 + c * 0.5)
    return max(1, min(10, int(round(weighted))))


def _score_fundamental(ticker: str, sig: dict | None, run: dict | None, macro: dict) -> dict:
    comps = (sig or {}).get("component_scores") or {}
    analysts = (run or {}).get("result_json", {}).get("layers", {}).get("analysts", {})
    fund = analysts.get("fundamental", {})
    raw = fund.get("score", comps.get("fundamental", 50))
    score = float(raw) if raw is not None else 50.0
    vol = float(fund.get("metrics", {}).get("volatility", 0.3) or 0.3)
    # Warren hates volatility — penalize high-vol names
    score = score - min(15, vol * 50)
    return {
        "direction": _dir_from_score(score),
        "conviction": _conviction(score, (sig or {}).get("confidence", 0.5)),
        "reasoning": fund.get("summary") or f"Score {score:.0f}. Warren weights quality × moat × fair price.",
        "time_horizon": EXPERT_AGENTS["fundamental"]["time_horizon"],
        "debug": {"score": round(score, 1), "volatility": round(vol, 3)},
    }


def _score_technical(ticker: str, sig: dict | None, run: dict | None, macro: dict) -> dict:
    comps = (sig or {}).get("component_scores") or {}
    analysts = (run or {}).get("result_json", {}).get("layers", {}).get("analysts", {})
    tech = analysts.get("technical", {})
    score = float(tech.get("score", comps.get("technical", 50)) or 50)
    signals = tech.get("signals") or []
    return {
        "direction": _dir_from_score(score),
        "conviction": _conviction(score, (sig or {}).get("confidence", 0.5)),
        "reasoning": tech.get("summary") or f"Score {score:.0f}. Signals: {', '.join(signals) or 'none'}.",
        "time_horizon": EXPERT_AGENTS["technical"]["time_horizon"],
        "debug": {"score": round(score, 1), "signals": signals},
    }


def _score_sentiment(ticker: str, sig: dict | None, run: dict | None, macro: dict) -> dict:
    comps = (sig or {}).get("component_scores") or {}
    analysts = (run or {}).get("result_json", {}).get("layers", {}).get("analysts", {})
    s = analysts.get("sentiment", {})
    raw = s.get("score")
    # Sentiment analyst returns -1..1; convert to 0..100 with contrarian flip
    if raw is not None and -1 <= float(raw) <= 1:
        score = (float(raw) * 50) + 50
    else:
        score = float(raw) if raw is not None else comps.get("sentiment", 50)
    # Howard's contrarian twist: extreme consensus direction gets inverted
    if score > 80:
        score = 40 + (score - 80) * 0.5  # froth → fade
    elif score < 20:
        score = 60 - (20 - score) * 0.5  # despair → bid

    # News-driven supply-chain impact nudge. Howard's contrarian streak still
    # applies: if news impact is strongly positive but sentiment is already
    # frothy, fade. If news impact is strongly negative while sentiment is
    # already despair, lean in. Net nudge is capped at ±10 points.
    news = _fetch_news_impact(ticker)
    ni = float(news.get("total_score", 0) or 0)
    news_nudge = 0.0
    news_note = ""
    if abs(ni) >= 1.0:
        base_nudge = max(-10.0, min(10.0, ni * 3.0))
        # Contrarian inversion when at extremes
        if score > 70 and base_nudge > 0:
            base_nudge *= -0.5
        elif score < 30 and base_nudge < 0:
            base_nudge *= -0.5
        news_nudge = base_nudge
        news_note = (f" News nudge {base_nudge:+.1f} "
                     f"(Σ={ni:+.2f} across {news.get('trace_count', 0)} traces).")
    score = max(0, min(100, score + news_nudge))

    return {
        "direction": _dir_from_score(score),
        "conviction": _conviction(score, (sig or {}).get("confidence", 0.5)),
        "reasoning": (
            (s.get("summary") or f"Contrarian-adjusted sentiment {score:.0f}. Howard fades extremes.")
            + news_note
        ),
        "time_horizon": EXPERT_AGENTS["sentiment"]["time_horizon"],
        "debug": {"score": round(score, 1), "news_nudge": round(news_nudge, 2)},
    }


def _score_macro(ticker: str, sig: dict | None, run: dict | None, macro: dict) -> dict:
    regime = macro.get("regime", "NEUTRAL")
    score = float(macro.get("macro_score", 50) or 50)
    # Sector alignment bump
    sector_impacts = macro.get("sector_impacts", {}) or {}
    try:
        from tools.trading.market_intel.universe import get_full_universe
        sector = get_full_universe().get(ticker, "")
        sector_score = sector_impacts.get(sector, {}).get("impact_score", 0)
        score += sector_score * 0.2  # weight sector tailwind into macro view
    except Exception:
        pass
    # Deflation/stagflation regimes push macro away from risk
    sf_flags = (macro.get("stagflation_risk", {}) or {}).get("active_flags", 0)
    df_flags = (macro.get("deflation_risk", {}) or {}).get("active_flags", 0)
    score -= sf_flags * 4 + df_flags * 3

    # Geopolitical news overlay — recent country-subject news events raise
    # Ray's risk aversion globally. Scales up to a 10-point score haircut.
    geo = _fetch_geopolitical_danger()
    geo_danger = int(geo.get("danger_score", 0) or 0)
    geo_penalty = min(10, geo_danger * 0.1)
    score -= geo_penalty

    # Ticker-specific news impact — direct nudge from KG-propagated
    # impact. A ticker pulled by news from both country and commodity
    # subjects gets the strongest weighting.
    news = _fetch_news_impact(ticker)
    ni = float(news.get("total_score", 0) or 0)
    news_bump = max(-8.0, min(8.0, ni * 2.0))
    score += news_bump

    score = max(0, min(100, score))
    notes = []
    if sf_flags:
        notes.append(f"Stagflation {sf_flags}/4")
    if df_flags:
        notes.append(f"Deflation {df_flags}/4")
    if geo_danger >= 30:
        notes.append(f"Geo-news danger {geo_danger}/100")
    if abs(ni) >= 1.0:
        notes.append(f"news impact {ni:+.2f}")
    return {
        "direction": _dir_from_score(score),
        "conviction": _conviction(score),
        "reasoning": (
            f"Regime: {regime}. Macro score {score:.0f}. "
            + ("; ".join(notes) + ". " if notes else "")
            + "Ray positions for the regime, not the narrative."
        ),
        "time_horizon": EXPERT_AGENTS["macro"]["time_horizon"],
        "debug": {
            "regime": regime, "score": round(score, 1),
            "sf_flags": sf_flags, "df_flags": df_flags,
            "geo_penalty": round(geo_penalty, 2), "news_bump": round(news_bump, 2),
        },
    }


def _score_quant(ticker: str, sig: dict | None, run: dict | None, macro: dict) -> dict:
    score = float((sig or {}).get("composite_score", 50) or 50)
    conf = float((sig or {}).get("confidence", 0.5) or 0.5)
    return {
        "direction": (sig or {}).get("direction") or _dir_from_score(score),
        "conviction": _conviction(score, conf),
        "reasoning": f"Composite {score:.0f} @ {conf*100:.0f}% confidence. Jim trusts the signal as scored.",
        "time_horizon": EXPERT_AGENTS["quant"]["time_horizon"],
        "debug": {"composite_score": round(score, 1), "confidence": round(conf, 3)},
    }


def _score_risk(ticker: str, sig: dict | None, run: dict | None, macro: dict) -> dict:
    conf = float((sig or {}).get("confidence", 0.5) or 0.5)
    # Risk-off bias unless confidence is strong AND macro is supportive
    macro_score = float(macro.get("macro_score", 50) or 50)
    raw = conf * 100 * 0.6 + macro_score * 0.4
    # Big danger signals push toward SELL
    regime = macro.get("regime", "")
    if regime in ("CRISIS", "DANGER", "DEFLATION", "STAGFLATION", "RED"):
        raw -= 20
    return {
        "direction": _dir_from_score(raw) if raw >= 55 else "HOLD" if raw >= 45 else "SELL",
        "conviction": _conviction(raw, conf),
        "reasoning": f"Defensive read — conf {conf*100:.0f}%, macro {macro_score:.0f}, regime {regime or 'n/a'}. Seth protects capital first.",
        "time_horizon": EXPERT_AGENTS["risk"]["time_horizon"],
        "debug": {"raw": round(raw, 1), "regime": regime},
    }


_SCORERS = {
    "fundamental": _score_fundamental,
    "technical": _score_technical,
    "sentiment": _score_sentiment,
    "macro": _score_macro,
    "quant": _score_quant,
    "risk": _score_risk,
}


# =========================================================================
# Public API
# =========================================================================

def run_expert_analysis(ticker: str) -> list[dict]:
    """Run all 6 expert agents on a ticker; return a list of opinion dicts.

    Each opinion has the shape consumed by judge.run_socratic_analysis:
      {expert_key, expert_name, icon, direction, conviction, reasoning,
       risk_profile, time_horizon, style, debug}

    If there is no latest signal AND no completed analysis run for the ticker,
    returns [{"error": "..."}] so judge can short-circuit cleanly.
    """
    _ensure_tables()
    ticker = ticker.upper()

    sig = _fetch_latest_signal(ticker)
    run = _fetch_latest_run(ticker)
    macro = _fetch_macro()

    if sig is None and run is None:
        return [{"error": f"No signal or analysis run found for {ticker}. "
                          f"Run /analysis on this ticker first."}]

    opinions = []
    for key, meta in EXPERT_AGENTS.items():
        scorer = _SCORERS.get(key)
        try:
            r = scorer(ticker, sig, run, macro) if scorer else {}
        except Exception as e:
            r = {"direction": "HOLD", "conviction": 1,
                 "reasoning": f"Error: {e}", "time_horizon": meta["time_horizon"]}
        opinions.append({
            "expert_key": key,
            "expert_name": meta["name"],
            "icon": meta["icon"],
            "style": meta["style"],
            "philosophy": meta["philosophy"],
            "risk_profile": meta["risk_profile"],
            **r,
        })

    # Persist opinions for audit
    conn = get_connection()
    try:
        for op in opinions:
            conn.execute(
                "INSERT INTO ad_expert_opinions "
                "(id, ticker, expert_key, expert_name, direction, conviction, reasoning, risk_profile, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (f"op-{uuid.uuid4().hex[:12]}", ticker, op["expert_key"], op["expert_name"],
                 op["direction"], op["conviction"], op["reasoning"], op["risk_profile"], _now()),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("run_expert_analysis: best-effort INSERT into ad_expert_opinions failed (non-blocking): %s", exc)
    finally:
        conn.close()

    return opinions


def synthesize_recommendation(ticker: str, opinions: list[dict]) -> dict:
    """CIS synthesis: weighted vote across 6 (possibly revised) opinions.

    Each agent's vote is weighted by the active risk profile's alignment with
    the agent's own risk profile (matching profiles get 1.5x weight).
    Ties resolve toward HOLD.
    """
    _ensure_tables()
    active = _get_active_risk_profile()
    active_name = active.get("profile_name", "moderate")

    # Map direction -> weighted score
    weighted: dict[str, float] = {"BUY": 0.0, "SELL": 0.0, "SHORT": 0.0, "HOLD": 0.0}
    conviction_sum = 0.0
    weight_sum = 0.0
    votes = []
    for op in opinions:
        w = 1.5 if op.get("risk_profile") == active_name else 1.0
        conviction = float(op.get("conviction", 1))
        weighted[op.get("direction", "HOLD")] = weighted.get(op.get("direction", "HOLD"), 0) + conviction * w
        conviction_sum += conviction * w
        weight_sum += w
        votes.append({
            "expert_key": op.get("expert_key"),
            "expert_name": op.get("expert_name"),
            "direction": op.get("direction"),
            "conviction": op.get("conviction"),
            "weight": w,
        })

    # Winner
    final_direction = max(weighted.items(), key=lambda kv: kv[1])[0]
    if weighted[final_direction] == 0:
        final_direction = "HOLD"

    # Agreement: fraction of experts that voted the winning side
    agreement_count = sum(1 for op in opinions if op.get("direction") == final_direction)
    agreement_pct = agreement_count / max(1, len(opinions))

    # Final conviction: scaled average × agreement factor
    avg_conv = (conviction_sum / weight_sum) if weight_sum else 1
    final_conviction = int(round(max(1, min(10, avg_conv * (0.5 + agreement_pct / 2)))))

    narrative = (
        f"{agreement_count}/{len(opinions)} experts aligned on {final_direction} "
        f"(conviction {final_conviction}/10, profile {active_name}). "
        + (
            "Strong consensus." if agreement_pct >= 0.83
            else "Moderate consensus." if agreement_pct >= 0.5
            else "Split committee — proceed cautiously."
        )
    )

    rec_id = f"cis-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO ad_cis_recommendations "
            "(id, ticker, final_direction, final_conviction, expert_votes, narrative, auto_trade, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (rec_id, ticker, final_direction, final_conviction,
             json.dumps(votes), narrative, 1 if final_conviction >= 6 and final_direction == "BUY" else 0,
             _now()),
        )
        # Also log to legacy expert_recommendations for /advisor page compatibility
        conn.execute(
            "INSERT INTO ad_expert_recommendations "
            "(id, ticker, direction, conviction, source, narrative, risk_profile, expert_votes, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"er-{uuid.uuid4().hex[:12]}", ticker, final_direction, final_conviction,
             "cis", narrative, active_name, json.dumps(votes), _now()),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "synthesize_recommendation: best-effort INSERT into ad_cis_recommendations failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()

    return {
        "id": rec_id,
        "ticker": ticker,
        "final_direction": final_direction,
        "final_conviction": final_conviction,
        "agreement": f"{agreement_count}/{len(opinions)}",
        "agreement_pct": round(agreement_pct, 2),
        "expert_votes": votes,
        "narrative": narrative,
        "risk_profile": active_name,
        "auto_trade_eligible": final_conviction >= 6 and final_direction == "BUY",
    }


# =========================================================================
# Risk profile management
# =========================================================================

def _get_active_risk_profile() -> dict:
    """Return the currently active risk profile as a dict. Falls back to
    a hardcoded moderate profile if the table is unreachable."""
    try:
        _ensure_tables()
        conn = get_connection()
        row = conn.execute(
            "SELECT profile_name, max_position_pct, max_daily_budget, "
            "min_confidence_to_trade, preferred_directions, max_portfolio_beta, "
            "max_daily_orders FROM ad_risk_profiles WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            d = dict(row)
            if isinstance(d.get("preferred_directions"), str):
                try:
                    d["preferred_directions"] = json.loads(d["preferred_directions"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return d
    except Exception:
        pass
    return {
        "profile_name": "moderate",
        "max_position_pct": 0.15,
        "max_daily_budget": 10000,
        "min_confidence_to_trade": 0.60,
        "preferred_directions": ["BUY", "HOLD"],
        "max_portfolio_beta": 1.2,
        "max_daily_orders": 10,
    }


def get_risk_profiles() -> list[dict]:
    _ensure_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT profile_name, max_position_pct, max_daily_budget, "
            "min_confidence_to_trade, preferred_directions, max_portfolio_beta, "
            "max_daily_orders, is_active FROM ad_risk_profiles ORDER BY profile_name"
        ).fetchall()
        profiles = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("preferred_directions"), str):
                try:
                    d["preferred_directions"] = json.loads(d["preferred_directions"])
                except (json.JSONDecodeError, TypeError):
                    pass
            d["is_active"] = bool(d.get("is_active"))
            profiles.append(d)
        return profiles
    finally:
        conn.close()


def set_active_risk_profile(name: str) -> dict:
    _ensure_tables()
    conn = get_connection()
    try:
        # Check target exists
        row = conn.execute(
            "SELECT profile_name FROM ad_risk_profiles WHERE profile_name = %s", (name,)
        ).fetchone()
        if not row:
            return {"error": f"profile '{name}' does not exist",
                    "available": [p["profile_name"] for p in get_risk_profiles()]}
        conn.execute("UPDATE ad_risk_profiles SET is_active = 0")
        conn.execute("UPDATE ad_risk_profiles SET is_active = 1, updated_at = %s WHERE profile_name = %s",
                     (_now(), name))
        conn.commit()
        return {"activated": name, "status": "ok"}
    finally:
        conn.close()


# =========================================================================
# Daily brief
# =========================================================================

def generate_daily_brief() -> dict:
    """Produce a morning brief snapshot: market state, top ideas, alerts.

    Pulls from already-computed tables — no LLM calls, so the dashboard can
    load fast. Stored in ad_daily_briefs for historical lookback.
    """
    _ensure_tables()
    macro = _fetch_macro()
    conn = get_connection()
    try:
        # Top 5 BUY signals by composite_score
        top_buys = conn.execute(
            "SELECT s.ticker, s.direction, s.composite_score, s.confidence "
            "FROM ad_signals s INNER JOIN ("
            "  SELECT ticker, MAX(created_at) mx FROM ad_signals GROUP BY ticker"
            ") latest ON s.ticker = latest.ticker AND s.created_at = latest.mx "
            "WHERE s.direction = 'BUY' AND s.composite_score >= 65 "
            "ORDER BY s.composite_score DESC LIMIT 5"
        ).fetchall()

        # Top 5 SELL / SHORT signals
        top_sells = conn.execute(
            "SELECT s.ticker, s.direction, s.composite_score, s.confidence "
            "FROM ad_signals s INNER JOIN ("
            "  SELECT ticker, MAX(created_at) mx FROM ad_signals GROUP BY ticker"
            ") latest ON s.ticker = latest.ticker AND s.created_at = latest.mx "
            "WHERE s.direction IN ('SELL', 'SHORT') AND s.composite_score <= 35 "
            "ORDER BY s.composite_score ASC LIMIT 5"
        ).fetchall()

        # Latest CIS recs
        latest_cis = conn.execute(
            "SELECT ticker, final_direction, final_conviction, narrative "
            "FROM ad_cis_recommendations ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
    finally:
        conn.close()

    # Risk alerts from macro composites
    risk_alerts = []
    sf = (macro.get("stagflation_risk") or {})
    if sf.get("active_flags", 0) >= 2:
        risk_alerts.append(f"Stagflation risk {sf.get('risk_level', '?')} — {sf.get('active_flags')}/4 flags")
    df = (macro.get("deflation_risk") or {})
    if df.get("active_flags", 0) >= 2:
        risk_alerts.append(f"Deflation risk {df.get('risk_level', '?')} — {df.get('active_flags')}/4 flags")
    geo = (macro.get("geopolitical_risk") or {})
    if geo.get("score", 0) >= 50:
        risk_alerts.append(f"Geopolitical risk {geo.get('risk_level', '?')}")

    brief = {
        "brief_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "market_summary": macro.get("summary") or f"Regime: {macro.get('regime', 'UNKNOWN')}, score {macro.get('macro_score', 0)}/100",
        "regime": macro.get("regime"),
        "macro_score": macro.get("macro_score"),
        "top_ideas": [dict(r) for r in top_buys],
        "watchlist": [dict(r) for r in top_sells],
        "risk_alerts": risk_alerts,
        "expert_highlights": [dict(r) for r in latest_cis],
        "generated_at": _now(),
    }

    # Persist
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO ad_daily_briefs "
            "(id, brief_date, market_summary, top_ideas, watchlist, risk_alerts, expert_highlights) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (f"db-{uuid.uuid4().hex[:12]}", brief["brief_date"], brief["market_summary"],
             json.dumps(brief["top_ideas"]), json.dumps(brief["watchlist"]),
             json.dumps(brief["risk_alerts"]), json.dumps(brief["expert_highlights"])),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("generate_daily_brief: best-effort INSERT into ad_daily_briefs failed (non-blocking): %s", exc)
    finally:
        conn.close()

    return brief


# =========================================================================
# Latest recs helper (kept for dashboard /api/advisor/recommendations)
# =========================================================================

def get_latest_recommendations(limit: int = 10) -> list[dict]:
    """Return the most recent expert recommendations (CIS-tier)."""
    _ensure_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker, direction, conviction, source, narrative, risk_profile, created_at "
            "FROM ad_expert_recommendations ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]
        # Fall back to ad_cis_recommendations if no rows yet
        rows = conn.execute(
            "SELECT ticker, final_direction AS direction, final_conviction AS conviction, "
            "'cis' AS source, narrative, created_at "
            "FROM ad_cis_recommendations ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    # CLI smoke test
    import sys
    if len(sys.argv) > 1:
        t = sys.argv[1].upper()
        ops = run_expert_analysis(t)
        print(json.dumps(ops, indent=2, default=str))
        rec = synthesize_recommendation(t, ops)
        print()
        print(json.dumps(rec, indent=2, default=str))
    else:
        brief = generate_daily_brief()
        print(json.dumps(brief, indent=2, default=str))
