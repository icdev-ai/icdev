# CUI // SP-CTI
"""cdp-wd-02 — get_driver() opt-in backend selection.

The near-zero-edit lever that runs the tests/e2e_selenium/ estate driverless: with
ICDEV_BROWSER_BACKEND=cdp, get_driver() returns the CDP WebDriver facade instead of
a Selenium driver — and every estate module already calls get_driver(), so no
per-module edit is needed. Default (unset / selenium) is unchanged, so existing
callers and the 108 agent-browser tests are unaffected.
"""
from __future__ import annotations

import importlib

dm = importlib.import_module("tools.browser.driver_manager")


def test_cdp_env_returns_the_facade(monkeypatch):
    import tools.browser.cdp.webdriver as wdmod
    monkeypatch.setattr(wdmod.CDPWebDriver, "create", classmethod(lambda cls, **kw: ("CDP_FACADE", kw)))
    monkeypatch.setenv("ICDEV_BROWSER_BACKEND", "cdp")

    out = dm.get_driver(headless=True, window_size=(800, 600))
    assert out[0] == "CDP_FACADE"
    assert out[1]["headless"] is True
    assert out[1]["window_size"] == (800, 600)


def test_default_uses_selenium_path_unchanged(monkeypatch):
    monkeypatch.delenv("ICDEV_BROWSER_BACKEND", raising=False)

    class FakeMgr:
        def create_driver(self, **kw):
            return ("SELENIUM", kw)

    monkeypatch.setattr(dm.DriverManager, "instance", classmethod(lambda cls: FakeMgr()))
    out = dm.get_driver()
    assert out[0] == "SELENIUM"


def test_selenium_env_also_uses_selenium_path(monkeypatch):
    monkeypatch.setenv("ICDEV_BROWSER_BACKEND", "selenium")

    class FakeMgr:
        def create_driver(self, **kw):
            return ("SELENIUM", kw)

    monkeypatch.setattr(dm.DriverManager, "instance", classmethod(lambda cls: FakeMgr()))
    assert dm.get_driver()[0] == "SELENIUM"


def test_cdp_branch_does_not_touch_selenium_manager(monkeypatch):
    """The facade path must NOT construct a Selenium driver (that would defeat the
    driverless point). If get_driver reached DriverManager here, the fake would
    raise."""
    import tools.browser.cdp.webdriver as wdmod
    monkeypatch.setattr(wdmod.CDPWebDriver, "create", classmethod(lambda cls, **kw: "CDP"))
    monkeypatch.setenv("ICDEV_BROWSER_BACKEND", "cdp")

    def boom(cls):
        raise AssertionError("DriverManager must not be used on the CDP path")

    monkeypatch.setattr(dm.DriverManager, "instance", classmethod(boom))
    assert dm.get_driver() == "CDP"
