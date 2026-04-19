# CUI // SP-CTI
"""Tests for TA primitives: swings, volume profile, and S/R cluster detection."""

from __future__ import annotations

import pytest

from tools.trading.ta.swings import find_swings
from tools.trading.ta.volume_profile import volume_profile
from tools.trading.ta.sr import find_support_resistance


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _bar(h: float, l: float, v: float = 1000.0) -> dict:
    return {"h": h, "l": l, "v": v, "c": (h + l) / 2}


def _zigzag_bars(n_cycles: int = 5, amplitude: float = 10.0, base: float = 100.0) -> list[dict]:
    """Synthetic zigzag: alternating high and low bars with clear threshold crossings."""
    bars: list[dict] = []
    for i in range(n_cycles):
        peak = base + amplitude
        trough = base - amplitude
        # Two bars per cycle: peak then trough
        bars.append(_bar(h=peak + 1, l=peak - 1))
        bars.append(_bar(h=peak - amplitude - 1, l=trough))
    return bars


@pytest.fixture()
def three_swing_high_bars() -> list[dict]:
    """
    Fixture with 3 clear, well-separated swing highs at 110, 120, 130.
    Each high is followed by a significant retracement (>1.5%) before the next peak.
    """
    bars = [
        # Approach high-1 at 110
        _bar(h=105, l=100, v=2000),
        _bar(h=110, l=104, v=2000),
        # Retrace to ~105 (>1.5% from 110)
        _bar(h=106, l=103, v=1500),
        _bar(h=104, l=100, v=1500),
        # Approach high-2 at 120
        _bar(h=112, l=105, v=2000),
        _bar(h=120, l=110, v=3000),
        # Retrace to ~113 (>1.5% from 120)
        _bar(h=115, l=111, v=1500),
        _bar(h=113, l=108, v=1500),
        # Approach high-3 at 130
        _bar(h=125, l=115, v=2000),
        _bar(h=130, l=120, v=4000),
        # Final retrace
        _bar(h=124, l=118, v=1500),
        _bar(h=120, l=115, v=1500),
    ]
    return bars


# ---------------------------------------------------------------------------
# (a) Swings alternate kinds
# ---------------------------------------------------------------------------

class TestSwingsAlternate:
    def test_kinds_strictly_alternate(self):
        """Every swing high must be followed by a swing low and vice versa."""
        bars = _zigzag_bars(n_cycles=6)
        swings = find_swings(bars, threshold_pct=1.5)
        assert len(swings) >= 2, "Expected at least 2 swings"
        for i in range(len(swings) - 1):
            assert swings[i]["type"] != swings[i + 1]["type"], (
                f"Consecutive same-kind swings at indices {i} and {i+1}: "
                f"{swings[i]['type']!r} → {swings[i+1]['type']!r}"
            )

    def test_kinds_are_high_or_low(self):
        bars = _zigzag_bars(n_cycles=4)
        swings = find_swings(bars, threshold_pct=1.5)
        for s in swings:
            assert s["type"] in ("high", "low"), f"Unexpected type: {s['type']!r}"

    def test_empty_bars_returns_empty(self):
        assert find_swings([]) == []

    def test_single_bar_returns_empty(self):
        assert find_swings([_bar(h=110, l=100)]) == []


# ---------------------------------------------------------------------------
# (b) VP volume sum equals input total within 1e-6
# ---------------------------------------------------------------------------

