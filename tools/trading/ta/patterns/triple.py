# CUI // SP-CTI
"""Triple-top / triple-bottom pattern detector.

Three consecutive same-kind swings (all highs or all lows) whose prices
fall within *tolerance_pct* of their mean.  Triple patterns are rarer
and higher-conviction than double patterns.
"""

from __future__ import annotations


def detect_triple(
    bars: list[dict],
    swings: list[dict] | None = None,
    tolerance_pct: float = 3.0,
) -> list[dict]:
    """Detect triple-top and triple-bottom chart patterns.

    Args:
        bars: OHLCV dicts with at minimum ``{"h": float, "l": float}``.
        swings: Pre-computed swing list (same format as ``find_swings`` output).
                If *None*, ``find_swings(bars)`` is called automatically.
        tolerance_pct: Max % each swing price may deviate from the group mean.

    Returns:
        List of pattern dicts, each with keys:

        * ``type``         – ``"triple_top"`` or ``"triple_bottom"``
        * ``swing_1``      – first constituent swing dict
        * ``swing_2``      – second constituent swing dict
        * ``swing_3``      – third constituent swing dict
        * ``start_bar``    – ``bar_index`` of the first swing
        * ``end_bar``      – ``bar_index`` of the third swing
        * ``avg_price``    – mean price of the three swings
        * ``tolerance_pct``– tolerance used for detection
    """
    if not bars:
        return []

    if swings is None:
        from tools.trading.ta.swings import find_swings
        swings = find_swings(bars)

    if len(swings) < 5:
        # Need at least 3 same-kind swings; with strict alternation that
        # means a minimum of 5 total swings (h,l,h,l,h or l,h,l,h,l).
        return []

    highs = [s for s in swings if s["type"] == "high"]
    lows = [s for s in swings if s["type"] == "low"]

    patterns: list[dict] = []

    for group, kind in ((highs, "high"), (lows, "low")):
        for i in range(len(group) - 2):
            s1, s2, s3 = group[i], group[i + 1], group[i + 2]

            prices = [s1["price"], s2["price"], s3["price"]]
            avg = sum(prices) / 3.0

            if avg == 0.0:
                continue

            if all(abs(p - avg) / avg * 100.0 <= tolerance_pct for p in prices):
                patterns.append({
                    "type": "triple_top" if kind == "high" else "triple_bottom",
                    "swing_1": s1,
                    "swing_2": s2,
                    "swing_3": s3,
                    "start_bar": s1["bar_index"],
                    "end_bar": s3["bar_index"],
                    "avg_price": avg,
                    "tolerance_pct": tolerance_pct,
                })

    patterns.sort(key=lambda p: p["start_bar"])
    return patterns
