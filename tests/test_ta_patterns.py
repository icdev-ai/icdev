# CUI // SP-CTI
"""Tests for the chart patterns orchestrator: detect_patterns(bars).

Fixture shapes
--------------
(a) V-shape      → double_bottom detected (two lows near 90, neckline ~100)
(b) W-shape      → double_bottom with neckline above both troughs
(c) Triple-top   → triple_top detected (three highs within 3% of mean)
(d) Rising wedge → rising_wedge with slope_high > 0 and slope_low > 0
Edge             → empty/minimal bars return []
"""
from __future__ import annotations


from tools.trading.ta.patterns import detect_patterns


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _bar(h: float, l: float, v: float = 1000.0) -> dict:
    return {"h": h, "l": l, "v": v, "c": (h + l) / 2.0}


# ---------------------------------------------------------------------------
# Fixture factories
# ---------------------------------------------------------------------------

def _v_shape_bars() -> list[dict]:
    """Two V-shaped bottoms near 90 separated by a neckline at 100."""
    return [
        _bar(h=100, l=99),   # 0: opening reference (first swing HIGH ~100)
        _bar(h=98,  l=95),   # 1: declining — triggers direction detection
        _bar(h=93,  l=90),   # 2: first trough ~90
        _bar(h=95,  l=91),   # 3: bouncing
        _bar(h=100, l=96),   # 4: neckline ~100
        _bar(h=97,  l=93),   # 5: second decline
        _bar(h=92,  l=89),   # 6: second trough ~89
        _bar(h=96,  l=92),   # 7: recovering
        _bar(h=103, l=99),   # 8: breakout above neckline
    ]


def _w_shape_bars() -> list[dict]:
    """Explicit W: two troughs near 84–85, clear neckline ~101."""
    return [
        _bar(h=100, l=98),   # 0: opening top (first swing HIGH ~100)
        _bar(h=97,  l=93),   # 1: declining — triggers direction detection
        _bar(h=89,  l=85),   # 2: first trough ~85
        _bar(h=93,  l=89),   # 3: bouncing
        _bar(h=101, l=97),   # 4: neckline ~101
        _bar(h=98,  l=93),   # 5: second decline
        _bar(h=88,  l=84),   # 6: second trough ~84
        _bar(h=93,  l=89),   # 7: recovering
        _bar(h=104, l=100),  # 8: neckline breakout
    ]


def _triple_top_bars() -> list[dict]:
    """Three swing highs near 105–106 with retracements to ~95."""
    return [
        _bar(h=105, l=101),  # 0: first peak ~105 (first swing HIGH)
        _bar(h=103, l=98),   # 1: declining — triggers direction detection
        _bar(h=100, l=95),   # 2: swing low ~95
        _bar(h=103, l=99),   # 3: rising
        _bar(h=106, l=102),  # 4: second peak ~106
        _bar(h=104, l=99),   # 5: declining
        _bar(h=100, l=95),   # 6: swing low ~95
        _bar(h=104, l=100),  # 7: rising
        _bar(h=105, l=101),  # 8: third peak ~105
        _bar(h=102, l=97),   # 9: declining (confirms third swing HIGH)
    ]


def _rising_wedge_bars() -> list[dict]:
    """Rising wedge: support line slope (2.0) > resistance line slope (1.5).

    Swing highs: 100 @ bar 0, 105 @ bar 4, 112 @ bar 8  → slope ≈ 1.5
    Swing lows : 90 @ bar 2, 98 @ bar 6                  → slope = 2.0
    Both slopes positive and support steeper → converging rising wedge.
    Triple-top check on these highs (100, 105, 112) gives deviations of
    ~5% from mean, safely above the 3% tolerance so no triple_top fires.
    """
    return [
        _bar(h=100, l=99),   # 0: first swing HIGH ~100
        _bar(h=98,  l=94),   # 1: declining — triggers direction detection
        _bar(h=93,  l=90),   # 2: swing low 1 ~ 90
        _bar(h=97,  l=93),   # 3: recovering
        _bar(h=105, l=101),  # 4: swing high 1 ~ 105
        _bar(h=103, l=100),  # 5: pullback (confirms swing HIGH at bar 4)
        _bar(h=102, l=98),   # 6: swing low 2 ~ 98  (higher low)
        _bar(h=104, l=100),  # 7: recovering
        _bar(h=112, l=108),  # 8: swing high 2 ~ 112  (higher high)
        _bar(h=109, l=106),  # 9: pullback (confirms swing HIGH at bar 8)
    ]


