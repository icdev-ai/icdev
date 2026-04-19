"""Tests for tools.trading.rollout.preset_loader."""

from unittest import mock

import pytest

from tools.trading.rollout import preset_loader


def test_list_tiers_returns_all_four():
    tiers = preset_loader.list_tiers()
    names = {t["tier"] for t in tiers}
    assert names == {"micro_live", "scale_10k", "scale_25k", "scale_100k"}
    for t in tiers:
        assert t["exists"] is True


def test_load_unknown_tier_raises():
    with pytest.raises(ValueError):
        preset_loader.load("ludicrous_speed", allow_unsafe=True)


def test_load_refuses_without_env_match(monkeypatch):
    monkeypatch.delenv("ICDEV_TRADING_TIER", raising=False)
    with pytest.raises(ValueError, match="ICDEV_TRADING_TIER"):
        preset_loader.load("micro_live")


def test_load_succeeds_with_env_match(monkeypatch):
    monkeypatch.setenv("ICDEV_TRADING_TIER", "micro_live")
    cfg = preset_loader.load("micro_live")
    assert cfg["fathomdesk"]["rollout_phase"] == "micro_live"
    assert cfg["fathomdesk"]["risk"]["max_portfolio_usd"] == 5000.0


def test_load_allow_unsafe_bypasses_gate(monkeypatch):
    monkeypatch.delenv("ICDEV_TRADING_TIER", raising=False)
    cfg = preset_loader.load("scale_100k", allow_unsafe=True)
    assert cfg["fathomdesk"]["rollout_phase"] == "scale_100k"


def test_short_blocked_when_preset_disables():
    preset = {"fathomdesk": {"shorting": {"enabled": False}}}
    out = preset_loader.short_allowed(preset)
    assert out["allowed"] is False
    assert out["reason"] == "preset_disables_shorting"


def test_short_blocked_when_locate_missing():
    preset = {"fathomdesk": {"shorting": {"enabled": True, "require_locate": True}}}
    out = preset_loader.short_allowed(preset, locate_ok=None)
    assert out["allowed"] is False
    assert "locate" in out["reason"]


def test_short_blocked_when_kill_switch_tripped():
    preset = {"fathomdesk": {"shorting": {"enabled": True, "require_locate": False}}}
    with mock.patch("tools.trading.risk.kill_switch.is_killed", return_value={"killed": True, "sources": [{"source": "test"}], "reason": "test"}):
        out = preset_loader.short_allowed(preset)
    assert out["allowed"] is False
    assert out["reason"] == "kill_switch_active"


def test_short_allowed_when_all_clear():
    preset = {"fathomdesk": {"shorting": {"enabled": True, "require_locate": True}}}
    with mock.patch("tools.trading.risk.kill_switch.is_killed", return_value={"killed": False, "sources": [], "reason": None}):
        out = preset_loader.short_allowed(preset, locate_ok=True)
    assert out["allowed"] is True
    assert out["reason"] == "ok"
