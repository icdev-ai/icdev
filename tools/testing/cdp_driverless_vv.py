#!/usr/bin/env python3
# CUI // SP-CTI
"""Driverless CDP V&V — reproduce an e2e script's assertions with no driver binary.

cdp-vv-01, the first proof of the whole card (spike cdp-00 §9.1): on a machine with
``vendor/drivers/`` empty and driving the browser over CDP, reproduce the assertions
a hand-written ``tests/e2e_selenium/`` script makes against the live dashboard —
page loads, the CUI marking is present, navigation links exist, the body renders —
using the WebDriver-compatible facade, with **no WebDriver binary present**.

This is a live/integration check: it needs a Chromium-family browser installed and
a running dashboard. It is the tool behind the pytest in
``tests/browser/cdp/test_vv_driverless.py`` (which skips when either is absent), and
it is runnable standalone for a one-shot proof:

    python tools/testing/cdp_driverless_vv.py --json

It deliberately drives through ``CDPWebDriver`` with the same ``By``/``find_element``
calls the estate uses, so a pass here is evidence the estate itself runs driverless.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def run_vv(base_url: str = "http://localhost:5050", *, headless: bool = True) -> Dict[str, Any]:
    """Reproduce the core dashboard-home e2e assertions over CDP. Returns a report."""
    from selenium.webdriver.common.by import By

    from tools.browser.cdp.webdriver import CDPWebDriver

    checks: Dict[str, Any] = {}
    # Proof of driverlessness: no vendored driver binary is used. Record what is
    # present so the report is honest either way.
    vendor = REPO_ROOT / "vendor" / "drivers"
    checks["vendored_drivers_present"] = any(
        (vendor / sub).exists() and any((vendor / sub).iterdir())
        for sub in ("chromedriver", "msedgedriver")
        if (vendor / sub).exists()
    )

    driver = CDPWebDriver.create(headless=headless)
    try:
        driver.get(base_url)
        checks["current_url"] = driver.current_url
        checks["title"] = driver.title
        body = driver.find_element(By.TAG_NAME, "body")
        body_text = body.text
        checks["body_text_len"] = len(body_text)
        checks["cui_marking_present"] = ("CUI" in body_text.upper()) or ("CONTROLLED" in body_text.upper())
        checks["nav_link_count"] = len(driver.find_elements(By.TAG_NAME, "a"))
        checks["page_source_len"] = len(driver.page_source)

        shots = REPO_ROOT / "playwright" / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        checks["screenshot_saved"] = driver.save_screenshot(str(shots / "cdp_vv01_home.png"))
    finally:
        driver.quit()

    # The assertions that must hold for a PASS (the e2e-script equivalents).
    passed = (
        checks.get("body_text_len", 0) > 0
        and checks.get("cui_marking_present") is True
        and checks.get("nav_link_count", 0) > 0
        and checks.get("current_url", "").rstrip("/") == base_url.rstrip("/")
    )
    return {
        "ok": passed,
        "transport": "cdp",
        "driver_binary_used": False,
        "base_url": base_url,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Driverless CDP V&V (cdp-vv-01)")
    parser.add_argument("--url", default=os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050"))
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args()

    try:
        report = run_vv(ns.url, headless=not ns.no_headless)
    except Exception as exc:  # noqa: BLE001 - report the failure as data
        report = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "base_url": ns.url}

    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
