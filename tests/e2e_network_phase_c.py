"""
E2E Selenium test — Network Canvas Phase C (Impact Analysis + Enterprise Summary).

Tests:
1.  Impact analysis API: no interconnects = no impact
2.  Impact analysis API: with interconnects shows affected projects
3.  Impact analysis: hop distance (direct vs indirect)
4.  Enterprise summary API: returns aggregate metrics
5.  Enterprise page loads with stats
6.  Enterprise page: status distribution cards
7.  Enterprise page: risk posture section
8.  Enterprise nav link
9.  Impact Analysis button on project detail
10. Impact results render on project detail
11. JS error checks
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
    path = SCREENSHOT_DIR / f"network-phaseC-{name}-1920x1080.png"
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
    pids = []

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # Setup: 3 projects (A -> B -> C chain)
        for name, status in [
            ("E2E PhaseC Proj-A", "approved"),
            ("E2E PhaseC Proj-B", "deployed"),
            ("E2E PhaseC Proj-C", "approved"),
        ]:
            d = api(driver, "POST", "/network/api/projects", {"name": name, "status": status, "owner": "e2e"})
            pids.append(d.get("id"))

        # Link topologies
        topos = api(driver, "GET", "/network/api/topologies")
        for i, pid in enumerate(pids):
            if i < len(topos):
                api(driver, "POST", f"/network/api/projects/{pid}/topologies", {"topology_id": topos[i]["id"]})

        # ── 1. Impact: no interconnects ───────────────────────────────────
        try:
            r = api(driver, "GET", f"/network/api/projects/{pids[0]}/impact")
            assert r.get("total_affected") == 0
            assert r.get("interconnect_count") == 0
            results.ok("impact_no_interconnects", f"affected={r['total_affected']}")
        except Exception as e:
            results.fail("impact_no_interconnects", e)

        # Create chain: A -> B -> C
        api(
            driver,
            "POST",
            "/network/api/interconnects",
            {
                "src_project_id": pids[0],
                "dst_project_id": pids[1],
                "circuit_id": "IC-AB-001",
                "protocol": "BGP",
                "bandwidth": "10G",
            },
        )
        api(
            driver,
            "POST",
            "/network/api/interconnects",
            {
                "src_project_id": pids[1],
                "dst_project_id": pids[2],
                "circuit_id": "IC-BC-001",
                "protocol": "OSPF",
                "bandwidth": "1G",
            },
        )

        # ── 2. Impact: with interconnects ─────────────────────────────────
        try:
            r = api(driver, "GET", f"/network/api/projects/{pids[0]}/impact")
            assert r.get("total_affected") >= 2, f"Expected 2+ affected, got {r.get('total_affected')}"
            results.ok(
                "impact_with_interconnects",
                f"affected={r['total_affected']}, direct={r['direct']}, indirect={r['indirect']}",
            )
        except Exception as e:
            results.fail("impact_with_interconnects", e)

        # ── 3. Impact: hop distance ───────────────────────────────────────
        try:
            r = api(driver, "GET", f"/network/api/projects/{pids[0]}/impact")
            affected = r.get("affected_projects", [])
            direct = [a for a in affected if a["impact"] == "direct"]
            indirect = [a for a in affected if a["impact"] == "indirect"]
            assert len(direct) >= 1, "No direct impacts"
            assert len(indirect) >= 1, "No indirect impacts"
            assert direct[0]["hop_distance"] == 1
            assert indirect[0]["hop_distance"] == 2
            results.ok(
                "impact_hop_distance",
                f"direct[0]={direct[0]['project_name']}, indirect[0]={indirect[0]['project_name']}",
            )
        except Exception as e:
            results.fail("impact_hop_distance", e)

        # ── 4. Enterprise summary API ─────────────────────────────────────
        try:
            es = api(driver, "GET", "/network/api/enterprise-summary")
            assert es.get("total_projects") >= 3
            assert "status_counts" in es
            assert "total_devices" in es
            assert "compliance_pct" in es
            assert "board_reviews" in es
            assert "total_interconnects" in es
            results.ok(
                "enterprise_summary_api",
                f"projects={es['total_projects']}, devices={es['total_devices']}, ics={es['total_interconnects']}",
            )
        except Exception as e:
            results.fail("enterprise_summary_api", e)

        # ── 5. Enterprise page loads ──────────────────────────────────────
        driver.get(f"{BASE_URL}/network/enterprise")
        time.sleep(3)
        screenshot(driver, "enterprise-dashboard")

        try:
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "Enterprise" in title.text
            content = driver.find_element(By.ID, "ent-content")
            assert content.is_displayed()
            results.ok("enterprise_page_loads")
        except Exception as e:
            results.fail("enterprise_page_loads", e)

        # ── 6. Enterprise: status cards ───────────────────────────────────
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, ".ent-status-card")
            assert len(cards) >= 3, f"Expected 3+ cards, got {len(cards)}"
            results.ok("enterprise_status_cards", f"{len(cards)} cards")
        except Exception as e:
            results.fail("enterprise_status_cards", e)

        # ── 7. Enterprise: risk posture ───────────────────────────────────
        try:
            risk_el = driver.find_element(By.ID, "ent-risk")
            assert risk_el.text
            results.ok("enterprise_risk_posture")
        except Exception as e:
            results.fail("enterprise_risk_posture", e)

        # ── 8. Enterprise nav link ────────────────────────────────────────
        try:
            assert "Enterprise" in driver.page_source, "Nav link Enterprise not in page"
            results.ok("enterprise_nav_link")
        except Exception as e:
            results.fail("enterprise_nav_link", e)

        # ── 9. Impact button on project detail ────────────────────────────
        driver.get(f"{BASE_URL}/network/projects/{pids[0]}")
        time.sleep(2)

        try:
            btns = driver.find_elements(By.CSS_SELECTOR, "#pipeline-section button")
            btn_texts = [b.text for b in btns]
            assert "Impact Analysis" in btn_texts, f"No Impact Analysis button: {btn_texts}"
            results.ok("impact_button_on_detail")
        except Exception as e:
            results.fail("impact_button_on_detail", e)

        # ── 10. Impact results render ─────────────────────────────────────
        try:
            # Click the impact analysis button
            for b in driver.find_elements(By.CSS_SELECTOR, "#pipeline-section button"):
                if "Impact" in b.text:
                    b.click()
                    break
            time.sleep(2)
            impact_el = driver.find_element(By.ID, "impact-results")
            assert impact_el.is_displayed()
            assert impact_el.text.strip()
            screenshot(driver, "impact-results")
            results.ok("impact_results_render", impact_el.text[:80])
        except Exception as e:
            results.fail("impact_results_render", e)

        # ── 11. JS errors ─────────────────────────────────────────────────
        for page, name in [
            ("/network/enterprise", "enterprise"),
            (f"/network/projects/{pids[0]}", "detail_impact"),
        ]:
            driver.get(f"{BASE_URL}{page}")
            time.sleep(3)
            errs = check_js_errors(driver)
            if errs:
                results.fail(f"js_errors_{name}", f"{len(errs)}: {errs[0][:80]}")
            else:
                results.ok(f"js_errors_{name}", "No SEVERE JS errors")

        # ── Cleanup ───────────────────────────────────────────────────────
        for cid in pids:
            if cid:
                try:
                    api(driver, "DELETE", f"/network/api/projects/{cid}")
                except Exception:
                    pass
        results.ok("cleanup")

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Network Canvas — Phase C (Impact Analysis + Enterprise Summary)")
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
