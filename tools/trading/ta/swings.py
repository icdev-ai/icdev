# CUI // SP-CTI
"""Swing high / swing low detector using a threshold-based zigzag algorithm."""
from __future__ import annotations


def find_swings(bars: list[dict], threshold_pct: float = 1.5) -> list[dict]:
    """Detect alternating swing highs and lows.

    Uses a two-phase zigzag approach: first confirms the initial price
    direction by looking for a threshold_pct move from the running extreme,
    then scans subsequent bars for reversals.

    Args:
        bars: OHLCV dicts with at minimum ``{"h": float, "l": float}``.
        threshold_pct: Minimum % reversal from a swing extreme to confirm it.

    Returns:
        List of swing dicts: ``{"type": "high"|"low", "price": float, "bar_index": int}``.
        Guaranteed to alternate high/low.
    """
    if len(bars) < 2:
        return []

    threshold = threshold_pct / 100.0
    swings: list[dict] = []

    # Phase 1: scan from bar 0 tracking running max-high and min-low until a
    # threshold move is confirmed.  The first confirmed reversal tells us the
    # initial direction and retroactively pins the first swing.
    init_high: float = bars[0]["h"]
    init_high_idx: int = 0
    init_low: float = bars[0]["l"]
    init_low_idx: int = 0

    direction: str | None = None
    extreme_val: float = 0.0
    extreme_idx: int = 0
    phase2_start: int = len(bars)

    for i in range(1, len(bars)):
        h, l = bars[i]["h"], bars[i]["l"]
        if h > init_high:
            init_high = h
            init_high_idx = i
        if l < init_low:
            init_low = l
            init_low_idx = i

        if l <= init_high * (1.0 - threshold):
            # Price fell threshold% from running high → first swing is a HIGH
            swings.append({"type": "high", "price": init_high, "bar_index": init_high_idx})
            direction = "down"
            extreme_val = l
            extreme_idx = i
            phase2_start = i + 1
            break

        if h >= init_low * (1.0 + threshold):
            # Price rose threshold% from running low → first swing is a LOW
            swings.append({"type": "low", "price": init_low, "bar_index": init_low_idx})
            direction = "up"
            extreme_val = h
            extreme_idx = i
            phase2_start = i + 1
            break

    if direction is None:
        return swings

    # Phase 2: scan remaining bars, recording swings on confirmed reversals.
    for i in range(phase2_start, len(bars)):
        h, l = bars[i]["h"], bars[i]["l"]

        if direction == "up":
            if h > extreme_val:
                extreme_val = h
                extreme_idx = i
            elif l <= extreme_val * (1.0 - threshold):
                swings.append({"type": "high", "price": extreme_val, "bar_index": extreme_idx})
                direction = "down"
                extreme_val = l
                extreme_idx = i
        else:  # direction == "down"
            if l < extreme_val:
                extreme_val = l
                extreme_idx = i
            elif h >= extreme_val * (1.0 + threshold):
                swings.append({"type": "low", "price": extreme_val, "bar_index": extreme_idx})
                direction = "up"
                extreme_val = h
                extreme_idx = i

    return swings
