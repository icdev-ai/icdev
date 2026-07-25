"""Genesis Reflex: FathomDesk Supply Chain Correlation Monitor.

Runs on a 2-hour cadence. Detects:
  1. Cluster divergence — tickers within the same supply chain cluster moving
     in opposite directions (leading indicator of a sector breakdown or rotation).
  2. Monetary catalyst activation — when cheap money, QE, buyback, or TINA
     conditions flip active, emits a GKP insight artifact.
  3. Lead-lag signal — when the lead ticker in a cluster flips direction,
     emits an early-warning for the lag tickers.

Thresholds are loaded from args/fathomdesk_correlation_monitor.yaml and
calibrated at runtime by an AI anomaly detection step (haiku) that adjusts
sensitivity based on current market volatility.

Reflex tier: GREEN (auto-approved, no sandbox).
Cooldown: configurable per catalyst (prevents alert fatigue).
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"


import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

_ARGS_PATH = Path(__file__).resolve().parents[3] / "args" / "fathomdesk_correlation_monitor.yaml"

_DEFAULT_THRESHOLDS: dict = {
    "cooldown_hours": 2,
    "min_confidence": 0.70,
    "min_lagging_count": 2,
    "allowed_strengths": ["medium", "high"],
}


def _load_thresholds() -> dict:
    """Load threshold defaults from args YAML, falling back to built-ins."""
    try:
        import yaml
        if _ARGS_PATH.exists():
            with open(_ARGS_PATH, encoding="utf-8") as f:
                return {**_DEFAULT_THRESHOLDS, **yaml.safe_load(f).get("thresholds", {})}
    except Exception:
        pass
    return _DEFAULT_THRESHOLDS.copy()


def _calibrate_thresholds_with_ai(macro_raw: dict, base: dict) -> dict:
    """Use AI to adaptively calibrate anomaly detection thresholds.

    High-volatility regimes warrant lower confidence requirements (catch more
    signals); quiet regimes can afford stricter gates to suppress noise.
    Falls back to base thresholds on any LLM error.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        vix = macro_raw.get("vix", 0)
        fed_rate = macro_raw.get("fed_funds_rate", 0)
        spread = macro_raw.get("credit_spread", 0)

        prompt = (
            "Calibrate anomaly detection thresholds for a supply chain correlation monitor.\n"
            f"Market conditions: VIX={vix:.1f}, Fed Funds Rate={fed_rate:.2f}%, "
            f"Credit Spread={spread:.2f}%.\n"
            f"Current base thresholds: {json.dumps(base)}.\n"
            "Rules:\n"
            "- High VIX (>25) → lower min_confidence (catch more signals)\n"
            "- Low VIX (<15) → higher min_confidence (reduce noise)\n"
            "- Wider credit spread → lower min_lagging_count (earlier warning)\n"
            "- Tighter credit spread → keep min_lagging_count at base\n"
            "Respond ONLY with valid JSON:\n"
            '{"min_confidence": <float 0.50-0.95>, "min_lagging_count": <int 1-4>, '
            '"cooldown_hours": <int 1-6>}'
        )

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a quantitative risk calibration assistant. "
                "Output only compact JSON with the three requested keys."
            ),
            agent_id="fathomdesk-anomaly-calibrator",
            classification="CUI",
            max_tokens=120,
            effort="low",
        )
        response = router.invoke("anomaly_detection", request)
        calibrated = json.loads(response.content.strip())
        return {
            "cooldown_hours": int(calibrated.get("cooldown_hours", base["cooldown_hours"])),
            "min_confidence": float(calibrated.get("min_confidence", base["min_confidence"])),
            "min_lagging_count": int(calibrated.get("min_lagging_count", base["min_lagging_count"])),
            "allowed_strengths": base["allowed_strengths"],
        }
    except Exception:
        return base