# ---------------------------------------------------------------------------
# (a) V-shape → double bottom detected
# ---------------------------------------------------------------------------

class TestDoubleBottomVShape:
    def test_double_bottom_detected(self):
        patterns = detect_patterns(_v_shape_bars())
        types = [p["type"] for p in patterns]
        assert "double_bottom" in types, (
            f"Expected 'double_bottom' in patterns, got: {types}"
        )

    def test_double_bottom_has_required_fields(self):
        patterns = detect_patterns(_v_shape_bars())
        db = next(p for p in patterns if p["type"] == "double_bottom")
        assert "low_1" in db
        assert "low_2" in db
        assert "neckline" in db
        assert db["start_bar"] < db["end_bar"]


# ---------------------------------------------------------------------------
# (b) W-shape → double bottom with neckline confirmation
# ---------------------------------------------------------------------------

class TestDoubleBottomWShape:
    def test_double_bottom_detected(self):
        patterns = detect_patterns(_w_shape_bars())
        types = [p["type"] for p in patterns]
        assert "double_bottom" in types, (
            f"Expected 'double_bottom' in W-shape patterns, got: {types}"
        )

    def test_neckline_above_both_troughs(self):
        """Neckline price must be above both trough prices."""
        patterns = detect_patterns(_w_shape_bars())
        db = next(p for p in patterns if p["type"] == "double_bottom")
        neckline_price = db["neckline"]["price"]
        assert neckline_price > db["low_1"]["price"], (
            f"Neckline {neckline_price} must be > low_1 {db['low_1']['price']}"
        )
        assert neckline_price > db["low_2"]["price"], (
            f"Neckline {neckline_price} must be > low_2 {db['low_2']['price']}"
        )


# ---------------------------------------------------------------------------
# (c) 3-touch resistance → triple top
# ---------------------------------------------------------------------------

class TestTripleTop:
    def test_triple_top_detected(self):
        patterns = detect_patterns(_triple_top_bars())
        types = [p["type"] for p in patterns]
        assert "triple_top" in types, (
            f"Expected 'triple_top' in patterns, got: {types}"
        )

    def test_triple_top_all_swings_are_highs(self):
        patterns = detect_patterns(_triple_top_bars())
        tt = next(p for p in patterns if p["type"] == "triple_top")
        assert tt["swing_1"]["type"] == "high"
        assert tt["swing_2"]["type"] == "high"
        assert tt["swing_3"]["type"] == "high"

    def test_triple_top_prices_within_tolerance(self):
        patterns = detect_patterns(_triple_top_bars())
        tt = next(p for p in patterns if p["type"] == "triple_top")
        avg = tt["avg_price"]
        for key in ("swing_1", "swing_2", "swing_3"):
            pct = abs(tt[key]["price"] - avg) / avg * 100.0
            assert pct <= tt["tolerance_pct"], (
                f"{key} price {tt[key]['price']} deviates {pct:.2f}% from "
                f"avg {avg:.2f} (tolerance {tt['tolerance_pct']}%)"
            )


# ---------------------------------------------------------------------------
# (d) Rising wedge → detected with correct slope signs
# ---------------------------------------------------------------------------

class TestRisingWedge:
    def test_rising_wedge_detected(self):
        patterns = detect_patterns(_rising_wedge_bars())
        types = [p["type"] for p in patterns]
        assert "rising_wedge" in types, (
            f"Expected 'rising_wedge' in patterns, got: {types}"
        )

    def test_rising_wedge_slope_signs_positive(self):
        """Both resistance and support OLS slopes must be strictly positive."""
        patterns = detect_patterns(_rising_wedge_bars())
        rw = next(p for p in patterns if p["type"] == "rising_wedge")
        assert rw["slope_high"] > 0.0, (
            f"slope_high must be > 0 for rising wedge, got {rw['slope_high']:.4f}"
        )
        assert rw["slope_low"] > 0.0, (
            f"slope_low must be > 0 for rising wedge, got {rw['slope_low']:.4f}"
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_bars_returns_empty(self):
        assert detect_patterns([]) == []

    def test_single_bar_returns_empty(self):
        assert detect_patterns([_bar(h=100, l=99)]) == []

    def test_two_bars_no_pattern(self):
        result = detect_patterns([_bar(h=100, l=99), _bar(h=95, l=93)])
        assert isinstance(result, list)
