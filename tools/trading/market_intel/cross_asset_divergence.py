"""Cross-asset divergence detector for SROR (Family 6).

Detects dislocations between asset classes that historically precede
market corrections or signal hidden stress. 100% deterministic — no LLM.

Usage:
    python tools/trading/market_intel/cross_asset_divergence.py --detect --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def detect_divergences(macro_raw: dict, extended: dict | None = None) -> dict:
    """Detect cross-asset divergences from macro indicator data.

    Args:
        macro_raw: Raw values dict from fetch_macro_context()["raw_values"].
        extended: Optional extended macro dict from fetch_extended_macro().

    Returns:
        Dict with divergences list, danger_score, and opportunity_score.
    """
    divergences: list[dict] = []

    # --- 1. Stocks vs Bonds divergence ---
    # S&P above SMA50 (bullish) but yield spread inverted (bearish bonds)
    sp_above_sma = (macro_raw.get("sp500", 5200) > macro_raw.get("sp500_sma50", 5100))
    yield_inverted = (macro_raw.get("yield_spread", 0.5) < 0)
    if sp_above_sma and yield_inverted:
        divergences.append({
            "name": "stocks_vs_bonds",
            "severity": "high",
            "danger_score": 80,
            "message": "Stocks rallying but yield curve inverted — recession signal ignored by equities",
        })
    elif not sp_above_sma and not yield_inverted:
        divergences.append({
            "name": "stocks_vs_bonds",
            "severity": "medium",
            "danger_score": 60,
            "message": "Stocks falling but yield curve positive — potential oversold opportunity",
        })

    # --- 2. Gold + Dollar both rising (extreme stress) ---
    gold_rising = (macro_raw.get("gold_change_30d", 0) > 0.03)
    dxy_strong = (macro_raw.get("dxy", 100) > 105)
    if gold_rising and dxy_strong:
        divergences.append({
            "name": "gold_dollar_stress",
            "severity": "high",
            "danger_score": 85,
            "message": "Gold AND Dollar both rising — extreme flight to safety across asset classes",
        })

    # --- 3. Oil crash + equity rally (demand destruction ignored) ---
    oil_crashing = (macro_raw.get("oil_change_30d", 0) < -0.10)
    if oil_crashing and sp_above_sma:
        divergences.append({
            "name": "oil_equity_divergence",
            "severity": "medium",
            "danger_score": 65,
            "message": "Oil crashing while equities rally — demand destruction signal being ignored",
        })

    # --- 4. VIX elevated vs S&P above SMA (fear without selling) ---
    vix = macro_raw.get("vix", 20)
    if vix > 25 and sp_above_sma:
        divergences.append({
            "name": "vix_equity_divergence",
            "severity": "medium",
            "danger_score": 60,
            "message": f"VIX elevated ({vix:.1f}) but S&P above SMA — fear without selling, unusual pattern",
        })

    # --- 5. Market breadth vs index (topping pattern) ---
    # Use NASDAQ vs S&P as breadth proxy (NASDAQ more speculative)
    nq_above_sma = (macro_raw.get("nasdaq", 16000) > macro_raw.get("nasdaq_sma50", 15800))
    if sp_above_sma and not nq_above_sma:
        divergences.append({
            "name": "breadth_divergence",
            "severity": "medium",
            "danger_score": 65,
            "message": "S&P above SMA but NASDAQ below — narrow leadership, topping risk",
        })

    # --- 6. Credit spread widening vs equity strength ---
    if extended:
        credit_spread = extended.get("credit_spread_hy_bps", 400)
        if credit_spread > 500 and sp_above_sma:
            divergences.append({
                "name": "credit_equity_divergence",
                "severity": "high",
                "danger_score": 75,
                "message": f"HY credit spreads wide ({credit_spread:.0f}bps) but equities resilient — credit leads",
            })

    # --- 7. IG/HY OAS ratio compression (systemic credit stress) ---
    # Normal ratio: HY_OAS / IG_OAS = 4-5x. Below 3x means IG widening fast
    # relative to HY — systemic stress spreading into investment-grade.
    if extended:
        ig_oas = extended.get("ig_oas_bps", 110.0)
        hy_oas = extended.get("credit_spread_hy_bps", 450.0)
        if ig_oas > 0:
            ig_hy_ratio = hy_oas / ig_oas
            if ig_hy_ratio < 3.0:
                divergences.append({
                    "name": "ig_hy_ratio_compression",
                    "severity": "high",
                    "danger_score": 80,
                    "message": (
                        f"IG/HY OAS ratio compressed to {ig_hy_ratio:.2f}x "
                        f"(IG {ig_oas:.0f}bps, HY {hy_oas:.0f}bps) — "
                        "systemic credit stress: investment-grade widening toward junk levels"
                    ),
                })

    # --- 8. IG widening while equity holds (leading indicator) ---
    # IG spreads price in credit risk before equity sellers show up.
    if extended:
        ig_oas = extended.get("ig_oas_bps", 110.0)
        if ig_oas > 150 and sp_above_sma:
            divergences.append({
                "name": "ig_widening_equity_holds",
                "severity": "medium",
                "danger_score": 70,
                "message": (
                    f"IG OAS elevated ({ig_oas:.0f}bps) while S&P holds above SMA — "
                    "credit markets pricing risk ahead of equity; historically a leading indicator"
                ),
            })

    # Compute aggregate scores
    if divergences:
        danger_score = sum(d["danger_score"] for d in divergences) / len(divergences)
        # More divergences = more danger
        divergence_count_penalty = min(20, len(divergences) * 5)
        danger_score = min(100, danger_score + divergence_count_penalty)
        opportunity_score = max(0, 100 - danger_score)
    else:
        danger_score = 15.0  # no divergences = low danger
        opportunity_score = 75.0

    return {
        "divergences": divergences,
        "divergence_count": len(divergences),
        "danger_score": round(danger_score, 1),
        "opportunity_score": round(opportunity_score, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-asset divergence detector")
    parser.add_argument("--detect", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.detect:
        from tools.trading.data.macro_data import fetch_macro_context, fetch_extended_macro
        ctx = fetch_macro_context()
        ext = fetch_extended_macro()
        result = detect_divergences(ctx.get("raw_values", {}), ext)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Divergences detected: {result['divergence_count']}")
            for d in result["divergences"]:
                print(f"  [{d['severity'].upper()}] {d['name']}: {d['message']}")
            print(f"Danger score: {result['danger_score']}")


if __name__ == "__main__":
    main()
