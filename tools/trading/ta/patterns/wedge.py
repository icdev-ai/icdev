# CUI // SP-CTI
"""Rising-wedge / falling-wedge chart pattern detector.

Fits OLS regression lines through swing highs (resistance) and swing lows
(support).  A rising wedge has both slopes positive with support rising
faster than resistance (converging upward).  A falling wedge has both
slopes negative with resistance falling slower than support.
"""
from __future__ import annotations


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Return (slope, intercept) via OLS, or None if singular."""
    n = len(xs)
    if n < 2:
        return None
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0.0:
        return None
    slope = num / den
    return slope, y_mean - slope * x_mean


def detect_wedge(
    bars: list[dict],
    swings: list[dict] | None = None,
    min_touches: int = 2,
) -> list[dict]:
    """Detect rising-wedge and falling-wedge chart patterns.

    Args:
        bars: OHLCV dicts with at minimum ``{"h": float, "l": float}``.
        swings: Pre-computed swing list.  If *None*, ``find_swings(bars)`` is
                called automatically.
        min_touches: Minimum number of swing highs and lows required.

    Returns:
        List of pattern dicts (0 or 1), each with keys:

        * ``type``       – ``"rising_wedge"`` or ``"falling_wedge"``
        * ``slope_high`` – OLS slope of the resistance (swing-highs) line
        * ``slope_low``  – OLS slope of the support (swing-lows) line
        * ``start_bar``  – earliest bar_index among all constituent swings
        * ``end_bar``    – latest bar_index among all constituent swings
    """
    if not bars:
        return []

    if swings is None:
        from tools.trading.ta.swings import find_swings
        swings = find_swings(bars)

    highs = [s for s in swings if s["type"] == "high"]
    lows = [s for s in swings if s["type"] == "low"]

    if len(highs) < min_touches or len(lows) < min_touches:
        return []

    res_fit = _ols([float(s["bar_index"]) for s in highs], [s["price"] for s in highs])
    sup_fit = _ols([float(s["bar_index"]) for s in lows], [s["price"] for s in lows])

    if res_fit is None or sup_fit is None:
        return []

    slope_high = res_fit[0]
    slope_low = sup_fit[0]

    # Rising wedge: both positive, support steeper (lines converge upward)
    if slope_high > 0.0 and slope_low > 0.0 and slope_low > slope_high:
        wedge_type = "rising_wedge"
    # Falling wedge: both negative, resistance less steep (lines converge downward)
    elif slope_high < 0.0 and slope_low < 0.0 and slope_high > slope_low:
        wedge_type = "falling_wedge"
    else:
        return []

    all_swings = highs + lows
    start_bar = min(s["bar_index"] for s in all_swings)
    end_bar = max(s["bar_index"] for s in all_swings)

    return [{
        "type": wedge_type,
        "slope_high": slope_high,
        "slope_low": slope_low,
        "start_bar": start_bar,
        "end_bar": end_bar,
    }]
