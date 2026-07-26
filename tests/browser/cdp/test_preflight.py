# CUI // SP-CTI
"""cdp-port-06 — policy/tier preflight.

The load-bearing invariant (spike cdp-00 §4.7.1): RemoteDebuggingAllowed=0 kills
BOTH CDP and Selenium (chromedriver drives over CDP too), so a restrictive policy
selects Tier 3, not Tier 2. Unset means PERMITTED (Chromium default). These tests
pin the tier decision table and the deterministic policy read — no browser is
ever launched.
"""
from __future__ import annotations

import importlib
import json

pf = importlib.import_module("tools.browser.cdp.preflight")


def _policy(allowed, source="test"):
    return pf.PolicyResult(allowed=allowed, source=source)


# ── The tier decision table ───────────────────────────────────────────────────


def test_policy_forbids_selects_tier3_not_selenium():
    """A restrictive policy must NOT fall back to Selenium — it is dead too."""
    d = pf.select_tier(_policy(False, "registry:HKLM\\...Edge"), browser_present=True, requested="auto")
    assert d.tier == 3
    assert d.name == "http-only"
    assert "Selenium" in d.reason  # states both transports are blocked
    assert d.lost_at_this_tier  # the loss is enumerated, not silent


def test_policy_forbids_overrides_explicit_selenium_request():
    d = pf.select_tier(_policy(False), browser_present=True, requested="selenium")
    assert d.tier == 3  # even an explicit selenium ask cannot beat the policy


def test_permitted_unset_plus_browser_auto_selects_cdp():
    d = pf.select_tier(_policy(None), browser_present=True, requested="auto")
    assert d.tier == 1
    assert d.name == "cdp"
    assert "unset default" in d.reason


def test_permitted_explicit_true_plus_browser_selects_cdp():
    d = pf.select_tier(_policy(True), browser_present=True, requested="auto")
    assert d.tier == 1
    assert "explicitly permitted" in d.reason


def test_permitted_but_no_browser_is_tier3():
    d = pf.select_tier(_policy(None), browser_present=False, requested="auto")
    assert d.tier == 3
    assert "no Chromium-family browser" in d.reason


def test_selenium_requested_and_permitted_is_tier2():
    d = pf.select_tier(_policy(None), browser_present=True, requested="selenium")
    assert d.tier == 2
    assert d.name == "selenium"
    assert "compatibility" in d.reason


def test_forbids_debugging_property():
    assert _policy(False).forbids_debugging is True
    assert _policy(True).forbids_debugging is False
    assert _policy(None).forbids_debugging is False  # unset != forbidden


# ── Deterministic policy read ─────────────────────────────────────────────────


def test_linux_policy_file_read(monkeypatch, tmp_path):
    pol = tmp_path / "managed.json"
    pol.write_text(json.dumps({"RemoteDebuggingAllowed": 0, "OtherPolicy": 1}), encoding="utf-8")
    monkeypatch.setattr(pf.platform, "system", lambda: "Linux")
    monkeypatch.setattr(pf, "_LINUX_POLICY_GLOBS", [str(tmp_path / "*.json")])
    result = pf.read_remote_debugging_policy()
    assert result.allowed is False
    assert result.forbids_debugging is True
    assert "managed.json" in result.source


def test_linux_policy_absent_means_unset_permitted(monkeypatch, tmp_path):
    monkeypatch.setattr(pf.platform, "system", lambda: "Linux")
    monkeypatch.setattr(pf, "_LINUX_POLICY_GLOBS", [str(tmp_path / "nonexistent" / "*.json")])
    result = pf.read_remote_debugging_policy()
    assert result.allowed is None       # unset
    assert result.forbids_debugging is False  # ...which is PERMITTED


def test_linux_policy_permits_when_explicitly_one(monkeypatch, tmp_path):
    (tmp_path / "m.json").write_text(json.dumps({"RemoteDebuggingAllowed": 1}), encoding="utf-8")
    monkeypatch.setattr(pf.platform, "system", lambda: "Linux")
    monkeypatch.setattr(pf, "_LINUX_POLICY_GLOBS", [str(tmp_path / "*.json")])
    assert pf.read_remote_debugging_policy().allowed is True


# ── Browser presence + integration ────────────────────────────────────────────


def test_detect_browser_present_delegates_to_detectors(monkeypatch):
    import tools.browser.driver_manager as dm
    monkeypatch.setattr(dm, "_detect_edge_version", lambda: None)
    monkeypatch.setattr(dm, "_detect_chrome_version", lambda: "150.0.1.2")
    assert pf.detect_browser_present() is True
    monkeypatch.setattr(dm, "_detect_chrome_version", lambda: None)
    assert pf.detect_browser_present() is False


def test_preflight_report_shape(monkeypatch):
    monkeypatch.setattr(pf, "read_remote_debugging_policy", lambda: _policy(None, "unset"))
    monkeypatch.setattr(pf, "detect_browser_present", lambda: True)
    report = pf.preflight(requested="auto")
    assert report["decision"]["tier"] == 1
    assert report["browser_present"] is True
    assert report["policy"]["allowed"] is None
    # fully JSON-serialisable
    json.dumps(report)
