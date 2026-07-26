# CUI // SP-CTI
"""cdp-vv-01 — the driverless CDP V&V as a gated integration test.

Skips unless a Chromium-family browser is installed AND a dashboard is reachable —
this is a live proof, not a unit test. When both are present it launches the browser
over CDP (no driver binary) and reproduces the dashboard-home e2e assertions through
the WebDriver facade. The hermetic coverage of the facade lives in test_webdriver.py;
this is the end-to-end truth check.
"""
from __future__ import annotations

import os
import socket

import pytest

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")


def _reachable(url: str) -> bool:
    try:
        host = url.split("://", 1)[-1].split("/", 1)[0]
        hostname, _, port = host.partition(":")
        with socket.create_connection((hostname, int(port or 80)), timeout=1.5):
            return True
    except Exception:
        return False


def _browser_present() -> bool:
    try:
        from tools.browser.browser_locator import locate_browser
        return locate_browser() is not None
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _browser_present(), reason="no Chromium-family browser installed"),
    pytest.mark.skipif(not _reachable(BASE_URL), reason=f"dashboard not reachable at {BASE_URL}"),
]


def test_dashboard_home_reproduces_driverless():
    from tools.testing.cdp_driverless_vv import run_vv

    report = run_vv(BASE_URL, headless=True)
    assert report["ok"], f"driverless CDP reproduction failed: {report}"
    assert report["driver_binary_used"] is False
    checks = report["checks"]
    assert checks["cui_marking_present"] is True
    assert checks["nav_link_count"] > 0
    assert checks["body_text_len"] > 0
