#!/usr/bin/env python3
# CUI // SP-CTI
"""OSINT Predictive Analysis Engine — conflict monitoring and leadership briefings.

Ingests the OSINT data mesh (sg_raw_signals, sg_prioritized_signals,
sg_conflict_events) and produces:
  - PMESII-PT Weighted Risk Index (WRI) from live data
  - Bayesian escalation probability (p_war_posterior)
  - Multi-horizon forecasts: 24h / 72h / 7d
  - Threat tier classification: LOW / MEDIUM / HIGH / CRITICAL
  - Leadership brief record saved to sg_leadership_briefs
  - Optional SITREP saved to sg_intelligence_briefs

Usage
-----
    from tools.strategos.predictive_analysis import PredictiveAnalysisEngine

    engine = PredictiveAnalysisEngine()
    result = engine.run(theater="global")
    print(result["brief_id"], result["threat_tier"], result["wri"])
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from tools.db.storage import get_connection, is_pg
from tools.strategos.iw_engine import PMESIIPTCompositor


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THREAT_TIERS = [
    (0,   30,  "LOW",      "#10b981"),
    (30,  55,  "MEDIUM",   "#f59e0b"),
    (55,  75,  "HIGH",     "#f97316"),
    (75, 101,  "CRITICAL", "#ef4444"),
]

# Goldstein scale: negative = conflictual. Threshold below -3 triggers IW flag.
_GOLDSTEIN_IW_THRESHOLD = -3.0

# Signal velocity window: signals created in last 24h vs prev 24h
_VELOCITY_WINDOW_H = 24


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _safe_query(conn, sql: str, params=()) -> list[dict]:
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def _safe_scalar(conn, sql: str, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return default


def _ph() -> str:
    return "%s" if is_pg() else "?"


def _threat_tier(wri: float) -> tuple[str, str]:
    for lo, hi, label, color in THREAT_TIERS:
        if lo <= wri < hi:
            return label, color
    return "CRITICAL", "#ef4444"


# ---------------------------------------------------------------------------
# Predictive Analysis Engine
# ---------------------------------------------------------------------------

class PredictiveAnalysisEngine:
    """Derives predictions from the live OSINT data mesh.

    All reads use existing sg_* tables.  Falls back gracefully when tables
    are empty or not yet created.
    """

    def __init__(self) -> None:
        self._compositor = PMESIIPTCompositor()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, theater: str = "global") -> dict[str, Any]:
        """Execute a full predictive analysis cycle and persist results.

        Returns a dict with: brief_id, theater, wri, escalation_rung,
        rung_label, threat_tier, threat_color, p_war_posterior,
        signal_count_24h, conflict_event_count, signal_velocity,
        goldstein_avg, dti_score, iw_triggered, forecasts, generated_at.
        """
        conn = get_connection()
        try:
            return self._run(conn, theater)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _run(self, conn, theater: str) -> dict[str, Any]:
        now = _now_utc()
        cutoff_24h = _iso(now - timedelta(hours=24))
        cutoff_48h = _iso(now - timedelta(hours=48))
        ph = _ph()

        # 1. Signal counts (data mesh volume metrics)
        sig_24h = _safe_scalar(
            conn,
            f"SELECT COUNT(*) FROM sg_raw_signals WHERE created_at >= {ph}",
            (cutoff_24h,),
        )
        sig_48h = _safe_scalar(
            conn,
            f"SELECT COUNT(*) FROM sg_raw_signals WHERE created_at >= {ph} AND created_at < {ph}",
            (cutoff_48h, cutoff_24h),
        )
        signal_velocity = round(
            ((sig_24h - sig_48h) / max(sig_48h, 1)) * 100, 2
        )

        # 2. Conflict event metrics
        conflict_count = _safe_scalar(
            conn,
            f"SELECT COUNT(*) FROM sg_conflict_events WHERE date >= {ph}",
            (cutoff_24h[:10],),
        )
        goldstein_avg = _safe_scalar(
            conn,
            f"SELECT AVG(goldstein_scale) FROM sg_conflict_events WHERE date >= {ph}",
            (cutoff_24h[:10],),
            default=0.0,
        ) or 0.0

        iw_triggered = goldstein_avg < _GOLDSTEIN_IW_THRESHOLD

        # 3. PMESII-PT WRI from live DB state
        wri_result = self._compositor.derive_from_db()
        wri = wri_result["wri"]
        escalation_rung = wri_result["escalation_rung"]
        rung_label = wri_result["rung_label"]

        # 4. Threat tier
        threat_tier, threat_color = _threat_tier(wri)

        # 5. Bayesian escalation probability
        p_war = self._compute_p_war(
            wri=wri,
            goldstein_avg=goldstein_avg,
            signal_count_24h=sig_24h,
            signal_velocity=signal_velocity,
            iw_triggered=iw_triggered,
        )

        # 6. DTI score (Decision-to-Impact window in hours)
        dti_score = self._compute_dti(wri, escalation_rung, p_war)

        # 7. Multi-horizon forecasts
        forecasts = self._generate_forecasts(
            wri=wri,
            p_war=p_war,
            signal_velocity=signal_velocity,
            goldstein_avg=goldstein_avg,
        )

        # 8. Top signals for narrative context
        top_signals = _safe_query(
            conn,
            f"SELECT r.title, r.source, r.signal_date, p.composite_score "
            f"FROM sg_prioritized_signals p "
            f"LEFT JOIN sg_raw_signals r ON r.id = p.raw_signal_id "
            f"WHERE p.created_at >= {ph} "
            f"ORDER BY p.composite_score DESC LIMIT 5",
            (cutoff_24h,),
        )

        # 9. Generate narrative
        narrative_md = self._build_narrative(
            theater=theater,
            wri=wri,
            rung_label=rung_label,
            threat_tier=threat_tier,
            p_war=p_war,
            dti_score=dti_score,
            goldstein_avg=goldstein_avg,
            signal_count_24h=sig_24h,
            signal_velocity=signal_velocity,
            iw_triggered=iw_triggered,
            top_signals=top_signals,
            forecasts=forecasts,
            generated_at=_iso(now),
        )

        # 10. Persist to sg_leadership_briefs
        brief_id = self._save_brief(
            conn=conn,
            theater=theater,
            wri=wri,
            threat_tier=threat_tier,
            escalation_rung=escalation_rung,
            iw_triggered=iw_triggered,
            sig_24h=sig_24h,
            conflict_count=conflict_count,
            signal_velocity=signal_velocity,
            p_war=p_war,
            goldstein_avg=goldstein_avg,
            dti_score=dti_score,
            forecasts=forecasts,
            narrative_md=narrative_md,
            generated_at=_iso(now),
        )

        return {
            "brief_id":            brief_id,
            "theater":             theater,
            "wri":                 wri,
            "escalation_rung":     escalation_rung,
            "rung_label":          rung_label,
            "threat_tier":         threat_tier,
            "threat_color":        threat_color,
            "p_war_posterior":     p_war,
            "dti_score":           dti_score,
            "signal_count_24h":    sig_24h,
            "conflict_event_count": conflict_count,
            "signal_velocity":     signal_velocity,
            "goldstein_avg":       round(goldstein_avg, 3),
            "iw_triggered":        iw_triggered,
            "forecasts":           forecasts,
            "narrative_md":        narrative_md,
            "generated_at":        _iso(now),
        }

    # ------------------------------------------------------------------
    # Bayesian escalation probability
    # ------------------------------------------------------------------

    def _compute_p_war(
        self,
        wri: float,
        goldstein_avg: float,
        signal_count_24h: int,
        signal_velocity: float,
        iw_triggered: bool,
    ) -> float:
        """Compute posterior P(war escalation | current observations).

        Uses a logistic function over a composite evidence score, grounded in:
        - WRI (primary structural pressure, 0-100)
        - Goldstein scale average (negative = conflictual)
        - Signal velocity (surge = latent event)
        - IW trigger flag
        """
        # Base prior: 5% (routine periods)
        prior_log_odds = math.log(0.05 / 0.95)

        # Evidence contributions (log-odds updates)
        wri_update         = (wri / 100) * 2.5           # max +2.5 at WRI=100
        goldstein_update   = max(0.0, -goldstein_avg / 5) # negative Goldstein → positive evidence
        velocity_update    = min(signal_velocity / 200, 1.0) * 0.8
        iw_update          = 0.6 if iw_triggered else 0.0
        surge_update       = min(signal_count_24h / 500, 1.0) * 0.5

        posterior_log_odds = (
            prior_log_odds
            + wri_update
            + goldstein_update
            + velocity_update
            + iw_update
            + surge_update
        )

        # Logistic transform to [0, 1]
        p = 1.0 / (1.0 + math.exp(-posterior_log_odds))
        return round(min(max(p, 0.01), 0.99), 4)

    # ------------------------------------------------------------------
    # Decision-to-Impact score
    # ------------------------------------------------------------------

    def _compute_dti(self, wri: float, escalation_rung: int, p_war: float) -> float:
        """Estimate Decision-to-Impact window in hours (lower = more urgent)."""
        # Linear interpolation: WRI 0 → 168h, WRI 100 → 2h
        base_hours = 168 - (wri / 100) * 166
        # Adjust for escalation rung (rung 5 = halve the window)
        rung_factor = 1.0 - ((escalation_rung - 1) / 4) * 0.5
        # Adjust for p_war (high certainty = shorter window)
        p_factor = 1.0 - p_war * 0.3
        return round(base_hours * rung_factor * p_factor, 1)

    # ------------------------------------------------------------------
    # Multi-horizon forecasts
    # ------------------------------------------------------------------

    def _generate_forecasts(
        self,
        wri: float,
        p_war: float,
        signal_velocity: float,
        goldstein_avg: float,
    ) -> dict[str, dict]:
        """Generate 24h, 72h, and 7d escalation forecasts."""
        def _project(horizon_h: int) -> dict:
            decay = math.exp(-0.02 * horizon_h)
            velocity_effect = min(signal_velocity / 100, 1.0) * (1 - decay)
            proj_wri = min(100.0, wri + velocity_effect * 15)
            proj_p_war = min(0.99, p_war + (1 - decay) * 0.05 * (p_war ** 0.5))
            tier, color = _threat_tier(proj_wri)
            trend = "escalating" if proj_wri > wri + 2 else "stable" if abs(proj_wri - wri) <= 2 else "de-escalating"
            return {
                "wri":      round(proj_wri, 1),
                "p_war":    round(proj_p_war, 4),
                "tier":     tier,
                "color":    color,
                "trend":    trend,
                "confidence": round(max(0.3, 1.0 - horizon_h / (24 * 30) * 0.6), 2),
            }

        return {
            "24h":  _project(24),
            "72h":  _project(72),
            "7d":   _project(168),
        }

    # ------------------------------------------------------------------
    # Narrative
    # ------------------------------------------------------------------

    def _build_narrative(
        self,
        theater: str,
        wri: float,
        rung_label: str,
        threat_tier: str,
        p_war: float,
        dti_score: float,
        goldstein_avg: float,
        signal_count_24h: int,
        signal_velocity: float,
        iw_triggered: bool,
        top_signals: list[dict],
        forecasts: dict,
        generated_at: str,
    ) -> str:
        """Build a structured Markdown narrative for leadership consumption."""
        lines = [
            "# CUI // SP-CTI",
            "",
            f"# Predictive Intelligence Brief — {theater.upper()} Theater",
            f"**Generated:** {generated_at[:16]}Z  |  "
            f"**Classification:** CUI // SP-CTI  |  "
            f"**Threat Tier:** {threat_tier}",
            "",
            "---",
            "",
            "## 1. Executive Threat Posture",
            "",
            f"| Indicator | Value |",
            f"|-----------|-------|",
            f"| PMESII-PT WRI | **{wri:.1f} / 100** |",
            f"| Escalation Rung | **{rung_label}** |",
            f"| P(Escalation) | **{p_war * 100:.1f}%** |",
            f"| Decision-to-Impact | **{dti_score:.0f} h** |",
            f"| Goldstein Avg (24h) | **{goldstein_avg:.2f}** |",
            f"| Signals Ingested (24h) | **{signal_count_24h}** |",
            f"| Signal Velocity | **{signal_velocity:+.1f}%** |",
            f"| IW Activity | **{'TRIGGERED' if iw_triggered else 'Not Triggered'}** |",
            "",
            "---",
            "",
            "## 2. Multi-Horizon Forecast",
            "",
            "| Horizon | Projected WRI | P(Escalation) | Tier | Trend |",
            "|---------|--------------|---------------|------|-------|",
        ]
        for h, fc in forecasts.items():
            lines.append(
                f"| {h} | {fc['wri']:.1f} | {fc['p_war']*100:.1f}% | {fc['tier']} | {fc['trend']} |"
            )

        if top_signals:
            lines += [
                "",
                "---",
                "",
                "## 3. Priority OSINT Signals (Last 24h)",
                "",
                "| Score | Title | Source |",
                "|-------|-------|--------|",
            ]
            for sig in top_signals:
                score = sig.get("composite_score") or 0
                title = (sig.get("title") or "—")[:60]
                source = (sig.get("source") or "unknown")[:30]
                lines.append(f"| {score:.2f} | {title} | {source} |")

        lines += [
            "",
            "---",
            "",
            "## 4. SIO Analyst Notes",
            "",
            "_Awaiting analyst review and annotation._",
            "",
            "---",
            "",
            "## 5. Recommended COAs",
            "",
        ]

        if threat_tier == "CRITICAL":
            lines += [
                "1. **Immediate escalation brief** to decision authority within DTI window.",
                "2. **Surge collection** on dominant PMESII-PT dimensions.",
                "3. **Activate CCIR triggers** for all PIRs with HIGH collection priority.",
            ]
        elif threat_tier == "HIGH":
            lines += [
                "1. **Heightened collection posture** on conflict zone sources.",
                "2. **Update SIO assessments** with Bayesian posterior inputs.",
                "3. **Prepare contingency SITREP** for senior leadership.",
            ]
        elif threat_tier == "MEDIUM":
            lines += [
                "1. **Maintain enhanced watch** on flagged PMESII-PT dimensions.",
                "2. **Review PIR coverage gaps** for next 72h collection cycle.",
            ]
        else:
            lines += [
                "1. **Routine monitoring** — maintain current collection posture.",
                "2. **No immediate escalatory indicators** detected in data mesh.",
            ]

        lines += [
            "",
            "---",
            "",
            "*This brief was generated by the ICDEV™ Predictive Intelligence Engine "
            "using the live OSINT data mesh. Confirm with analyst review before action.*",
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_brief(
        self,
        conn,
        theater: str,
        wri: float,
        threat_tier: str,
        escalation_rung: int,
        iw_triggered: bool,
        sig_24h: int,
        conflict_count: int,
        signal_velocity: float,
        p_war: float,
        goldstein_avg: float,
        dti_score: float,
        forecasts: dict,
        narrative_md: str,
        generated_at: str,
    ) -> str:
        self._ensure_table()
        brief_id = str(uuid.uuid4())
        ph = _ph()
        conn.execute(
            f"INSERT INTO sg_leadership_briefs "
            f"(id, theater, sio_composite_score, iw_triggered, threat_tier, "
            f"signal_count_24h, conflict_event_count, signal_velocity, "
            f"p_war_posterior, goldstein_avg, dti_score, "
            f"forecast_24h_json, forecast_72h_json, forecast_7d_json, "
            f"narrative_md, generated_at, classification) "
            f"VALUES ({','.join([ph]*17)})",  # nosec B608
            (
                brief_id,
                theater,
                round(wri, 2),
                1 if iw_triggered else 0,
                threat_tier,
                sig_24h,
                conflict_count,
                round(signal_velocity, 2),
                round(p_war, 4),
                round(goldstein_avg, 3),
                round(dti_score, 1),
                json.dumps(forecasts["24h"]),
                json.dumps(forecasts["72h"]),
                json.dumps(forecasts["7d"]),
                narrative_md,
                generated_at,
                "CUI // SP-CTI",
            ),
        )
        conn.commit()
        return brief_id

    def _ensure_table(self) -> None:
        try:
            import importlib  # noqa: PLC0415
            mod = importlib.import_module(
                "tools.db.migrations.158_sg_leadership_briefs.up"
            )
            mod.up()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    engine = PredictiveAnalysisEngine()
    result = engine.run(theater="global")
    printable = {k: v for k, v in result.items() if k != "narrative_md"}
    print(_json.dumps(printable, indent=2, default=str))
    print("\n--- Narrative (first 500 chars) ---")
    print(result["narrative_md"][:500])
