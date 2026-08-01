# CUI // SP-CTI
"""cdp-port-04 — browser backend selection.

Pins the declared-order resolution (auto → CDP → Tier 3, never silently Selenium)
and the never-degrade rule for explicit requests (spike cdp-00 §4.6). Hermetic —
preflight and the two constructors are monkeypatched; no browser is launched.
"""
from __future__ import annotations

import importlib

import pytest

backend = importlib.import_module("tools.browser.backend")


def _decision(name, tier, reason="because"):
    return {"decision": {"name": name, "tier": tier, "reason": reason}}


def _patch_preflight(monkeypatch, name, tier):
    monkeypatch.setattr(backend, "preflight", lambda requested="auto": _decision(name, tier))


# ── resolution: declared order ────────────────────────────────────────────────


def test_auto_resolves_to_cdp_when_tier1(monkeypatch):
    _patch_preflight(monkeypatch, "cdp", 1)
    res = backend.resolve_backend("auto")
    assert res.backend == backend.BACKEND_CDP
    assert res.tier == 1


def test_auto_resolves_to_tier3_when_no_transport(monkeypatch):
    _patch_preflight(monkeypatch, "http-only", 3)
    res = backend.resolve_backend("auto")
    assert res.backend == backend.BACKEND_HTTP_ONLY
    assert res.tier == 3


def test_explicit_selenium_permitted_is_selenium(monkeypatch):
    _patch_preflight(monkeypatch, "selenium", 2)
    res = backend.resolve_backend("selenium")
    assert res.backend == backend.BACKEND_SELENIUM


# ── never-degrade rule for explicit requests ──────────────────────────────────


def test_explicit_cdp_raises_when_unavailable(monkeypatch):
    _patch_preflight(monkeypatch, "http-only", 3)  # preflight downgraded it
    with pytest.raises(backend.BackendUnavailable):
        backend.resolve_backend("cdp")


def test_explicit_selenium_raises_when_policy_forbids(monkeypatch):
    _patch_preflight(monkeypatch, "http-only", 3)
    with pytest.raises(backend.BackendUnavailable):
        backend.resolve_backend("selenium")


def test_invalid_request_raises(monkeypatch):
    with pytest.raises(backend.BackendUnavailable):
        backend.resolve_backend("firefox")


# ── env var ───────────────────────────────────────────────────────────────────


def test_env_var_is_honoured(monkeypatch):
    _patch_preflight(monkeypatch, "cdp", 1)
    monkeypatch.setenv("ICDEV_BROWSER_BACKEND", "cdp")
    assert backend.resolve_backend().requested == "cdp"


def test_explicit_arg_overrides_env(monkeypatch):
    _patch_preflight(monkeypatch, "selenium", 2)
    monkeypatch.setenv("ICDEV_BROWSER_BACKEND", "cdp")
    assert backend.resolve_backend("selenium").requested == "selenium"


# ── construction dispatch ─────────────────────────────────────────────────────


def test_create_backend_dispatches_to_cdp(monkeypatch):
    _patch_preflight(monkeypatch, "cdp", 1)
    import tools.browser.cdp.driver as drv
    monkeypatch.setattr(drv.CDPDriver, "create", classmethod(lambda cls, **kw: ("CDP", kw)))
    out = backend.create_backend("cdp", headless=False)
    assert out[0] == "CDP"
    assert out[1]["headless"] is False


def test_create_backend_dispatches_to_selenium(monkeypatch):
    _patch_preflight(monkeypatch, "selenium", 2)
    import tools.browser.driver_manager as dm

    class FakeMgr:
        def create_driver(self, **kw):
            return ("SELENIUM", kw)

    monkeypatch.setattr(dm.DriverManager, "instance", classmethod(lambda cls: FakeMgr()))
    out = backend.create_backend("selenium")
    assert out[0] == "SELENIUM"


def test_create_backend_raises_loudly_at_tier3(monkeypatch):
    _patch_preflight(monkeypatch, "http-only", 3)
    with pytest.raises(backend.BackendUnavailable) as exc:
        backend.create_backend("auto")
    # points at the surviving Tier-3 verification, not a dead end
    assert "route_smoke" in str(exc.value)
