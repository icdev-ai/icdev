# CUI // SP-CTI
"""Double-top / double-bottom chart pattern detector.

Three consecutive swings (low, high, low) or (high, low, high) where the
two same-kind swings are within *tolerance_pct* of their mean price.
The middle swing is the neckline.
"""
from __future__ import annotations


def detect_double(
    bars: list[dict],
    swings: list[dict] | None = None,
    tolerance_pct: float = 3.0,
) -> list[dict]:
    """Detect double-top and double-bottom chart patterns.

    Args:
        bars: OHLCV dicts with at minimum ``{"h": float, "l": float}``.
        swings: Pre-computed swing list (same format as ``find_swings`` output).
                If *None*, ``find_swings(bars)`` is called automatically.
        tolerance_pct: Max % each swing price may deviate from the pair mean.

    Returns:
        List of pattern dicts, each with keys:

        * ``type``         – ``"double_top"`` or ``"double_bottom"``
        * ``high_1``/``high_2`` or ``low_1``/``low_2`` – constituent swings
        * ``neckline``     – the intermediate swing dict
        * ``start_bar``    – ``bar_index`` of the first swing
        * ``end_bar``      – ``bar_index`` of the second same-kind swing
        * ``avg_price``    – mean price of the two same-kind swings
        * ``tolerance_pct``– tolerance used for detection
    """
    if not bars:
        return []

    if swings is None:
        from tools.trading.ta.swings import find_swings
        swings = find_swings(bars)

    if len(swings) < 3:
        return []

    threshold = tolerance_pct / 100.0
    patterns: list[dict] = []

    for i in range(len(swings) - 2):
        s1 = swings[i]
        s_mid = swings[i + 1]
        s2 = swings[i + 2]

        # s1 and s2 must be same kind; s_mid must be the opposite kind
        if s1["type"] != s2["type"] or s_mid["type"] == s1["type"]:
            continue

        avg = (s1["price"] + s2["price"]) / 2.0
        if avg == 0.0:
            continue

        if (abs(s1["price"] - avg) / avg <= threshold
                and abs(s2["price"] - avg) / avg <= threshold):

            if s1["type"] == "high":
                patterns.append({
                    "type": "double_top",
                    "high_1": s1,
                    "high_2": s2,
                    "neckline": s_mid,
                    "start_bar": s1["bar_index"],
                    "end_bar": s2["bar_index"],
                    "avg_price": avg,
                    "tolerance_pct": tolerance_pct,
                })
            else:
                patterns.append({
                    "type": "double_bottom",
                    "low_1": s1,
                    "low_2": s2,
                    "neckline": s_mid,
                    "start_bar": s1["bar_index"],
                    "end_bar": s2["bar_index"],
                    "avg_price": avg,
                    "tolerance_pct": tolerance_pct,
                })

    patterns.sort(key=lambda p: p["start_bar"])
    return patterns
