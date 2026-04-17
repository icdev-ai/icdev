"""E2E Selenium test — Home (/) with Projects in Flight partial.

Asserts:
  1. Home page loads + renders the Task Board
  2. Projects in Flight section renders below the Task Board
  3. Digital Twin project card is visible (until 100% done)
  4. project card contains expected epics rendered as progress bars
  5. brief links present
  6. /api/projects/progress returns valid JSON
  7. legacy /digital-twin redirects
  8. screenshot captured
  9. no SEVERE JS errors (favicon/404 excluded)

Run: python tests/e2e_home_projects_in_flight.py
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

    # -- API smoke (before browser) -------------------------------------
    try:
        with urllib.request.urlopen(f"{BASE_URL}/api/projects/progress", timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert "projects" in payload, "missing 'projects' key"
        assert "triage" in payload, "missing 'triage' key"
        ok("/api/projects/progress responds",
           f"{len(payload.get('projects', []))} project(s)")
    except Exception as e:
        fail("/api/projects/progress responds", e)
        payload = {"projects": [], "triage": {}}

    # -- /digital-twin legacy redirect ----------------------------------
    try:
        req = urllib.request.Request(f"{BASE_URL}/digital-twin", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            # Followed redirect → should land on /kanban and show Task Board page
            final = resp.url
        assert "/kanban" in final, f"expected /kanban in redirect target, got {final}"
        ok("/digital-twin legacy redirect", final)
    except Exception as e:
        fail("/digital-twin legacy redirect", e)

    # -- Browser test ---------------------------------------------------
    driver = make_driver()
    try:
        driver.get(f"{BASE_URL}/")
        time.sleep(2.5)  # give the projects fetch time to complete

        body = driver.page_source

        # 1. Task Board shell
        try:
            assert "Task Board" in body, "missing 'Task Board' header"
            ok("Task Board header rendered")
        except Exception as e:
            fail("Task Board header rendered", e)

        # 2. Projects in Flight section
        try:
            section = driver.find_element(By.ID, "projects-in-flight")
            assert section is not None
            ok("Projects in Flight section present")
        except Exception as e:
            fail("Projects in Flight section present", e)

        # 3. Skip project-specific assertions if no in-flight projects —
        # that's the intended "auto-hide when done" behavior.
        visible_projects = payload.get("projects", [])
        if not visible_projects:
            ok("no in-flight projects (section auto-hides)")
        else:
            # Digital Twin should be the first registered project while work is ongoing
            try:
                card = driver.find_element(By.ID, "project-digital-twin")
                assert card is not None
                ok("Digital Twin project card rendered")
            except Exception as e:
                fail("Digital Twin project card rendered", e)

            # Project card has epics
            try:
                epics = driver.find_elements(By.CSS_SELECTOR,
                    "#project-digital-twin .proj-epic")
                assert len(epics) >= 1, f"expected ≥1 epic bar, got {len(epics)}"
                ok("epics rendered", f"{len(epics)} bar(s)")
            except Exception as e:
                fail("epics rendered", e)

            # Brief links present
            try:
                briefs = driver.find_elements(By.CSS_SELECTOR,
                    "#project-digital-twin .proj-briefs a")
                assert len(briefs) >= 2, f"expected ≥2 brief links, got {len(briefs)}"
                ok("brief links", f"{len(briefs)} link(s)")
            except Exception as e:
                fail("brief links", e)

        # 4. Screenshot
        try:
            shot = SCREENSHOT_DIR / "home-with-projects.png"
            driver.save_screenshot(str(shot))
            ok("screenshot", str(shot))
        except Exception as e:
            fail("screenshot", e)

        # 5. No SEVERE JS errors (favicon/404 excluded)
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
