# CUI // SP-CTI
"""cdp-port-02 — shared, network-free browser executable locator.

Pins the preference order (Edge -> Chrome -> Chromium), the loud-degradation
None return when nothing is found, and version delegation to driver_manager's
detectors. Hermetic — the per-family locators are monkeypatched, so no real
browser or registry is touched.
"""
from __future__ import annotations

import importlib
from pathlib import Path

bl = importlib.import_module("tools.browser.browser_locator")


def _fake_locator(path):
    return lambda: Path(path) if path else None


def test_prefers_edge_then_chrome_then_chromium(monkeypatch):
    monkeypatch.setitem(bl._LOCATORS, "edge", _fake_locator("/x/msedge"))
    monkeypatch.setitem(bl._LOCATORS, "chrome", _fake_locator("/x/chrome"))
    monkeypatch.setitem(bl._LOCATORS, "chromium", _fake_locator("/x/chromium"))
    monkeypatch.setattr(bl, "_resolve_version", lambda fam, exe: "150.0.1.2")

    loc = bl.locate_browser()
    assert loc.family == "edge"
    assert loc.executable == str(Path("/x/msedge"))


def test_falls_through_to_chrome_when_edge_absent(monkeypatch):
    monkeypatch.setitem(bl._LOCATORS, "edge", _fake_locator(None))
    monkeypatch.setitem(bl._LOCATORS, "chrome", _fake_locator("/x/chrome"))
    monkeypatch.setitem(bl._LOCATORS, "chromium", _fake_locator(None))
    monkeypatch.setattr(bl, "_resolve_version", lambda fam, exe: None)

    loc = bl.locate_browser()
    assert loc.family == "chrome"


def test_none_when_no_browser_found(monkeypatch):
    for fam in bl.FAMILIES:
        monkeypatch.setitem(bl._LOCATORS, fam, _fake_locator(None))
    assert bl.locate_browser() is None


def test_locate_all_returns_present_in_order(monkeypatch):
    monkeypatch.setitem(bl._LOCATORS, "edge", _fake_locator("/x/msedge"))
    monkeypatch.setitem(bl._LOCATORS, "chrome", _fake_locator(None))
    monkeypatch.setitem(bl._LOCATORS, "chromium", _fake_locator("/x/chromium"))
    monkeypatch.setattr(bl, "_resolve_version", lambda fam, exe: "150.0.0.0")

    families = [loc.family for loc in bl.locate_all()]
    assert families == ["edge", "chromium"]


def test_custom_preference_order(monkeypatch):
    monkeypatch.setitem(bl._LOCATORS, "edge", _fake_locator("/x/msedge"))
    monkeypatch.setitem(bl._LOCATORS, "chrome", _fake_locator("/x/chrome"))
    monkeypatch.setattr(bl, "_resolve_version", lambda fam, exe: None)

    loc = bl.locate_browser(prefer=("chrome", "edge"))
    assert loc.family == "chrome"


def test_major_derived_from_version():
    loc = bl.BrowserLocation(family="chrome", executable="/x/chrome", version="150.0.7871.186")
    assert loc.major == "150"
    assert bl.BrowserLocation(family="edge", executable="/x", version=None).major is None
    assert loc.to_dict()["major"] == "150"


def test_resolve_version_delegates_to_driver_manager(monkeypatch):
    import tools.browser.driver_manager as dm
    monkeypatch.setattr(dm, "_detect_edge_version", lambda: "150.0.4078.99")
    monkeypatch.setattr(dm, "_detect_chrome_version", lambda: "150.0.7871.186")
    assert bl._resolve_version("edge", Path("/x/msedge")) == "150.0.4078.99"
    assert bl._resolve_version("chrome", Path("/x/chrome")) == "150.0.7871.186"
