# CUI // SP-CTI
"""Support / resistance level detector built on swing-point clustering."""
from __future__ import annotations


def compute_sr(
    bars: list[dict],
    swings: list[dict] | None = None,
    cluster_pct: float = 0.5,
) -> list[dict]:
    """Cluster swing highs/lows into S/R levels and score their strength.

    Args:
        bars: OHLCV dicts with at minimum ``{"h": float, "l": float}``.
        swings: Pre-computed swings (``find_swings`` output).  If *None*,
                ``find_swings(bars)`` is called automatically.
        cluster_pct: Maximum % price deviation to merge touches into one level.

    Returns:
        List of level dicts sorted by strength desc::

            {
                "price":    float,   # cluster centre price
                "strength": float,   # normalised 0.0–1.0
                "touches":  int,     # raw touch count
                "type":     "support" | "resistance"
            }
    """
    if not bars:
        return []

    if swings is None:
        from tools.trading.ta.swings import find_swings
        swings = find_swings(bars)

    if not swings:
        return []

    tol = cluster_pct / 100.0
    clusters: list[dict] = []

    for sw in swings:
        price = sw["price"]
        merged = False
        for cl in clusters:
            if cl["price"] > 0 and abs(price - cl["price"]) / cl["price"] <= tol:
                n = cl["touches"]
                cl["price"] = (cl["price"] * n + price) / (n + 1)
                cl["touches"] += 1
                merged = True
                break
        if not merged:
            clusters.append({
                "price": price,
                "touches": 1,
                "type": "resistance" if sw["type"] == "high" else "support",
            })

    if not clusters:
        return []

    max_touches = max(c["touches"] for c in clusters) or 1
    for c in clusters:
        c["strength"] = round(c["touches"] / max_touches, 3)

    clusters.sort(key=lambda c: -c["strength"])
    return clusters
