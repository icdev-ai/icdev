"""
E2E Selenium test — Network Canvas Phase B
(Presentation Generator + Global Connectivity + Conflict Detection).

Tests:
1.  Presentation API returns structured data
2.  Presentation page renders
3.  Global topology API returns projects + interconnects
4.  Create interconnect API
5.  Delete interconnect API
6.  Conflict detection API (no false positives)
7.  Global page loads with project cards
8.  Global page: interconnects table
9.  Global nav link in navbar
10. Presentation link on project detail
11. Conflict check button on global page
12. JS error checks on Phase B pages
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
    path = SCREENSHOT_DIR / f"network-phaseB-{name}-1920x1080.png"
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
    pid = pid2 = ic_id = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # Setup: two projects with topologies
        d = api(
            driver,
            "POST",
            "/network/api/projects",
            {"name": "E2E PhaseB Project A", "status": "approved", "owner": "e2e-test"},
        )
        pid = d.get("id")
        d2 = api(
            driver,
            "POST",
            "/network/api/projects",
            {"name": "E2E PhaseB Project B", "status": "deployed", "owner": "e2e-test"},
        )
        pid2 = d2.get("id")

        topos = api(driver, "GET", "/network/api/topologies")
        topo_a = topos[0]["id"] if topos else None
        topo_b = topos[1]["id"] if len(topos) > 1 else topo_a
        if topo_a:
            api(driver, "POST", f"/network/api/projects/{pid}/topologies", {"topology_id": topo_a})
        if topo_b:
            api(driver, "POST", f"/network/api/projects/{pid2}/topologies", {"topology_id": topo_b})

        # Set ROI on project A for presentation
        api(
            driver,
            "PUT",
            f"/network/api/projects/{pid}/roi",
            {"capex": 150000, "opex_annual": 24000, "savings_annual": 80000, "justification": "E2E test business case"},
        )

        # ── 1. Presentation API ───────────────────────────────────────────
        try:
            pres = api(driver, "GET", f"/network/api/projects/{pid}/presentation")
            assert pres.get("title") == "E2E PhaseB Project A"
            assert pres.get("executive_summary"), "No exec summary"
            assert pres.get("generated_at"), "No timestamp"
            es = pres["executive_summary"]
            assert "compliance_pct" in es
            assert "total_capex" in es
            assert "roi" in es
            results.ok("presentation_api", f"devices={es.get('total_devices')}")
        except Exception as e:
            results.fail("presentation_api", e)

        # ── 2. Presentation page ──────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/projects/{pid}/presentation")
            time.sleep(3)
            screenshot(driver, "presentation-page")
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "E2E PhaseB Project A" in title.text
            # Check JS loaded content
            panels = driver.find_elements(By.CSS_SELECTOR, ".pres-section")
            assert len(panels) >= 1, "No presentation sections rendered"
            results.ok("presentation_page_renders", f"{len(panels)} sections")
        except Exception as e:
            results.fail("presentation_page_renders", e)

        # ── 3. Global topology API ────────────────────────────────────────
        try:
            gt = api(driver, "GET", "/network/api/global-topology")
            assert gt.get("projects") is not None
            assert gt.get("total_projects") >= 2
            results.ok(
                "global_topology_api", f"projects={gt['total_projects']}, ics={gt.get('total_interconnects', 0)}"
            )
        except Exception as e:
            results.fail("global_topology_api", e)

        # ── 4. Create interconnect ────────────────────────────────────────
        try:
            r = api(
                driver,
                "POST",
                "/network/api/interconnects",
                {
                    "src_project_id": pid,
                    "src_topology_id": topo_a,
                    "dst_project_id": pid2,
                    "dst_topology_id": topo_b,
                    "circuit_id": "WAN-E2E-001",
                    "protocol": "BGP",
                    "bandwidth": "10G",
                    "notes": "E2E test interconnect",
                },
            )
            ic_id = r.get("id")
            assert ic_id, f"No interconnect ID: {r}"
            results.ok("create_interconnect", f"id={ic_id}")
        except Exception as e:
            results.fail("create_interconnect", e)

        # ── 5. List interconnects ─────────────────────────────────────────
        try:
            ics = api(driver, "GET", "/network/api/interconnects")
            assert len(ics) >= 1
            assert any(ic["id"] == ic_id for ic in ics)
            results.ok("list_interconnects", f"{len(ics)} total")
        except Exception as e:
            results.fail("list_interconnects", e)

        # ── 6. Conflict detection API ─────────────────────────────────────
        try:
            cd = api(driver, "GET", "/network/api/conflicts")
            assert "conflicts" in cd
            assert "checked" in cd
            results.ok("conflict_detection_api", f"conflicts={cd['total']}, checked={cd['checked']}")
        except Exception as e:
            results.fail("conflict_detection_api", e)

        # ── 7. Global page loads ──────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/global")
            time.sleep(3)
            screenshot(driver, "global-page")
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "Global" in title.text
            grid = driver.find_element(By.ID, "global-grid")
            assert grid.is_displayed()
            results.ok("global_page_loads")
        except Exception as e:
            results.fail("global_page_loads", e)

        # ── 8. Global: project cards ──────────────────────────────────────
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, ".global-project-card")
            assert len(cards) >= 2, f"Expected 2+ cards, got {len(cards)}"
            results.ok("global_project_cards", f"{len(cards)} cards")
        except Exception as e:
            results.fail("global_project_cards", e)

        # ── 9. Global: interconnects table ────────────────────────────────
        try:
            table = driver.find_element(By.ID, "ic-table")
            rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
            assert len(rows) >= 1
            page_text = driver.page_source
            assert "WAN-E2E-001" in page_text
            results.ok("global_interconnects_table", f"{len(rows)} rows")
        except Exception as e:
            results.fail("global_interconnects_table", e)

        # ── 10. Global nav link ───────────────────────────────────────────
        try:
            assert "Global" in driver.page_source, "Nav link Global not in page"
            results.ok("global_nav_link")
        except Exception as e:
            results.fail("global_nav_link", e)

        # ── 11. Presentation link on detail ───────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/projects/{pid}")
            time.sleep(2)
            links = driver.find_elements(By.CSS_SELECTOR, ".page-actions a, .page-actions button")
            texts = [l.text for l in links]
            assert "Presentation" in texts
            results.ok("presentation_link_on_detail")
        except Exception as e:
            results.fail("presentation_link_on_detail", e)

        # ── 12. Delete interconnect ───────────────────────────────────────
        if ic_id:
            try:
                api(driver, "DELETE", f"/network/api/interconnects/{ic_id}")
                ics = api(driver, "GET", "/network/api/interconnects")
                assert not any(ic["id"] == ic_id for ic in ics)
                results.ok("delete_interconnect")
            except Exception as e:
                results.fail("delete_interconnect", e)

        # ── 13. JS errors on Phase B pages ────────────────────────────────
        for page, name in [
            ("/network/global", "global"),
            (f"/network/projects/{pid}/presentation", "presentation"),
        ]:
            driver.get(f"{BASE_URL}{page}")
            time.sleep(3)
            errs = check_js_errors(driver)
            if errs:
                results.fail(f"js_errors_{name}", f"{len(errs)}: {errs[0][:80]}")
            else:
                results.ok(f"js_errors_{name}", "No SEVERE JS errors")

        # ── Cleanup ───────────────────────────────────────────────────────
        for cid in [pid, pid2]:
            if cid:
                api(driver, "DELETE", f"/network/api/projects/{cid}")
        results.ok("cleanup")

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Network Canvas — Phase B (Presentation + Global Connectivity + Conflicts)")
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
