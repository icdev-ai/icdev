# CUI // SP-CTI
"""cdp-fix-01 — the air-gap driver guarantee must be real (fail closed).

The spike (docs/spikes/cdp-00-*) found that `get_driver()` documented raising
`AirgapDriverMissingError` and "Never triggers a CDN download", but that
exception did not exist and the real fall-through returned `driver_path=None`,
handing control to Selenium Manager — which resolves drivers *by downloading
them*. In an air-gapped environment that is a guarantee violation: the operator
gets a confusing network timeout from a component promised never to reach the
network, instead of an actionable "no vendored driver — run the admin refresh".

These tests pin the fixed behaviour. They are hermetic — selenium is blocked via
sys.modules so nothing is ever launched or downloaded. In air-gap mode the guard
must fire BEFORE the selenium import; in commercial mode the guard must be passed
(reaching the import, which then fails with ImportError, not AirgapDriverMissingError).
"""
from __future__ import annotations

import importlib

import pytest

dm = importlib.import_module("tools.browser.driver_manager")


@pytest.fixture
def manager_without_driver(monkeypatch):
    """A DriverManager whose resolution fell through to Selenium Manager."""
    resolution = dm.DriverResolution(
        browser="chrome", driver_path=None, source="selenium_manager"
    )
    monkeypatch.setattr(dm, "resolve_driver", lambda: resolution)
    dm.DriverManager.reset()
    mgr = dm.DriverManager.instance()
    assert mgr.resolved_driver_path is None
    yield mgr
    dm.DriverManager.reset()


def _block_selenium(monkeypatch):
    """Make `from selenium import webdriver` raise ImportError, hermetically."""
    monkeypatch.setitem(importlib.import_module("sys").modules, "selenium", None)


# ── The exception exists and is the documented type ──────────────────────────


def test_airgap_error_exists_and_is_runtime_error():
    assert issubclass(dm.AirgapDriverMissingError, RuntimeError)


# ── Air-gap mode: fail closed, before any download or selenium import ─────────


def test_create_driver_fails_closed_in_airgap(manager_without_driver, monkeypatch):
    monkeypatch.setattr(dm, "_airgap_active", lambda: True)
    _block_selenium(monkeypatch)  # even with selenium present the guard is earlier

    with pytest.raises(dm.AirgapDriverMissingError) as exc:
        manager_without_driver.create_driver()

    msg = str(exc.value)
    # actionable: names the refresh command and the searched locations
    assert "driver_vendor.py" in msg
    assert "--fetch-chrome" in msg or "--fetch-edge" in msg
    assert "vendor" in msg.lower()


def test_airgap_guard_precedes_selenium_import(manager_without_driver, monkeypatch):
    """The raise must not depend on selenium being importable — it is a
    resolution failure, not a selenium failure."""
    monkeypatch.setattr(dm, "_airgap_active", lambda: True)
    _block_selenium(monkeypatch)
    with pytest.raises(dm.AirgapDriverMissingError):
        manager_without_driver.create_driver()


# ── Commercial mode: guard is passed (no false fail-closed) ───────────────────


def test_commercial_mode_does_not_raise_airgap_error(manager_without_driver, monkeypatch):
    """With air-gap off, a None driver_path must NOT raise AirgapDriverMissingError.
    It should proceed to the selenium import (here blocked → ImportError), proving
    the guard let it through rather than launching/downloading a browser."""
    monkeypatch.setattr(dm, "_airgap_active", lambda: False)
    _block_selenium(monkeypatch)

    with pytest.raises(ImportError):
        manager_without_driver.create_driver()


# ── _airgap_active honours the explicit env override ─────────────────────────


def test_airgap_active_honours_env(monkeypatch):
    # Force the airgap package import to fail so the env fallback is exercised.
    monkeypatch.setitem(importlib.import_module("sys").modules, "tools.airgap.detector", None)
    monkeypatch.setenv("ICDEV_AIRGAP", "true")
    assert dm._airgap_active() is True
    monkeypatch.setenv("ICDEV_AIRGAP", "")
    assert dm._airgap_active() is False
