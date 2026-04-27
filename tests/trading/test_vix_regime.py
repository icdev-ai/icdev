"""Tests for tools.trading.analysis.vix_weight_rotation VIX regime logic."""

from tools.trading.analysis.vix_weight_rotation import (
    VIXRegime,
    get_vix_regime,
    machine_momentum_modifier,
)


def test_low_regime():
    assert get_vix_regime(12.0) == VIXRegime.LOW


def test_normal_regime():
    assert get_vix_regime(18.0) == VIXRegime.NORMAL


def test_elevated_regime():
    assert get_vix_regime(25.0) == VIXRegime.ELEVATED


def test_stress_regime():
    assert get_vix_regime(35.0) == VIXRegime.STRESS


def test_spiking_regime():
    assert get_vix_regime(35.0, vix_delta=6.0) == VIXRegime.SPIKING


def test_boundary_stress_vs_spiking_no_delta():
    # VIX > 30 but delta <= 5 → STRESS, not SPIKING
    assert get_vix_regime(31.0, vix_delta=5.0) == VIXRegime.STRESS


def test_enum_has_five_values():
    assert len(VIXRegime) == 5


def test_low_modifier_size_multiplier():
    m = machine_momentum_modifier(VIXRegime.LOW)
    assert m["size_multiplier"] == 1.2
    assert m["doom_loop_alert"] is False


def test_spiking_modifier_blocks_size():
    m = machine_momentum_modifier(VIXRegime.SPIKING)
    assert m["size_multiplier"] == 0.0
    assert m["doom_loop_alert"] is True


def test_stress_modifier_doom_loop():
    m = machine_momentum_modifier(VIXRegime.STRESS)
    assert m["size_multiplier"] == 0.2
    assert m["doom_loop_alert"] is True


def test_modifier_returns_copy():
    m1 = machine_momentum_modifier(VIXRegime.NORMAL)
    m1["size_multiplier"] = 99
    m2 = machine_momentum_modifier(VIXRegime.NORMAL)
    assert m2["size_multiplier"] == 1.0
