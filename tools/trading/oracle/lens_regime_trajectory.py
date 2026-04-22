# CUI // SP-CTI
"""Trading Oracle — Regime Trajectory Lens.

Forecasts macro regime shifts by analyzing indicator trends over time.
Uses SROR snapshots + macro context to predict when danger/opportunity
scores will cross critical thresholds.

Signals:
  - Regime shift imminent (danger rising toward CRISIS/DANGER threshold)
  - Opportunity window opening (opportunity rising, danger falling)
  - Stagflation escalation (flags accumulating over time)
  - Deflation escalation (credit/M2 contraction + velocity collapse + breakeven
    compression — the balance-sheet-recession tail)
  - Yield curve deepening (inversion getting worse = recession clock ticking)
  - Cross-asset rotation HARD_EXIT → regime DETERIORATING override
  - Cross-asset rotation ROTATION_ALERT → +15 composite danger
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection, sql_placeholder
from tools.trading.oracle.base_lens import BaseTradingLens, TradingPrediction


class RegimeTrajectoryLens(BaseTradingLens):
    name = "regime_trajectory"
    description = "Forecasts macro regime shifts from indicator trends"

    def analyze(self) -> dict[str, Any]:
        """Gather SROR snapshots, macro context, and cross-asset rotation signal."""
        result: dict[str, Any] = {}

        # Current macro
        try:
            from tools.trading.data.macro_data import fetch_macro_context
            result["macro"] = fetch_macro_context()
        except Exception:
            result["macro"] = {}

        # SROR history (last 10 snapshots)
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT regime_label, composite_danger, composite_opportunity, "
                "danger_score_immediate, danger_score_near_term, danger_score_medium_term, "
                "recession_precursor_count, created_at "
                "FROM ad_systemic_radar_snapshots "
                "ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            result["sror_history"] = [dict(r) for r in rows]
            conn.close()
        except Exception:
            result["sror_history"] = []

        # Current regime context
        try:
            from tools.trading.market_intel.regime_lens import get_regime_context
            conn = get_connection()
            ctx = get_regime_context(conn)
            conn.close()
            result["regime_ctx"] = {
                "regime": ctx.regime,
                "composite_danger": ctx.composite_danger,
                "composite_opportunity": ctx.composite_opportunity,
                "danger_immediate": ctx.danger_immediate,
                "danger_near_term": ctx.danger_near_term,
                "danger_medium_term": ctx.danger_medium_term,
                "recession_precursors": ctx.recession_precursors,
            }
        except Exception:
            result["regime_ctx"] = {}

        # Cross-asset rotation signal (XAR-04)
        try:
            from tools.trading.market_intel.cross_asset_rotation import (
                compute_cross_asset_rotation,
            )
            xar = compute_cross_asset_rotation()
            result["rotation"] = {
                "rotation_signal": xar.get("rotation_signal", "NEUTRAL"),
                "rotation_score": xar.get("rotation_score", 0),
                "triggered_conditions": xar.get("triggered_conditions", []),
            }
        except Exception:
            result["rotation"] = {
                "rotation_signal": "NEUTRAL",
                "rotation_score": 0,
                "triggered_conditions": [],
            }

        return result

    def score(self, analysis: dict[str, Any]) -> list[TradingPrediction]:
        """Score regime trajectory patterns."""
        predictions = []
        macro = analysis.get("macro", {})
        raw = macro.get("raw_values", macro)
        ctx = analysis.get("regime_ctx", {})
        history = analysis.get("sror_history", [])

        regime = ctx.get("regime", "NEUTRAL")
        danger = ctx.get("composite_danger", 50)
        opportunity = ctx.get("composite_opportunity", 50)
        precursors = ctx.get("recession_precursors", 0)

        # --- Cross-asset rotation: apply before threshold checks (XAR-04) ---
        rotation = analysis.get("rotation", {})
        rotation_signal = rotation.get("rotation_signal", "NEUTRAL")
        rotation_score = rotation.get("rotation_score", 0)
        triggered_conditions = rotation.get("triggered_conditions", [])

        # HARD_EXIT: override regime label to DETERIORATING
        if rotation_signal == "HARD_EXIT":
            regime = "DETERIORATING"

        # ROTATION_ALERT: inflate composite danger by 15 points
        if rotation_signal == "ROTATION_ALERT":
            danger = min(100, danger + 15)

        # --- Cross-asset rotation prediction ---
        if rotation_signal == "HARD_EXIT":
            predictions.append(TradingPrediction(
                lens=self.name,
                subject_type="macro",
                subject_id="rotation_hard_exit",
                prediction_type="rotation_hard_exit",
                title=(
                    f"Cross-asset rotation HARD_EXIT — regime DETERIORATING "
                    f"(score={rotation_score}/5)"
                ),
                description=(
                    f"Cross-asset rotation model signals HARD_EXIT (score={rotation_score}/5). "
                    f"Multiple inter-market ratios confirm risk-off rotation. "
                    f"Regime trajectory overridden to DETERIORATING. "
                    f"Active conditions: {', '.join(triggered_conditions) or 'none'}."
                ),
                confidence=min(0.95, 0.60 + rotation_score * 0.07),
                severity="critical",
                horizon_days=21,
                direction="bearish",
                evidence={
                    "rotation_signal": rotation_signal,
                    "rotation_score": rotation_score,
                    "triggered_conditions": triggered_conditions,
                    "regime_override": "DETERIORATING",
                },
            ))
        elif rotation_signal == "ROTATION_ALERT":
            predictions.append(TradingPrediction(
                lens=self.name,
                subject_type="macro",
                subject_id="rotation_alert",
                prediction_type="rotation_alert",
                title=(
                    f"Cross-asset rotation ROTATION_ALERT — danger +15 "
                    f"(score={rotation_score}/5, adjusted={danger:.0f})"
                ),
                description=(
                    f"Cross-asset rotation model signals ROTATION_ALERT (score={rotation_score}/5). "
                    f"Composite danger elevated by 15 points to {danger:.0f}/100. "
                    f"Inter-market rotation pressure building. "
                    f"Active conditions: {', '.join(triggered_conditions) or 'none'}."
                ),
                confidence=min(0.85, 0.50 + rotation_score * 0.10),
                severity="high",
                horizon_days=14,
                direction="bearish",
                evidence={
                    "rotation_signal": rotation_signal,
                    "rotation_score": rotation_score,
                    "triggered_conditions": triggered_conditions,
                    "danger_adjusted": danger,
                },
            ))

        # --- Danger trajectory: approaching CRISIS ---
        if danger >= 65 and regime not in ("CRISIS",):
            predictions.append(TradingPrediction(
                lens=self.name,
                subject_type="macro",
                subject_id="regime_shift",
                prediction_type="danger_escalation",
                title=f"Regime approaching CRISIS (danger={danger:.0f}, current={regime})",
                description=(
                    f"Composite danger at {danger:.0f}/100. Current regime: {regime}. "
                    f"CRISIS threshold is ~75. If danger continues rising, "
                    f"expect regime shift within 1-2 weeks."
                ),
                confidence=min(0.90, danger / 100),
                severity="critical" if danger >= 75 else "high",
                horizon_days=14,
                direction="bearish",
                evidence={
                    "composite_danger": danger,
                    "regime": regime,
                    "danger_immediate": ctx.get("danger_immediate", 0),
                    "danger_near_term": ctx.get("danger_near_term", 0),
                },
            ))

        # --- Opportunity window ---
        if opportunity >= 65 and danger < 40:
            predictions.append(TradingPrediction(
                lens=self.name,
                subject_type="macro",
                subject_id="opportunity_window",
                prediction_type="opportunity_opening",
                title=f"Opportunity window (opp={opportunity:.0f}, danger={danger:.0f})",
                description=(
                    f"Composite opportunity at {opportunity:.0f}/100 with low danger ({danger:.0f}). "
                    f"Favorable conditions for risk-on positioning."
                ),
                confidence=min(0.85, opportunity / 100),
                severity="info",
                horizon_days=30,
                direction="bullish",
                evidence={
                    "composite_opportunity": opportunity,
                    "composite_danger": danger,
                    "regime": regime,
                },
            ))

        # --- Recession precursors accumulating ---
        if precursors >= 4:
            predictions.append(TradingPrediction(
                lens=self.name,
                subject_type="macro",
                subject_id="recession_watch",
                prediction_type="recession_signal",
                title=f"Recession precursors: {precursors}/7 active",
                description=(
                    f"{precursors} of 7 recession precursors are active. "
                    f"Historical pattern: 5+ precursors preceded every recession since 1970. "
                    f"Consider defensive positioning."
                ),
                confidence=min(0.90, 0.4 + precursors * 0.1),
                severity="critical" if precursors >= 5 else "high",
                horizon_days=90,
                direction="bearish",
                evidence={"recession_precursors": precursors, "regime": regime},
            ))

        # --- Stagflation escalation ---
        stagflation = macro.get("stagflation_risk", {})
        if isinstance(stagflation, dict):
            flags = stagflation.get("active_flags", 0)
            if flags >= 3:
                predictions.append(TradingPrediction(
                    lens=self.name,
                    subject_type="macro",
                    subject_id="stagflation",
                    prediction_type="stagflation_escalation",
                    title=f"Stagflation: {flags}/4 conditions met",
                    description=(
                        f"{stagflation.get('label', '?')}. "
                        f"GDP: {raw.get('gdp_growth_q', 0):.1f}%, CPI: {raw.get('cpi_yoy', 0):.1f}%. "
                        f"Stagflation erodes both growth and value plays — only commodities and cash benefit."
                    ),
                    confidence=0.75 + (flags - 3) * 0.1,
                    severity="critical",
                    horizon_days=60,
                    direction="bearish",
                    evidence={
                        "stagflation_flags": flags,
                        "gdp": raw.get("gdp_growth_q", 0),
                        "cpi": raw.get("cpi_yoy", 0),
                        "label": stagflation.get("label", ""),
                    },
                ))

        # --- Deflation escalation (symmetric to stagflation) ---
        deflation = macro.get("deflation_risk", {})
        if isinstance(deflation, dict):
            flags = deflation.get("active_flags", 0)
            if flags >= 2:
                predictions.append(TradingPrediction(
                    lens=self.name,
                    subject_type="macro",
                    subject_id="deflation",
                    prediction_type="deflation_escalation",
                    title=f"Deflation: {flags}/4 conditions met",
                    description=(
                        f"{deflation.get('label', '?')}. "
                        f"Loan growth: {raw.get('loan_growth_yoy', 0):.1f}%, "
                        f"M2 YoY: {raw.get('m2_yoy_pct', 0):.1f}%, "
                        f"velocity: {raw.get('velocity_of_money', 0):.2f}, "
                        f"5y5y breakeven: {raw.get('breakeven_5y5y', 0):.2f}%. "
                        f"Deflation favors duration + cash + consumer staples; punishes "
                        f"leverage, commodities, and cyclicals."
                    ),
                    confidence=0.65 + (flags - 2) * 0.1,
                    severity="critical" if flags >= 3 else "high",
                    horizon_days=90,
                    direction="bearish",
                    evidence={
                        "deflation_flags": flags,
                        "loan_growth_yoy": raw.get("loan_growth_yoy", 0),
                        "m2_yoy_pct": raw.get("m2_yoy_pct", 0),
                        "velocity_of_money": raw.get("velocity_of_money", 0),
                        "breakeven_5y5y": raw.get("breakeven_5y5y", 0),
                        "label": deflation.get("label", ""),
                    },
                ))

        # --- Yield curve inversion deepening ---
        yield_spread = raw.get("yield_spread", 0)
        if yield_spread < -0.5:
            predictions.append(TradingPrediction(
                lens=self.name,
                subject_type="macro",
                subject_id="yield_curve",
                prediction_type="deep_inversion",
                title=f"Yield curve deeply inverted ({yield_spread:.2f}%)",
                description=(
                    f"10Y-2Y spread at {yield_spread:.2f}%. Deep inversion historically "
                    f"precedes recession by 6-18 months. Credit conditions will tighten."
                ),
                confidence=0.80,
                severity="high",
                horizon_days=180,
                direction="bearish",
                evidence={"yield_spread": yield_spread, "fed_funds": raw.get("fed_funds", 0)},
            ))

        # --- VIX regime shift ---
        vix = raw.get("vix", 20)
        if vix > 30:
            predictions.append(TradingPrediction(
                lens=self.name,
                subject_type="macro",
                subject_id="volatility",
                prediction_type="elevated_vix",
                title=f"VIX elevated at {vix:.0f} — stress regime",
                description=(
                    f"VIX at {vix:.0f} indicates market stress. "
                    f"Position sizes should be reduced. "
                    f"Mean reversion typically takes 2-4 weeks from elevated levels."
                ),
                confidence=0.75,
                severity="high",
                horizon_days=21,
                direction="bearish",
                evidence={"vix": vix},
            ))

        return predictions

    def propose(self, predictions: list[TradingPrediction]) -> list[TradingPrediction]:
        """Add regime-specific recommended actions."""
        for pred in predictions:
            if pred.prediction_type == "rotation_hard_exit":
                pred.recommended_action = (
                    "Cross-asset rotation at HARD_EXIT threshold. Immediately reduce risk exposure: "
                    "exit equities, rotate to Treasuries/gold/cash. Regime labeled DETERIORATING — "
                    "avoid new long positions until rotation_signal returns to NEUTRAL."
                )
            elif pred.prediction_type == "rotation_alert":
                pred.recommended_action = (
                    "Rotation pressure building. Trim speculative positions, tighten stop-losses. "
                    "Monitor rotation_score daily — escalation to HARD_EXIT warrants full defensive pivot."
                )
            elif pred.prediction_type == "danger_escalation":
                pred.recommended_action = (
                    "Reduce equity exposure by 20-30%. Move to defensive sectors "
                    "(utilities, staples, healthcare). Increase cash allocation."
                )
            elif pred.prediction_type == "opportunity_opening":
                pred.recommended_action = (
                    "Risk-on conditions favorable. Consider increasing equity exposure. "
                    "Look for high-conviction BUY signals with confluence tier A."
                )
            elif pred.prediction_type == "recession_signal":
                pred.recommended_action = (
                    "Defensive positioning: rotate to quality (low debt, high FCF), "
                    "increase bond/gold allocation, trim cyclicals."
                )
            elif pred.prediction_type == "stagflation_escalation":
                pred.recommended_action = (
                    "Stagflation hedge: overweight energy, commodities, TIPS. "
                    "Underweight growth/tech and long-duration bonds."
                )
            elif pred.prediction_type == "deflation_escalation":
                pred.recommended_action = (
                    "Deflation hedge: overweight long-duration Treasuries (TLT), "
                    "cash equivalents (SHY), staples (XLP), dividend aristocrats. "
                    "Underweight banks, commodities, small caps, and leveraged names. "
                    "Watch for coordinated central bank easing as the exit signal."
                )
            elif pred.prediction_type == "deep_inversion":
                pred.recommended_action = (
                    "Credit tightening ahead. Reduce exposure to leveraged names, "
                    "real estate, and small caps. Monitor bank earnings closely."
                )
            elif pred.prediction_type == "elevated_vix":
                pred.recommended_action = (
                    "Reduce position sizes to 50-70% of normal. "
                    "Avoid new initiations until VIX drops below 25."
                )
        return predictions