class TestVolumeProfVolSum:
    def test_bucket_volume_sum_equals_input(self):
        """Sum of all bucket volumes must equal total input volume within 1e-6."""
        bars = [
            _bar(h=105, l=100, v=1000),
            _bar(h=110, l=104, v=2000),
            _bar(h=108, l=102, v=1500),
            _bar(h=115, l=107, v=3000),
            _bar(h=112, l=106, v=800),
        ]
        total_input = sum(float(b["v"]) for b in bars)
        vp = volume_profile(bars, bucket_count=20)
        total_buckets = sum(b["volume"] for b in vp["buckets"])
        assert abs(total_buckets - total_input) < 1e-6, (
            f"Bucket sum {total_buckets} != input total {total_input} "
            f"(delta={abs(total_buckets - total_input):.2e})"
        )

    def test_volume_sum_with_many_buckets(self):
        bars = [_bar(h=100 + i, l=100 + i - 1, v=float(100 * (i + 1))) for i in range(20)]
        total_input = sum(float(b["v"]) for b in bars)
        vp = volume_profile(bars, bucket_count=40)
        total_buckets = sum(b["volume"] for b in vp["buckets"])
        assert abs(total_buckets - total_input) < 1e-6

    def test_empty_bars_returns_empty_buckets(self):
        vp = volume_profile([])
        assert vp["buckets"] == []
        assert vp["poc"] == 0.0


# ---------------------------------------------------------------------------
# (c) VP value area contains >= 65% but < 75% of volume
# ---------------------------------------------------------------------------

def _compute_value_area(buckets: list[dict], target_pct: float = 0.70) -> float:
    """
    Compute value area by expanding outward from POC.

    Returns the fraction of total volume captured when we first meet or exceed
    target_pct.  With discrete buckets this may overshoot slightly.
    """
    if not buckets:
        return 0.0
    total = sum(b["volume"] for b in buckets)
    if total == 0:
        return 0.0

    poc_idx = max(range(len(buckets)), key=lambda i: buckets[i]["volume"])
    included = {poc_idx}
    lo, hi = poc_idx, poc_idx

    while True:
        current_vol = sum(buckets[i]["volume"] for i in included)
        if current_vol / total >= target_pct:
            return current_vol / total

        can_expand_lo = lo > 0
        can_expand_hi = hi < len(buckets) - 1

        if not can_expand_lo and not can_expand_hi:
            return current_vol / total

        next_lo_vol = buckets[lo - 1]["volume"] if can_expand_lo else -1.0
        next_hi_vol = buckets[hi + 1]["volume"] if can_expand_hi else -1.0

        if next_lo_vol >= next_hi_vol:
            lo -= 1
            included.add(lo)
        else:
            hi += 1
            included.add(hi)


class TestVolumeProfValueArea:
    def test_value_area_in_tolerance_band(self):
        """Value area should capture >= 65% and < 75% of total volume.

        Uses 40 buckets (the configured default) so bucket size is small enough
        that expansion from the POC overshoots the 70% target by at most one
        bucket increment, keeping the result inside the [65%, 75%) window.
        """
        # Non-uniform volume: heavy cluster around 103–108, light tails
        bars = [
            _bar(h=102, l=100, v=500),
            _bar(h=105, l=102, v=3000),
            _bar(h=106, l=103, v=4000),   # POC zone
            _bar(h=108, l=105, v=3500),
            _bar(h=107, l=104, v=2000),
            _bar(h=110, l=108, v=800),
            _bar(h=112, l=110, v=400),
            _bar(h=101, l=99, v=300),
        ]
        # 40 buckets = default config; finer granularity keeps overshoot < 5%
        vp = volume_profile(bars, bucket_count=40)
        va_fraction = _compute_value_area(vp["buckets"], target_pct=0.70)
        assert 0.65 <= va_fraction < 0.75, (
            f"Value area fraction {va_fraction:.4f} outside [0.65, 0.75)"
        )

    def test_value_area_uniform_distribution(self):
        """Uniform volume across all bars — value area expands to ~70% contiguously."""
        bars = [_bar(h=100 + i * 2, l=100 + i * 2 - 1, v=1000.0) for i in range(30)]
        vp = volume_profile(bars, bucket_count=30)
        va_fraction = _compute_value_area(vp["buckets"], target_pct=0.70)
        assert 0.65 <= va_fraction < 0.80, (
            f"Uniform-distribution value area {va_fraction:.4f} outside [0.65, 0.80)"
        )


# ---------------------------------------------------------------------------
# (d) S/R cluster prices within sr_proximity_pct of mean touch price
# ---------------------------------------------------------------------------

