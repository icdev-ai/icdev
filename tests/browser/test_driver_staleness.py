# CUI // SP-CTI
"""cdp-fix-02 — vendored-driver staleness (D2) + edge/chrome version threading (D6).

The spike measured the live defect: vendored chromedriver 147 against installed
Chrome 150, and msedgedriver empty against Edge 150 — so get_driver() resolves at
*resolve* time but fails at *launch* (chromedriver refuses a browser 3 majors
ahead). And resolve_driver() discarded the detected Edge version on every
chrome-branch resolution, so --probe reported resolved_edge_version: null even
with Edge installed.

These tests pin: (D6) both installed versions are threaded through every
resolution, and (D2) driver_staleness() flags a major mismatch with an actionable
refresh command and passes a match. All hermetic — no real browser or driver.
"""
from __future__ import annotations

import importlib
from pathlib import Path

dm = importlib.import_module("tools.browser.driver_manager")


def _vendored(browser, major, edge_version=None, chrome_version=None):
    exe = "msedgedriver" if browser == "edge" else "chromedriver"
    path = str(Path("vendor") / "drivers" / exe / str(major) / f"{exe}.exe")
    return dm.DriverResolution(
        browser=browser,
        driver_path=path,
        source="vendored",
        edge_version=edge_version,
        chrome_version=chrome_version,
    )


# ── D6: version threading through every resolution ────────────────────────────


def test_chrome_resolution_keeps_detected_edge_and_chrome_versions(monkeypatch):
    """A machine with Edge installed but resolving to a vendored chromedriver must
    still report edge_version (the D6 defect) AND chrome_version."""
    monkeypatch.setattr(dm, "_detect_edge_version", lambda: "150.0.4078.99")
    monkeypatch.setattr(dm, "_detect_chrome_version", lambda: "150.0.7871.186")
    monkeypatch.setattr(dm, "_find_vendored_msedgedriver", lambda edge_major=None: None)
    monkeypatch.setattr(dm.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        dm, "_find_vendored_chromedriver",
        lambda: Path("vendor/drivers/chromedriver/150/chromedriver.exe"),
    )

    res = dm.resolve_driver()
    assert res.browser == "chrome"
    assert res.source == "vendored"
    assert res.edge_version == "150.0.4078.99"      # D6: not discarded
    assert res.chrome_version == "150.0.7871.186"
    d = res.to_dict()
    assert d["edge_version"] and d["chrome_version"]


# ── D2: staleness detection ───────────────────────────────────────────────────


def test_stale_chromedriver_flagged_with_refresh_command():
    res = _vendored("chrome", 147, chrome_version="150.0.7871.186")
    st = dm.driver_staleness(res)
    assert st["checkable"] is True
    assert st["stale"] is True
    assert st["driver_major"] == "147"
    assert st["browser_major"] == "150"
    assert "--fetch-chrome --major 150" in st["reason"]


def test_stale_msedgedriver_flagged_with_edge_refresh():
    res = _vendored("edge", 147, edge_version="150.0.4078.99")
    st = dm.driver_staleness(res)
    assert st["stale"] is True
    assert "--fetch-edge" in st["reason"]


def test_matching_major_is_not_stale():
    res = _vendored("chrome", 150, chrome_version="150.0.7871.186")
    st = dm.driver_staleness(res)
    assert st["checkable"] is True
    assert st["stale"] is False


def test_path_driver_is_not_staleness_checkable():
    res = dm.DriverResolution(browser="chrome", driver_path="/usr/bin/chromedriver", source="path")
    st = dm.driver_staleness(res)
    assert st["checkable"] is False
    assert st["stale"] is False


def test_undetected_browser_is_checkable_but_not_stale():
    """A vendored driver present but the browser undetected: we cannot call it
    stale (no major to compare), and must not false-positive."""
    res = _vendored("chrome", 150, chrome_version=None)
    st = dm.driver_staleness(res)
    assert st["checkable"] is True
    assert st["stale"] is False
    assert "not detected" in st["reason"]


# ── Surfaces: probe + manager warning ─────────────────────────────────────────


def test_probe_reports_chrome_version_and_staleness(monkeypatch):
    monkeypatch.setattr(dm, "resolve_driver", lambda: _vendored("chrome", 147, chrome_version="150.0.1.2"))
    dm.DriverManager.reset()
    info = dm.DriverManager.instance().probe()
    dm.DriverManager.reset()
    assert "resolved_chrome_version" in info
    assert info["staleness"]["stale"] is True


def test_manager_warns_on_stale_vendored_driver(monkeypatch):
    """Init emits a loud warning so the launch-time failure is not cryptic.
    Asserted against the module logger directly — the icdev logger does not
    propagate to root, so caplog would not see it."""
    warnings = []
    monkeypatch.setattr(dm, "resolve_driver", lambda: _vendored("chrome", 147, chrome_version="150.0.1.2"))
    monkeypatch.setattr(dm.logger, "warning", lambda msg, *a, **k: warnings.append(msg % a if a else msg))
    dm.DriverManager.reset()
    dm.DriverManager.instance()
    dm.DriverManager.reset()
    assert any("STALE" in w for w in warnings)
