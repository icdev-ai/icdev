# CUI // SP-CTI
"""Pattern orchestrator: detect_patterns(bars) aggregates all chart pattern detectors."""
from __future__ import annotations


def detect_patterns(bars: list[dict]) -> list[dict]:
    """Detect chart patterns in price bar data.

    Calls the double, triple, and wedge detectors once (sharing a single
    ``find_swings`` pass), then removes duplicate patterns of the same type
    whose bar ranges overlap — keeping the widest-range instance.

    Args:
        bars: OHLCV dicts with at minimum ``{"h": float, "l": float}``.

    Returns:
        List of pattern dicts sorted by ``start_bar``, deduplicated.
    """
    if not bars:
        return []

    from tools.trading.ta.swings import find_swings
    from tools.trading.ta.patterns.double import detect_double
    from tools.trading.ta.patterns.triple import detect_triple
    from tools.trading.ta.patterns.wedge import detect_wedge

    swings = find_swings(bars)

    patterns: list[dict] = []
    patterns.extend(detect_double(bars, swings=swings))
    patterns.extend(detect_triple(bars, swings=swings))
    patterns.extend(detect_wedge(bars, swings=swings))

    return _dedup(patterns)


def _dedup(patterns: list[dict]) -> list[dict]:
    """Remove same-type patterns with overlapping bar ranges.

    When two patterns of the same type overlap, the one covering the wider
    bar range is kept (wider = more price history = higher conviction).
    """
    if not patterns:
        return []

    by_type: dict[str, list[dict]] = {}
    for p in patterns:
        by_type.setdefault(p["type"], []).append(p)

    result: list[dict] = []
    for group in by_type.values():
        # Widest range first so earlier kept entries always dominate overlaps
        group.sort(key=lambda p: -(p["end_bar"] - p["start_bar"]))
        kept: list[dict] = []
        for p in group:
            overlaps = any(
                max(p["start_bar"], k["start_bar"]) <= min(p["end_bar"], k["end_bar"])
                for k in kept
            )
            if not overlaps:
                kept.append(p)
        result.extend(kept)

    result.sort(key=lambda p: p["start_bar"])
    return result
