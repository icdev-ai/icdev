"""Tests for tools.trading.risk.vix_sizing."""

from tools.trading.risk import vix_sizing


def test_neutral_vix_gives_unit_scale():
    d = vix_sizing.scale_for_vix(12.0)
    assert d.scale == 1.0
    assert d.regime == "low_vol"
    assert d.reason == "ok"


def test_low_vix_hits_max_via_floor():
    # VIX below VIX_FLOOR is raised to the floor; denominator = 8, raw = 12/8 = 1.5 = MAX_SCALE.
    d = vix_sizing.scale_for_vix(6.0)
    assert d.scale == vix_sizing.MAX_SCALE


def test_intermediate_low_vix_clips_to_max():
    # VIX 7.5 still caught by floor; raw=1.5 hits MAX_SCALE boundary.
    d = vix_sizing.scale_for_vix(7.5)
    assert d.scale == vix_sizing.MAX_SCALE


def test_high_vix_clips_to_min():
    d = vix_sizing.scale_for_vix(45.0)
    assert d.scale == vix_sizing.MIN_SCALE
    assert d.reason == "clipped_min"
    assert d.regime == "stressed"


def test_elevated_vix_scales_down_smoothly():
    d = vix_sizing.scale_for_vix(24.0)  # raw = 12/24 = 0.5, not clipped
    assert d.reason == "ok"
    assert 0.45 < d.scale < 0.55
    assert d.regime == "elevated"


def test_none_vix_is_safe_default():
    d = vix_sizing.scale_for_vix(None)
    assert d.scale == 1.0
    assert d.reason == "vix_unavailable"


def test_invalid_vix_is_safe_default():
    d = vix_sizing.scale_for_vix("not a number")
    assert d.scale == 1.0
    assert d.reason == "vix_invalid"


def test_nonpositive_vix_is_safe_default():
    d = vix_sizing.scale_for_vix(-1.0)
    assert d.scale == 1.0
    assert d.reason == "vix_nonpositive"


def test_size_qty_scales_integer_and_floors_at_one():
    out = vix_sizing.size_qty(100, vix=24.0)
    assert out["base_qty"] == 100
    assert 45 <= out["scaled_qty"] <= 55
    # Extreme high-VIX should still floor at 1 share for small base
    tiny = vix_sizing.size_qty(2, vix=50.0)
    assert tiny["scaled_qty"] == 1


def test_size_qty_respects_max_clip():
    out = vix_sizing.size_qty(100, vix=6.0)
    assert out["scaled_qty"] == int(100 * vix_sizing.MAX_SCALE)


def test_size_qty_passes_through_metadata():
    out = vix_sizing.size_qty(10, vix=18.0)
    assert out["vix"] == 18.0
    assert out["regime"] == "neutral"
    assert "vix_scale" in out