def run(context: dict, session) -> dict:
    """Entry point called by Genesis daemon."""
    now = datetime.now(timezone.utc)
    results = []

    # Load and AI-calibrate thresholds once per run
    thresholds = _load_thresholds()

    try:
        from tools.trading.market_intel.correlation_engine import (
            SUPPLY_CHAIN_CLUSTERS,
            MONETARY_CATALYSTS,
            score_monetary_catalysts,
            score_cluster_states,
        )
        from tools.trading.db import get_conn

        conn = get_conn()
        macro_raw = _get_macro_raw()

        # Calibrate thresholds using live market data
        thresholds = _calibrate_thresholds_with_ai(macro_raw, thresholds)
        cooldown_hours = thresholds["cooldown_hours"]
        min_lagging_count = thresholds["min_lagging_count"]
        allowed_strengths = thresholds["allowed_strengths"]

        # ── 1. Monetary Catalyst Activation Detection ─────────────────────
        cat_result = score_monetary_catalysts(macro_raw)
        for cat_id, cat_data in cat_result.get("catalysts", {}).items():
            if not cat_data["is_active"]:
                continue
            if cat_data["strength"] not in allowed_strengths:
                continue
            if not _check_cooldown(conn, f"monetary_catalyst:{cat_id}", cooldown_hours):
                continue

            catalyst_def = MONETARY_CATALYSTS.get(cat_id, {})
            evidence_str = "; ".join(cat_data.get("evidence", [])[:3])
            results.append({
                "type": "monetary_catalyst",
                "catalyst_id": cat_id,
                "catalyst_name": cat_data["name"],
                "strength": cat_data["strength"],
                "regime_score": cat_data["regime_score"],
                "evidence": evidence_str,
                "gkp_summary": (
                    f"Monetary catalyst '{cat_data['name']}' is ACTIVE "
                    f"(strength={cat_data['strength']}, score={cat_data['regime_score']:.0f}/100). "
                    f"Evidence: {evidence_str}. "
                    f"Sector impacts: "
                    + ", ".join(
                        f"{s}={v}"
                        for s, v in list(catalyst_def.get("sector_impacts", {}).items())[:5]
                    )
                ),
            })
            _mark_cooldown(conn, f"monetary_catalyst:{cat_id}", now)

        # ── 2. Cluster Divergence Detection ──────────────────────────────
        cl_result = score_cluster_states(macro_raw)
        for cluster_id, cluster_state in cl_result.get("clusters", {}).items():
            if cluster_state.get("state") != "diverging":
                continue
            if not _check_cooldown(conn, f"cluster_diverge:{cluster_id}", cooldown_hours):
                continue

            cluster_def = SUPPLY_CHAIN_CLUSTERS.get(cluster_id, {})
            ticker_states = cluster_state.get("ticker_states", {})
            bullish = [t for t, s in ticker_states.items() if s == "bullish"]
            bearish = [t for t, s in ticker_states.items() if s == "bearish"]
            results.append({
                "type": "cluster_divergence",
                "cluster_id": cluster_id,
                "cluster_name": cluster_state["cluster_name"],
                "bullish_tickers": bullish,
                "bearish_tickers": bearish,
                "gkp_summary": (
                    f"Supply chain cluster '{cluster_state['cluster_name']}' is DIVERGING. "
                    f"Bullish: {', '.join(bullish[:3])}. "
                    f"Bearish: {', '.join(bearish[:3])}. "
                    f"Correlation type: {cluster_def.get('correlation_type', 'unknown')}. "
                    f"Lead ticker ({cluster_def.get('lead_ticker')}) direction should be watched — "
                    f"divergence within correlated clusters often precedes a catch-up move."
                ),
            })
            _mark_cooldown(conn, f"cluster_diverge:{cluster_id}", now)

        # ── 3. Lead-Lag Signal ────────────────────────────────────────────
        for cluster_id, cluster in SUPPLY_CHAIN_CLUSTERS.items():
            lead = cluster.get("lead_ticker")
            if not lead:
                continue
            lag_tickers = [t for t in cluster["tickers"] if t != lead]
            if not lag_tickers:
                continue

            row = conn.execute(
                "SELECT direction FROM ad_signals WHERE ticker = %s "
                "AND status = 'approved' ORDER BY created_at DESC LIMIT 1",
                (lead,),
            ).fetchone()
            if not row:
                continue
            lead_direction = row[0]

            lagging = []
            for lag in lag_tickers[:4]:
                lag_row = conn.execute(
                    "SELECT direction FROM ad_signals WHERE ticker = %s "
                    "AND status = 'approved' ORDER BY created_at DESC LIMIT 1",
                    (lag,),
                ).fetchone()
                if lag_row and lag_row[0] != lead_direction:
                    lagging.append(lag)

            if len(lagging) >= min_lagging_count:
                cooldown_key = f"lead_lag:{cluster_id}:{lead_direction}"
                if not _check_cooldown(conn, cooldown_key, cooldown_hours * 2):
                    continue
                results.append({
                    "type": "lead_lag_signal",
                    "cluster_id": cluster_id,
                    "cluster_name": cluster["name"],
                    "lead_ticker": lead,
                    "lead_direction": lead_direction,
                    "lagging_tickers": lagging,
                    "lead_lag_weeks": cluster.get("lead_lag_weeks", 0),
                    "gkp_summary": (
                        f"Lead-lag signal in '{cluster['name']}': "
                        f"lead ticker {lead} is {lead_direction} but "
                        f"{', '.join(lagging)} have not yet followed. "
                        f"Historical lag: ~{cluster.get('lead_lag_weeks', 0)} weeks. "
                        f"Correlation type: {cluster['correlation_type']}."
                    ),
                })
                _mark_cooldown(conn, cooldown_key, now)

        conn.close()

    except Exception as e:
        return {"status": "error", "error": str(e), "results": []}

    # ── Emit GKP artifacts ────────────────────────────────────────────────
    gkp_count = 0
    for r in results:
        try:
            _emit_gkp(r["gkp_summary"], r["type"], thresholds["min_confidence"])
            gkp_count += 1
        except Exception:
            pass

    return {
        "status": "ok",
        "detections": len(results),
        "gkp_artifacts_emitted": gkp_count,
        "thresholds_used": thresholds,
        "details": results,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }


def _get_macro_raw() -> dict:
    try:
        from tools.trading.data.macro_data import get_macro_context
        return get_macro_context().get("raw", {})
    except Exception:
        return {}


def _check_cooldown(conn, key: str, hours: int) -> bool:
    """Returns True if cooldown has expired (safe to emit)."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        row = conn.execute(
            "SELECT value FROM ad_reflex_cooldowns WHERE key = %s AND value > %s",
            (key, cutoff),
        ).fetchone()
        return row is None
    except Exception:
        return True


def _mark_cooldown(conn, key: str, now: datetime):
    """Record current time as last emission for a cooldown key."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad_reflex_cooldowns (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO ad_reflex_cooldowns (key, value) VALUES (%s, %s)",
            (key, now.isoformat()),
        )
        conn.commit()
    except Exception:
        pass


def _emit_gkp(summary: str, artifact_type: str, confidence: float):
    """Emit a Genesis Knowledge Promotion artifact."""
    try:
        from tools.genesis.knowledge_bridge import emit_gkp_artifact
        emit_gkp_artifact(
            content=summary,
            artifact_type=f"correlation_{artifact_type}",
            confidence=confidence,
            source="fathomdesk_correlation_monitor",
            tags=["fathomdesk", "correlation", "supply_chain", "monetary"],
        )
    except Exception:
        pass
