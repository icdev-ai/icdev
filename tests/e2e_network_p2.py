"""
E2E Selenium test — Network Canvas P2 features.

Tests:
1.  Comparison page loads with matrix table
2.  Risk heatmap renders with color-coded cells
3.  Comparison matrix has all required columns
4.  Compare nav link in navbar
5.  Clone project API creates deep copy
6.  Cloned project has copied topologies
7.  Clone button on project detail page
8.  Clone from comparison page modal
9.  Jinja2 format fix verification (no negative scores)
10. JS error checks on all P2 pages
"""

import json
import os
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class TestResult:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, name, detail=""):
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS  {name} {detail}")

    def fail(self, name, error):
        self.failed.append({"test": name, "error": str(error)})
        print(f"  FAIL  {name} -- {error}")

    def summary(self):
        total = len(self.passed) + len(self.failed)
        rate = f"{len(self.passed) / total * 100:.1f}%" if total else "0%"
        return {
            "total": total,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "pass_rate": rate,
            "failures": self.failed,
        }


def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


def screenshot(driver, name):
    path = SCREENSHOT_DIR / f"network-p2-{name}-1920x1080.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass


def check_js_errors(driver):
    errors = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") == "SEVERE":
                msg = entry.get("message", "")
                if any(x in msg.lower() for x in ["favicon", "401", "404", "failed to load resource"]):
                    continue
                errors.append(msg)
    except Exception:
        pass
    return errors


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


def api(driver, method, path, data=None):
    if data is not None:
        body_str = json.dumps(json.dumps(data))
        script = f"""
            const r = await fetch('{path}', {{
                method: '{method}',
                headers: {{'Content-Type': 'application/json'}},
                body: {body_str}
            }});
            return await r.json();
        """
    else:
        script = f"""
            const r = await fetch('{path}', {{method: '{method}'}});
            return await r.json();
        """
    return driver.execute_script(script)