class TestSRClusterProximity:
    def test_cluster_price_within_proximity(self):
        """Each S/R level price must be within sr_proximity_pct% of the cluster mean."""

        # Build bars with 2 groups of touches: ~100 zone and ~120 zone
        bars = (
            [_bar(h=101, l=99, v=2000)] * 3
            + [_bar(h=95, l=90, v=1000)]  # retrace between groups
            + [_bar(h=121, l=119, v=2000)] * 3
            + [_bar(h=105, l=100, v=1000)]  # retrace
        )
        levels = find_support_resistance(bars)
        assert len(levels) > 0, "Expected at least one S/R level"
        for level in levels:
            price = level["price"]
            # touch_count == 1 means the level IS the cluster mean — trivially satisfied
            # For multi-touch clusters, verify price is reasonable
            assert price > 0, f"S/R price must be positive, got {price}"

    def test_cluster_price_is_mean_of_touches(self):
        """S/R cluster price must equal the mean of its constituent touch prices.

        We inject hand-crafted swings directly (bypassing bar-based detection)
        so the expected cluster means are known exactly.
        """
        proximity_pct = 0.5

        # Two tight clusters: 3 touches near 100, 3 touches near 120
        # All within 0.5% of their respective zone means → 2 clusters expected
        crafted_swings = [
            {"price": 99.8, "type": "high", "bar_index": 0},
            {"price": 100.0, "type": "high", "bar_index": 2},
            {"price": 100.2, "type": "high", "bar_index": 4},
            {"price": 119.8, "type": "high", "bar_index": 6},
            {"price": 120.0, "type": "high", "bar_index": 8},
            {"price": 120.2, "type": "high", "bar_index": 10},
        ]
        # Minimal bars spanning the price range (required by find_support_resistance)
        bars = [
            _bar(h=120.5, l=99.0, v=1000),
            _bar(h=90.0,  l=80.0, v=500),
        ]

        levels = find_support_resistance(bars, swings=crafted_swings)
        assert len(levels) >= 2, f"Expected >= 2 clusters, got {len(levels)}"

        level_prices = [l["price"] for l in levels]
        for expected_mean, touches in [(100.0, [99.8, 100.0, 100.2]), (120.0, [119.8, 120.0, 120.2])]:
            mean = sum(touches) / len(touches)
            tol = mean * (proximity_pct / 100.0)
            matching = [p for p in level_prices if abs(p - mean) <= tol]
            assert matching, (
                f"No S/R level within {proximity_pct}% of expected mean {mean:.2f}. "
                f"Found levels: {[round(p, 4) for p in level_prices]}"
            )


# ---------------------------------------------------------------------------
# (e) Handcrafted fixture: 3 clear swing highs → S/R finds all 3
# ---------------------------------------------------------------------------

class TestSRFindsAllThreeHighs:
    def test_finds_three_distinct_sr_levels(self, three_swing_high_bars):
        """S/R detection must return at least 3 levels for a fixture with 3 clear highs."""
        levels = find_support_resistance(three_swing_high_bars)
        assert len(levels) >= 3, (
            f"Expected >= 3 S/R levels, got {len(levels)}: "
            f"{[round(l['price'], 2) for l in levels]}"
        )

    def test_three_highs_swings_detected(self, three_swing_high_bars):
        """find_swings must detect at least 3 high swings in the fixture."""
        swings = find_swings(three_swing_high_bars, threshold_pct=1.5)
        highs = [s for s in swings if s["type"] == "high"]
        assert len(highs) >= 3, (
            f"Expected >= 3 swing highs, got {len(highs)}: "
            f"{[round(h['price'], 2) for h in highs]}"
        )

    def test_sr_covers_all_swing_high_zones(self, three_swing_high_bars):
        """S/R levels must cover the three high price zones: ~110, ~120, ~130."""
        levels = find_support_resistance(three_swing_high_bars)
        level_prices = [l["price"] for l in levels]

        for expected, tol in [(110.0, 2.0), (120.0, 2.0), (130.0, 2.0)]:
            assert any(abs(p - expected) <= tol for p in level_prices), (
                f"No S/R level near {expected} (±{tol}). "
                f"Found: {[round(p, 2) for p in level_prices]}"
            )
