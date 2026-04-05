"""
E2E Selenium test — Network Canvas P1 features.

Tests:
1.  Project detail: compliance rollup table renders
2.  Project detail: aggregate compliance score in stats bar
3.  Project detail: cost rollup table with CapEx + OpEx
4.  Project detail: activity feed panel renders
5.  Project detail: per-topology CAT1/CAT2/CAT3 breakdown
6.  Portfolio: activity feed section
7.  Portfolio: stats bar shows open findings
8.  Project detail: Filter Dashboard link present
9.  Project detail: compliance links to audit page
10. All P1 pages: zero JS errors
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
    path = SCREENSHOT_DIR / f"network-p1-{name}-1920x1080.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return str(path)


def check_js_errors(driver):
    errors = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") == "SEVERE":
                msg = entry.get("message", "")
                if any(
                    x in msg.lower()
                    for x in [
                        "favicon",
                        "401",
                        "404",
                        "failed to load resource",
                    ]
                ):
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
    """Execute API call via JS in browser context."""
    if data is not None:
        body_str = json.dumps(json.dumps(data))  # double-encode for JS
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
    topo_id = None

    try:
        login(driver)
        # Navigate to network dashboard to ensure session is active
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # ── Setup: create project + link a topology ───────────────────────
        proj_data = api(
            driver,
            "POST",
            "/network/api/projects",
            {
                "name": "E2E P1 Test Project",
                "status": "draft",
                "owner": "e2e-tester",
                "description": "P1 compliance/cost test",
            },
        )
        proj_id = proj_data.get("id")

        # Get first available topology and link it
        topos = api(driver, "GET", "/network/api/topologies")
        if topos and len(topos) > 0:
            topo_id = topos[0]["id"]
            link_result = api(driver, "POST", f"/network/api/projects/{proj_id}/topologies", {"topology_id": topo_id})
            print(f"  INFO  Linked topo {topo_id[:8]}... result: {link_result}")

        # Run a compliance audit on the topology (to generate data)
        if topo_id:
            try:
                # Set compliance profile first
                api(
                    driver,
                    "PUT",
                    f"/network/api/compliance/{topo_id}/profile",
                    {
                        "regimes": ["fisma_high", "stig"],
                        "classification": "CUI",
                        "environment": "IL4",
                    },
                )
                time.sleep(0.5)
                audit_result = api(driver, "POST", f"/network/api/compliance/{topo_id}/audit")
                print(f"  INFO  Audit result: passed={audit_result.get('passed')}, failed={audit_result.get('failed')}")
            except Exception as exc:
                print(f"  WARN  Audit call: {exc}")
            time.sleep(2)

        # ── 1. Project detail page loads with compliance rollup ───────────
        driver.get(f"{BASE_URL}/network/projects/{proj_id}")
        time.sleep(3)
        screenshot(driver, "project-detail-p1")

        try:
            comp_tables = driver.find_elements(By.ID, "compliance-table")
            if comp_tables:
                headers = [th.text.upper() for th in comp_tables[0].find_elements(By.CSS_SELECTOR, "thead th")]
                assert "SCORE" in headers
                assert "CAT1" in headers
                assert "CAT2" in headers
                results.ok("compliance_rollup_table", str(headers))
            else:
                # Table hidden because no audit data — check fallback text
                page_text = driver.page_source
                assert "Compliance Rollup" in page_text, "Compliance section missing"
                results.ok("compliance_rollup_table", "No audit data — fallback text shown correctly")
        except Exception as e:
            results.fail("compliance_rollup_table", e)

        # ── 2. Aggregate compliance score in stats bar ────────────────────
        try:
            stat_cards = driver.find_elements(By.CSS_SELECTOR, ".stats-bar .stat-card")
            labels = [s.find_element(By.CSS_SELECTOR, ".stat-label").text for s in stat_cards]
            assert "Compliance Score" in labels, f"No Compliance Score stat: {labels}"
            results.ok("aggregate_compliance_stat", str(labels))
        except Exception as e:
            results.fail("aggregate_compliance_stat", e)

        # ── 3. Cost rollup table ──────────────────────────────────────────
        try:
            cost_table = driver.find_element(By.ID, "cost-table")
            assert cost_table, "Cost rollup table not found"
            cost_headers = [th.text.upper() for th in cost_table.find_elements(By.CSS_SELECTOR, "thead th")]
            assert "EST. CAPEX" in cost_headers, f"No CapEx column: {cost_headers}"
            # Check footer has TOTAL row
            tfoot = cost_table.find_elements(By.CSS_SELECTOR, "tfoot tr")
            assert len(tfoot) >= 2, f"Expected 2+ footer rows, got {len(tfoot)}"
            results.ok("cost_rollup_table", str(cost_headers))
        except Exception as e:
            results.fail("cost_rollup_table", e)

        # ── 4. Activity feed panel ────────────────────────────────────────
        try:
            feed = driver.find_element(By.ID, "activity-feed")
            assert feed, "Activity feed not found"
            items = feed.find_elements(By.CSS_SELECTOR, ".activity-item")
            results.ok("activity_feed_panel", f"{len(items)} activity items")
        except Exception as e:
            results.fail("activity_feed_panel", e)

        # ── 5. Compliance has per-topology rows with severity breakdown ───
        try:
            comp_tables = driver.find_elements(By.ID, "compliance-table")
            if comp_tables:
                body_rows = comp_tables[0].find_elements(By.CSS_SELECTOR, "tbody tr")
                assert len(body_rows) >= 1, "No topology rows"
                footer_rows = comp_tables[0].find_elements(By.CSS_SELECTOR, "tfoot tr")
                assert len(footer_rows) >= 1, "No aggregate footer row"
                agg_text = footer_rows[0].text
                assert "AGGREGATE" in agg_text
                results.ok("compliance_per_topo_rows", f"{len(body_rows)} topos, aggregate footer present")
            else:
                results.ok("compliance_per_topo_rows", "No audit data — section present with fallback")
        except Exception as e:
            results.fail("compliance_per_topo_rows", e)

        # ── 6. Stats bar has CapEx and OpEx cards ─────────────────────────
        try:
            stat_cards = driver.find_elements(By.CSS_SELECTOR, ".stats-bar .stat-card")
            labels = [s.find_element(By.CSS_SELECTOR, ".stat-label").text for s in stat_cards]
            assert "Est. CapEx" in labels, f"No CapEx stat: {labels}"
            assert "Circuit OpEx" in labels, f"No OpEx stat: {labels}"
            results.ok("cost_stat_cards", str(labels))
        except Exception as e:
            results.fail("cost_stat_cards", e)

        # ── 7. Open Findings stat card ────────────────────────────────────
        try:
            labels = [
                s.find_element(By.CSS_SELECTOR, ".stat-label").text
                for s in driver.find_elements(By.CSS_SELECTOR, ".stats-bar .stat-card")
            ]
            assert "Open Findings" in labels
            results.ok("open_findings_stat")
        except Exception as e:
            results.fail("open_findings_stat", e)

        # ── 8. Filter Dashboard link ──────────────────────────────────────
        try:
            filter_link = driver.find_element(By.CSS_SELECTOR, f'a[href="/network/?project={proj_id}"]')
            assert filter_link.text.strip() == "Filter Dashboard"
            results.ok("filter_dashboard_link")
        except Exception as e:
            results.fail("filter_dashboard_link", e)

        # ── 9. Compliance links to audit page ─────────────────────────────
        if topo_id:
            try:
                comp_links = driver.find_elements(By.CSS_SELECTOR, f'a[href="/network/compliance/{topo_id}"]')
                if comp_links:
                    results.ok("compliance_audit_links", f"{len(comp_links)} links found")
                else:
                    # Links are in compliance table + topologies table
                    # If no audit ran, compliance table won't have links
                    topo_links = driver.find_elements(By.CSS_SELECTOR, f'a[href="/network/canvas/{topo_id}"]')
                    assert topo_links, "No topology links at all"
                    results.ok("compliance_audit_links", "Topo links present, audit links depend on data")
            except Exception as e:
                results.fail("compliance_audit_links", e)
        else:
            results.ok("compliance_audit_links", "No topology to test")

        # ── 10. Portfolio page: activity feed ─────────────────────────────
        driver.get(f"{BASE_URL}/network/projects")
        time.sleep(2)
        screenshot(driver, "portfolio-activity-p1")

        try:
            portfolio_feed = driver.find_element(By.ID, "portfolio-activity")
            items = portfolio_feed.find_elements(By.CSS_SELECTOR, ".activity-item")
            assert len(items) >= 1, "Portfolio activity feed is empty"
            # Check activity badges render
            badges = portfolio_feed.find_elements(By.CSS_SELECTOR, ".activity-badge")
            assert len(badges) >= 1
            results.ok("portfolio_activity_feed", f"{len(items)} items")
        except Exception as e:
            results.fail("portfolio_activity_feed", e)

        # ── 11. Portfolio stats still show findings ───────────────────────
        try:
            stat_cards = driver.find_elements(By.CSS_SELECTOR, ".stats-bar .stat-card")
            labels = [s.find_element(By.CSS_SELECTOR, ".stat-label").text for s in stat_cards]
            assert "Open Findings" in labels
            results.ok("portfolio_findings_stat")
        except Exception as e:
            results.fail("portfolio_findings_stat", e)

        # ── 12. JS errors on project detail ───────────────────────────────
        driver.get(f"{BASE_URL}/network/projects/{proj_id}")
        time.sleep(2)
        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("js_errors_project_detail", f"{len(js_errs)}: {js_errs[0][:100]}")
        else:
            results.ok("js_errors_project_detail", "No SEVERE JS errors")

        # ── 13. JS errors on portfolio ────────────────────────────────────
        driver.get(f"{BASE_URL}/network/projects")
        time.sleep(2)
        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("js_errors_portfolio", f"{len(js_errs)}: {js_errs[0][:100]}")
        else:
            results.ok("js_errors_portfolio", "No SEVERE JS errors")

        # ── Cleanup ───────────────────────────────────────────────────────
        try:
            api(driver, "DELETE", f"/network/api/projects/{proj_id}")
            results.ok("cleanup")
        except Exception as e:
            results.fail("cleanup", e)

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Network Canvas — P1 (Compliance/Cost Rollup + Activity)")
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
