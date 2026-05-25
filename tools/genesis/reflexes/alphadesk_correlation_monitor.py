"""Genesis Reflex: AlphaDesk Supply Chain Correlation Monitor.

Runs on a 2-hour cadence. Detects:
  1. Cluster divergence — tickers within the same supply chain cluster moving
     in opposite directions (leading indicator of a sector breakdown or rotation).
  2. Monetary catalyst activation — when cheap money, QE, buyback, or TINA
     conditions flip active, emits a GKP insight artifact.
  3. Lead-lag signal — when the lead ticker in a cluster flips direction,
     emits an early-warning for the lag tickers.

Reflex tier: GREEN (auto-approved, no sandbox).
Cooldown: 2 hours per catalyst (prevents alert fatigue).
"""
IMPLEMENTATION_STATUS = "full"

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


_COOLDOWN_HOURS = 2
_MIN_CONFIDENCE = 0.70


def run(context: dict, session) -> dict:
    """Entry point called by Genesis daemon."""
    now = datetime.now(timezone.utc)
    results = []

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

        # ── 1. Monetary Catalyst Activation Detection ─────────────────────
        cat_result = score_monetary_catalysts(macro_raw)
        for cat_id, cat_data in cat_result.get("catalysts", {}).items():
            if not cat_data["is_active"]:
                continue
            if cat_data["strength"] not in ("medium", "high"):
                continue
            if not _check_cooldown(conn, f"monetary_catalyst:{cat_id}", _COOLDOWN_HOURS):
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
            if not _check_cooldown(conn, f"cluster_diverge:{cluster_id}", _COOLDOWN_HOURS):
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

            # Get lead ticker latest direction
            row = conn.execute(
                "SELECT direction FROM ad_signals WHERE ticker = ? "
                "AND status = 'approved' ORDER BY created_at DESC LIMIT 1",
                (lead,),
            ).fetchone()
            if not row:
                continue
            lead_direction = row[0]

            # Check lag tickers that haven't followed yet
            lagging = []
            for lag in lag_tickers[:4]:
                lag_row = conn.execute(
                    "SELECT direction FROM ad_signals WHERE ticker = ? "
                    "AND status = 'approved' ORDER BY created_at DESC LIMIT 1",
                    (lag,),
                ).fetchone()
                if lag_row and lag_row[0] != lead_direction:
                    lagging.append(lag)

            if len(lagging) >= 2:
                cooldown_key = f"lead_lag:{cluster_id}:{lead_direction}"
                if not _check_cooldown(conn, cooldown_key, _COOLDOWN_HOURS * 2):
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
            _emit_gkp(r["gkp_summary"], r["type"])
            gkp_count += 1
        except Exception:
            pass

    return {
        "status": "ok",
        "detections": len(results),
        "gkp_artifacts_emitted": gkp_count,
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
            "SELECT value FROM ad_reflex_cooldowns WHERE key = ? AND value > ?",
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
            "INSERT OR REPLACE INTO ad_reflex_cooldowns (key, value) VALUES (?, ?)",
            (key, now.isoformat()),
        )
        conn.commit()
    except Exception:
        pass


def _emit_gkp(summary: str, artifact_type: str):
    """Emit a Genesis Knowledge Promotion artifact."""
    try:
        from tools.genesis.knowledge_bridge import emit_gkp_artifact
        emit_gkp_artifact(
            content=summary,
            artifact_type=f"correlation_{artifact_type}",
            confidence=_MIN_CONFIDENCE,
            source="alphadesk_correlation_monitor",
            tags=["fathomdesk", "correlation", "supply_chain", "monetary"],
        )
    except Exception:
        pass
