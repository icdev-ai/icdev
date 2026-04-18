#!/usr/bin/env python3
# CUI // SP-CTI
"""E2E Selenium test — PDC Pipeline Twin /devops/twin/<pipe_id>.

Lifecycle:
  1. Login and seed pipeline + 2 snapshots via HTTP API
  2. Navigate to /devops/twin/<pipe_id>
  3. Verify pipeline list (snapshot cards) renders
  4. Run simulation via JS fetch with a delta graph (2 extra nodes/edges)
  5. Assert diff table shows ≥1 added/removed row
  6. Screenshot to playwright/screenshots/devops-twin.png
  7. Cleanup seeded pipeline via API
"""

import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")

SCREENSHOT_DIR = Path(__file__).resolve().parents[1] / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_BASELINE_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "cicd-gitlab", "label": "GitLab CI"},
        {"id": "n2", "type": "scan-semgrep", "label": "Semgrep SAST"},
    ],
    "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
}

_DELTA_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "cicd-gitlab", "label": "GitLab CI"},
        {"id": "n2", "type": "scan-semgrep", "label": "Semgrep SAST"},
        {"id": "n3", "type": "deploy-k8s", "label": "K8s Deploy"},
        {"id": "n4", "type": "scan-trivy", "label": "Trivy Scan"},
    ],
    "edges": [
        {"id": "e1", "source": "n1", "target": "n2"},
        {"id": "e2", "source": "n2", "target": "n3"},
        {"id": "e3", "source": "n3", "target": "n4"},
    ],
}

passed = 0
failed = 0
errors = []


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        msg = f"  FAIL: {label}" + (f" -- {detail}" if detail else "")
        print(msg)
        errors.append(msg)


def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


def login(driver):
    driver.get(f"{BASE_URL}/login")
    time.sleep(1)
    try:
        driver.find_element(By.NAME, "username").send_keys("admin")
        driver.find_element(By.NAME, "password").send_keys("admin")
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        time.sleep(1)
    except Exception:
        pass


def js_fetch(driver, method, path, body=None):
    """Run a fetch() inside the browser and return parsed JSON."""
    body_js = json.dumps(json.dumps(body)) if body is not None else "undefined"
    script = f"""
        const done = arguments[arguments.length - 1];
        const opts = {{
            method: '{method}',
            headers: {{'Content-Type': 'application/json'}}
        }};
        if ({body_js} !== undefined) opts.body = {body_js};
        fetch('{path}', opts)
            .then(r => r.json())
            .then(d => done(JSON.stringify(d)))
            .catch(e => done(JSON.stringify({{error: e.message}})));
    """
    raw = driver.execute_async_script(script)
    return json.loads(raw) if raw else {}


