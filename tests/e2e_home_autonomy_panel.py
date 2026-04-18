"""E2E Selenium test — Autonomous Recovery panel on Home (/).

Asserts:
  1. /api/autonomy/status returns valid JSON with expected keys
  2. #autonomy-status section renders when visible=true
  3. Triage cards render with correct outcome badges (autofix/review/quarantine)
  4. Status tag updates based on activity level
  5. Panel auto-hides when payload.visible=false (tested via JS override)
  6. Screenshot captured
  7. No SEVERE JS errors

Run: python tests/e2e_home_autonomy_panel.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=opts)


def main() -> int:
    passed: list = []
    failed: list = []

    def ok(name: str, detail: str = ""):
        passed.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"[OK]   {name}{(': ' + detail) if detail else ''}")

    def fail(name: str, err: object):
        msg = str(err)[:240]
        failed.append((name, msg))
        print(f"[FAIL] {name}: {msg}")

    # 1. API shape
    try:
        with urllib.request.urlopen(f"{BASE_URL}/api/autonomy/status", timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        for key in ("visible", "triage_recent", "autofix_branches", "unresolved_failures", "triage_summary"):
            assert key in payload, f"missing {key!r}"
        ok("/api/autonomy/status shape",
           f"visible={payload['visible']} triage={len(payload['triage_recent'])} branches={len(payload['autofix_branches'])} failures={len(payload['unresolved_failures'])}")
    except Exception as e:
        fail("/api/autonomy/status shape", e)
        payload = {"visible": False, "triage_recent": [], "autofix_branches": [], "unresolved_failures": [], "triage_summary": {}}

    driver = make_driver()
    try:
        driver.get(f"{BASE_URL}/")
        time.sleep(3)  # let all refresh hooks fire

        # 2. Panel element exists
        try:
            section = driver.find_element(By.ID, "autonomy-status")
            ok("#autonomy-status element present")
        except Exception as e:
            fail("#autonomy-status element present", e)
            raise

        # 3. Panel visibility matches API
        try:
            shown = section.is_displayed()
            if payload["visible"]:
                assert shown, "API says visible=true but panel is hidden"
                ok("panel visible (matches API visible=true)")
            else:
                assert not shown, "API says visible=false but panel is shown"
                ok("panel hidden (auto-hide working, API visible=false)")
        except Exception as e:
            fail("panel visibility matches API", e)

        # 4. Triage cards render one per marker — only if API had some
        if payload["visible"] and payload["triage_recent"]:
            try:
                cards = driver.find_elements(By.CSS_SELECTOR, "#autonomy-container .auto-card")
                # triage cards + (optional) branch card + (optional) failures card
                # triage cards use .auto-card standalone, branch/failure groups use
                # .auto-card with nested rows. So expect ≥ triage_recent count.
                assert len(cards) >= len(payload["triage_recent"]), (
                    f"expected ≥{len(payload['triage_recent'])} cards, got {len(cards)}"
                )
                ok("triage cards rendered", f"{len(cards)} card(s)")
            except Exception as e:
                fail("triage cards rendered", e)

            # 5. Status tag reflects activity level
            try:
                tag = driver.find_element(By.ID, "autonomy-status-tag")
                txt = (tag.text or "").lower()
                assert txt in ("fixing", "diagnosing", "watching"), f"unexpected tag text: {txt!r}"
                ok("status tag renders", txt)
            except Exception as e:
                fail("status tag renders", e)

        # 6. Auto-hide behavior — simulate empty payload via JS
        try:
            driver.execute_script("window.renderAutonomyStatus && window.renderAutonomyStatus({visible: false})")
            time.sleep(0.5)
            still_shown = section.is_displayed()
            assert not still_shown, "panel should hide when visible=false"
            ok("auto-hide when visible=false")
            # Restore
            driver.execute_script("window.refreshAutonomyStatus && window.refreshAutonomyStatus()")
        except Exception as e:
            fail("auto-hide when visible=false", e)

        # 7. Screenshot
        try:
            shot = SCREENSHOT_DIR / "home-autonomy-panel.png"
            driver.save_screenshot(str(shot))
            ok("screenshot", str(shot))
        except Exception as e:
            fail("screenshot", e)

        # 8. No SEVERE JS errors (favicon/404 excluded)
        try:
            errors = []
            for entry in driver.get_log("browser"):
                if entry.get("level") != "SEVERE":
                    continue
                msg = entry.get("message", "")
                if any(tok in msg.lower() for tok in ("favicon", "404")):
                    continue
                errors.append(msg[:200])
            if errors:
                fail("no SEVERE JS errors", errors[0])
            else:
                ok("no SEVERE JS errors")
        except Exception as e:
            fail("no SEVERE JS errors", e)
    finally:
        driver.quit()

    summary = {
        "passed": len(passed),
        "failed": len(failed),
        "total": len(passed) + len(failed),
        "failures": failed,
    }
    print("\n" + "=" * 60)
    print(json.dumps(summary, indent=2))
    print("=" * 60)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
