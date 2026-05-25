"""Tests for adaptive_stops + vix_weight_rotation."""

from unittest import mock

from tools.trading.analysis import vix_weight_rotation as vwr
from tools.trading.risk import adaptive_stops as asm


# adaptive_stops


def test_low_vix_keeps_base():
    out = asm.adjust(0.03, 0.06, vix=12.0)
    assert out["multiplier"] == 1.0
    assert out["stop_pct"] == 0.03


def test_high_vix_widens_stop():
    out = asm.adjust(0.03, 0.06, vix=30.0)
    assert out["multiplier"] == 2.0
    assert out["stop_pct"] > 0.03
    assert out["target_pct"] > 0.06


def test_clipped_at_max():
    out = asm.adjust(0.05, 0.10, vix=100.0)
    assert out["stop_pct"] <= asm.MAX_STOP
    assert out["target_pct"] <= asm.MAX_TARGET


def test_unknown_vix_is_safe():
    with mock.patch("tools.trading.risk.vix_sizing.current_vix", return_value=None):
        out = asm.adjust(0.03, 0.06, vix=None)
    assert out["reason"] == "vix_unavailable"
    assert out["multiplier"] == 1.0


def test_negative_vix_is_safe():
    out = asm.adjust(0.03, 0.06, vix=-1)
    assert out["reason"] == "vix_unavailable"


# vix_weight_rotation


BASE = {"fundamental": 0.20, "technical": 0.25, "sentiment": 0.15, "news": 0.10, "macro": 0.20, "perspective": 0.10}


def test_low_vol_boosts_technical():
    out = vwr.rotate(BASE, vix=12.0)
    assert out["regime"] == "low_vol"
    assert out["weights"]["technical"] > BASE["technical"] * 0.95


def test_high_vol_boosts_fundamental():
    out = vwr.rotate(BASE, vix=30.0)
    assert out["regime"] == "high_vol"
    assert out["weights"]["fundamental"] > BASE["fundamental"]
    assert out["weights"]["news"] < BASE["news"]


def test_neutral_keeps_baseline():
    out = vwr.rotate(BASE, vix=20.0)
    assert out["regime"] == "neutral"
    # Renormalization may shift slightly but not materially
    for k in BASE:
        assert abs(out["weights"][k] - BASE[k]) < 1e-3


def test_weights_renormalize_to_one():
    for v in (10, 18, 35):
        out = vwr.rotate(BASE, vix=v)
        assert abs(sum(out["weights"].values()) - 1.0) < 1e-3


def test_unknown_vix_returns_base():
    with mock.patch("tools.trading.risk.vix_sizing.current_vix", return_value=None):
        out = vwr.rotate(BASE, vix=None)
    assert out["regime"] == "unknown"
    assert out["weights"] == BASE