def main():
    global passed, failed

    print("\n=== PDC Twin E2E — /devops/twin ===\n")

    driver = create_driver()
    pipe_id = None

    try:
        # ── Login ──────────────────────────────────────────────────────────
        print("0. Login")
        login(driver)
        # Navigate to a known authenticated page to confirm session
        driver.get(f"{BASE_URL}/devops/")
        time.sleep(2)
        check("Login succeeded (not on /login)",
              "/login" not in driver.current_url,
              f"landed at {driver.current_url}")

        # ── 1. Seed pipeline via API ───────────────────────────────────────
        print("\n1. Seed Pipeline")
        pipe_data = js_fetch(driver, "POST", "/devops/api/pipelines", {
            "name": "E2E Twin Pipeline",
            "description": "Selenium E2E seed",
            "graph_json": json.dumps(_BASELINE_GRAPH),
        })
        pipe_id = pipe_data.get("id")
        check("Pipeline created via API", bool(pipe_id),
              f"response: {json.dumps(pipe_data)[:120]}")

        if not pipe_id:
            print("\nFATAL: pipeline creation failed")
            return 1

        # Seed snapshot 1 (baseline)
        snap1 = js_fetch(driver, "POST", f"/devops/api/pipelines/{pipe_id}/twin/snapshot",
                         {"label": "baseline-v1"})
        snap1_id = snap1.get("id")
        check("Snapshot 1 'baseline-v1' created", bool(snap1_id),
              f"response: {json.dumps(snap1)[:120]}")

        # Seed snapshot 2 (pre-delta)
        snap2 = js_fetch(driver, "POST", f"/devops/api/pipelines/{pipe_id}/twin/snapshot",
                         {"label": "pre-delta-v2"})
        check("Snapshot 2 'pre-delta-v2' created", bool(snap2.get("id")),
              f"response: {json.dumps(snap2)[:120]}")

        # ── 2. Navigate to twin page ───────────────────────────────────────
        print("\n2. Page Load")
        driver.get(f"{BASE_URL}/devops/twin/{pipe_id}")
        time.sleep(2)
        src = driver.page_source

        check("Not redirected to login", "/login" not in driver.current_url,
              f"landed at {driver.current_url}")
        check("Pipeline Twin heading present", "Pipeline Twin" in src)
        check("Pipeline name present", "E2E Twin Pipeline" in src)
        check("Baseline Snapshot panel present", "Baseline Snapshot" in src)

        # ── 3. Snapshot list renders ───────────────────────────────────────
        print("\n3. Snapshot List")
        snap_items = driver.find_elements(By.CLASS_NAME, "snap-item")
        check("Snapshot list renders ≥2 items", len(snap_items) >= 2,
              f"found {len(snap_items)}")
        check("Snapshot label 'baseline-v1' in source", "baseline-v1" in src)
        check("Snapshot label 'pre-delta-v2' in source", "pre-delta-v2" in src)

        driver.save_screenshot(str(SCREENSHOT_DIR / "devops-twin-loaded.png"))

        # ── 4. Run simulation via API ──────────────────────────────────────
        print("\n4. Run Simulation (API)")
        sim = js_fetch(driver, "POST",
                       f"/devops/api/pipelines/{pipe_id}/twin/simulate",
                       {"delta_graph": _DELTA_GRAPH, "baseline_snap_id": snap1_id})
        check("Simulation returned id", "id" in sim, f"got: {json.dumps(sim)[:120]}")
        check("Verdict present (pass/warn/fail)",
              sim.get("verdict") in ("pass", "warn", "fail"),
              f"got: {sim.get('verdict')}")

        diff = sim.get("diff", {})
        added_nodes = diff.get("added_nodes", [])
        removed_nodes = diff.get("removed_nodes", [])
        added_edges = diff.get("added_edges", [])
        check("Diff has ≥1 added node", len(added_nodes) >= 1,
              f"added_nodes={len(added_nodes)}")
        check("Diff row count ≥1 (added+removed nodes)",
              len(added_nodes) + len(removed_nodes) >= 1,
              f"added={len(added_nodes)} removed={len(removed_nodes)}")
        check("Added edges ≥1", len(added_edges) >= 1,
              f"added_edges={len(added_edges)}")

        # ── 5. Render results in UI ────────────────────────────────────────
        print("\n5. UI Diff Rendering")
        textarea = driver.find_element(By.ID, "deltaInput")
        driver.execute_script("arguments[0].value = arguments[1]", textarea,
                              json.dumps(_DELTA_GRAPH))

        # Select first snapshot
        driver.execute_script(
            "const s = document.querySelector('.snap-item'); if(s) s.click();"
        )
        time.sleep(0.3)

        # Dismiss any tour overlay that may intercept the click
        driver.execute_script("""
            const tour = document.getElementById('icdev-tour-welcome');
            if (tour) tour.style.display = 'none';
            document.querySelectorAll('[class*="tour"]').forEach(el => {
                el.style.display = 'none';
            });
        """)
        time.sleep(0.3)
        btn = driver.find_element(By.XPATH,
                                  "//button[normalize-space(text())='Run Simulation']")
        driver.execute_script("arguments[0].click()", btn)
        time.sleep(4)

        results_panel = driver.find_element(By.ID, "simResults")
        check("Results panel visible after run", results_panel.is_displayed())

        diff_section = driver.find_element(By.ID, "diffSection")
        check("Diff section visible", diff_section.is_displayed())

        diff_body_html = driver.find_element(By.ID, "diffBody").get_attribute("innerHTML")
        check("Diff body has content", len(diff_body_html.strip()) > 20,
              f"innerHTML len={len(diff_body_html)}")

        diff_add = driver.find_elements(By.CLASS_NAME, "diff-add")
        diff_rem = driver.find_elements(By.CLASS_NAME, "diff-rem")
        check("Diff add rows present", len(diff_add) >= 1, f"found {len(diff_add)}")
        check("Diff remove rows present", len(diff_rem) >= 1, f"found {len(diff_rem)}")

        verdict_el = driver.find_element(By.ID, "verdictBanner")
        check("Verdict banner visible", verdict_el.is_displayed())

        # ── 6. Screenshot ──────────────────────────────────────────────────
        print("\n6. Screenshot")
        driver.execute_script("window.scrollTo(0, 0)")
        time.sleep(0.3)
        shot = SCREENSHOT_DIR / "devops-twin.png"
        driver.save_screenshot(str(shot))
        check("Screenshot saved at devops-twin.png", shot.exists(), str(shot))
        print(f"    Screenshot: {shot}")

        # ── 7. JS error check ──────────────────────────────────────────────
        print("\n7. JS Error Check")
        logs = driver.get_log("browser")
        twin_errors = [
            lg for lg in logs
            if lg["level"] == "SEVERE"
            and "favicon" not in lg.get("message", "").lower()
            and (
                "twin" in lg.get("message", "").lower()
                or "/devops/" in lg.get("message", "")
            )
        ]
        check("No twin-specific SEVERE JS errors", len(twin_errors) == 0,
              f"{[e['message'][:80] for e in twin_errors[:2]]}")

    except Exception as exc:
        failed += 1
        errors.append(f"EXCEPTION: {exc}")
        print(f"\n  EXCEPTION: {exc}")
        import traceback
        traceback.print_exc()
        try:
            driver.save_screenshot(str(SCREENSHOT_DIR / "devops-twin-error.png"))
        except Exception:
            pass
    finally:
        # Cleanup: delete seeded pipeline via DB (if created)
        if pipe_id:
            try:
                from tools.pipeline.db.init_db import get_connection
                conn = get_connection()
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute("DELETE FROM pdc_simulations WHERE pipeline_id=?", (pipe_id,))
                conn.execute("DELETE FROM pdc_snapshots WHERE pipeline_id=?", (pipe_id,))
                conn.execute("DELETE FROM pipelines WHERE id=?", (pipe_id,))
                conn.commit()
                conn.close()
                print(f"\n  Cleaned up pipeline {pipe_id}")
            except Exception as exc:
                print(f"\n  Cleanup warning: {exc}")
        driver.quit()

    # ── Summary ────────────────────────────────────────────────────────────
    total = passed + failed
    print("\n" + "=" * 60)
    print(f"DEVOPS TWIN E2E: {passed}/{total} passed, {failed} failed")
    print("=" * 60)
    if errors:
        print("\nFailed:")
        for e in errors:
            print(f"  {e}")
    else:
        print("\nAll checks passed!")
    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