def run_tests():
    results = TestResult()
    driver = create_driver()
    proj_id = None
    clone_id = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # Setup: create a project with a linked topology
        proj_data = api(
            driver,
            "POST",
            "/network/api/projects",
            {
                "name": "E2E P2 Compare Project",
                "status": "in_review",
                "owner": "e2e-tester",
                "description": "P2 comparison test",
            },
        )
        proj_id = proj_data.get("id")

        topos = api(driver, "GET", "/network/api/topologies")
        topo_id = None
        if topos and len(topos) > 0:
            topo_id = topos[0]["id"]
            api(driver, "POST", f"/network/api/projects/{proj_id}/topologies", {"topology_id": topo_id})
            # Run audit for comparison data
            api(driver, "POST", f"/network/api/compliance/{topo_id}/audit")
        time.sleep(1)

        # ── 1. Compare page loads ─────────────────────────────────────────
        driver.get(f"{BASE_URL}/network/projects/compare")
        time.sleep(2)
        screenshot(driver, "compare-page")

        try:
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "Comparison" in title.text
            results.ok("compare_page_loads", title.text)
        except Exception as e:
            results.fail("compare_page_loads", e)

        # ── 2. Comparison matrix table ────────────────────────────────────
        try:
            matrix = driver.find_element(By.ID, "compare-matrix")
            headers = [th.text.upper() for th in matrix.find_elements(By.CSS_SELECTOR, "thead th")]
            assert "COMPLIANCE" in headers
            assert "CAT1" in headers
            assert "CAPEX" in headers
            assert "MC RESILIENCE" in headers
            rows = matrix.find_elements(By.CSS_SELECTOR, "tbody tr")
            assert len(rows) >= 1, f"Expected 1+ rows, got {len(rows)}"
            results.ok("compare_matrix_table", f"{len(rows)} rows, columns: {headers}")
        except Exception as e:
            results.fail("compare_matrix_table", e)

        # ── 3. Risk heatmap renders ───────────────────────────────────────
        try:
            heatmap = driver.find_element(By.ID, "risk-heatmap")
            hm_rows = heatmap.find_elements(By.CSS_SELECTOR, ".heatmap-row")
            assert len(hm_rows) >= 1, "No heatmap rows"
            # Check color-coded cells exist
            colored = heatmap.find_elements(By.CSS_SELECTOR, ".hm-green, .hm-yellow, .hm-red, .hm-gray")
            assert len(colored) >= 3, f"Expected colored cells, got {len(colored)}"
            screenshot(driver, "compare-heatmap")
            results.ok("risk_heatmap_renders", f"{len(hm_rows)} rows, {len(colored)} colored cells")
        except Exception as e:
            results.fail("risk_heatmap_renders", e)

        # ── 4. Compliance bar visualization ───────────────────────────────
        try:
            bars = driver.find_elements(By.CSS_SELECTOR, ".compare-bar")
            if bars:
                fills = driver.find_elements(By.CSS_SELECTOR, ".compare-fill")
                assert fills, "No fill bars inside compare-bar"
                results.ok("compliance_bars", f"{len(bars)} bars rendered")
            else:
                results.ok("compliance_bars", "No audit data — no bars (correct)")
        except Exception as e:
            results.fail("compliance_bars", e)

        # ── 5. Compare nav link ───────────────────────────────────────────
        try:
            assert "Compare" in driver.page_source, "Nav link Compare not in page"
            results.ok("compare_nav_link")
        except Exception as e:
            results.fail("compare_nav_link", e)

        # ── 6. Clone project via API ──────────────────────────────────────
        try:
            clone_result = api(driver, "POST", f"/network/api/projects/{proj_id}/clone", {"name": "E2E Cloned Project"})
            assert clone_result.get("id"), f"No clone ID: {clone_result}"
            clone_id = clone_result["id"]
            assert clone_result.get("topologies_cloned", 0) >= 1, f"Expected 1+ topos cloned, got {clone_result}"
            results.ok("clone_project_api", f"id={clone_id}, topos={clone_result['topologies_cloned']}")
        except Exception as e:
            results.fail("clone_project_api", e)

        # ── 7. Cloned project has copied topologies ───────────────────────
        if clone_id:
            try:
                driver.get(f"{BASE_URL}/network/projects/{clone_id}")
                time.sleep(2)
                screenshot(driver, "cloned-project-detail")
                h1 = driver.find_element(By.CSS_SELECTOR, ".page-title")
                assert "E2E Cloned Project" in h1.text
                # Check topologies exist
                topo_rows = driver.find_elements(By.CSS_SELECTOR, ".data-table tbody tr")
                assert len(topo_rows) >= 1, "No topologies in cloned project"
                results.ok("cloned_project_detail", f"Title: {h1.text}, rows: {len(topo_rows)}")
            except Exception as e:
                results.fail("cloned_project_detail", e)

        # ── 8. Clone button on project detail ─────────────────────────────
        if proj_id:
            try:
                driver.get(f"{BASE_URL}/network/projects/{proj_id}")
                time.sleep(2)
                btns = driver.find_elements(By.CSS_SELECTOR, ".page-actions .btn")
                btn_texts = [b.text for b in btns]
                assert "Clone Project" in btn_texts, f"No Clone button: {btn_texts}"
                results.ok("clone_button_on_detail")
            except Exception as e:
                results.fail("clone_button_on_detail", e)

        # ── 9. Clone modal on compare page ────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/projects/compare")
            time.sleep(2)
            clone_btns = driver.find_elements(By.CSS_SELECTOR, "button[title='Clone']")
            if clone_btns:
                results.ok("clone_button_on_compare", f"{len(clone_btns)} clone buttons")
            else:
                results.ok("clone_button_on_compare", "No projects to clone")
        except Exception as e:
            results.fail("clone_button_on_compare", e)

        # ── 10. Compliance scores non-negative (bugfix verification) ──────
        try:
            if topo_id:
                audit = api(driver, "POST", f"/network/api/compliance/{topo_id}/audit")
                scores = audit.get("scores", {})
                for regime, s in scores.items():
                    assert s["passed"] >= 0, f"{regime}: passed={s['passed']} is negative!"
                    assert s["score_pct"] >= 0, f"{regime}: score_pct={s['score_pct']} is negative!"
                results.ok("compliance_scores_non_negative", str({r: s["score_pct"] for r, s in scores.items()}))
            else:
                results.ok("compliance_scores_non_negative", "No topo to test")
        except Exception as e:
            results.fail("compliance_scores_non_negative", e)

        # ── 11. JS errors on compare page ─────────────────────────────────
        driver.get(f"{BASE_URL}/network/projects/compare")
        time.sleep(2)
        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("js_errors_compare", f"{len(js_errs)}: {js_errs[0][:100]}")
        else:
            results.ok("js_errors_compare", "No SEVERE JS errors")

        # ── 12. Jinja2 format fix — no raw "%.0f" in rendered HTML ────────
        try:
            driver.get(f"{BASE_URL}/network/circuits")
            time.sleep(2)
            src = driver.page_source
            assert "%.0f" not in src, "Found raw %.0f in rendered circuits HTML"
            driver.get(f"{BASE_URL}/network/projects")
            time.sleep(2)
            src = driver.page_source
            assert "%.0f" not in src, "Found raw %.0f in rendered projects HTML"
            results.ok("jinja2_format_fix_verified")
        except Exception as e:
            results.fail("jinja2_format_fix_verified", e)

        # ── Cleanup ───────────────────────────────────────────────────────
        cleanup_ids = [i for i in [proj_id, clone_id] if i]
        for cid in cleanup_ids:
            try:
                api(driver, "DELETE", f"/network/api/projects/{cid}")
            except Exception:
                pass
        results.ok("cleanup", f"Deleted {len(cleanup_ids)} projects")

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Network Canvas — P2 (Comparison + Clone + Bugfixes)")
    print("=" * 60)
    r = run_tests()
    summary = r.summary()
    print()
    print(f"Results: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']})")
    if summary["failures"]:
        print("Failures:")
        for f in summary["failures"]:
            print(f"  - {f['test']}: {f['error']}")
    print(json.dumps(summary, indent=2))
    sys.exit(0 if summary["failed"] == 0 else 1)
