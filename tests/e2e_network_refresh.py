"""
E2E Selenium test — Tech Refresh Planner (Phase 6).

Tests:
1.  Replacement map: add mapping
2.  Replacement map: list
3.  Refresh plan: add item
4.  Refresh plan: budget by year
5.  Auto-generate refresh plan from topology
6.  Budget forecast enterprise-wide
7.  Delete replacement + refresh item
"""

import json
import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")


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
    rep_id = ref_id = pid = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # Setup project + topology with EOL device
        d = api(driver, "POST", "/network/api/projects", {"name": "E2E Refresh Test", "status": "draft"})
        pid = d.get("id")
        t = api(
            driver,
            "POST",
            "/network/api/topologies",
            {
                "name": "Refresh Topo",
                "graph_json": {
                    "nodes": [
                        {
                            "id": "r1",
                            "label": "Old-Router",
                            "type": "router",
                            "x": 100,
                            "y": 100,
                            "config": {"model": "ASR1001-X", "eol_date": "2025-06-30"},
                        },
                    ],
                    "edges": [],
                },
            },
        )
        topo_id = t.get("id")
        if topo_id and pid:
            api(driver, "POST", f"/network/api/projects/{pid}/topologies", {"topology_id": topo_id})

        # ── 1. Add replacement mapping ────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/replacement-map",
                {
                    "old_vendor": "Cisco",
                    "old_model": "ASR1001-X",
                    "new_vendor": "Cisco",
                    "new_model": "C8300-2N2S-4T2X",
                    "new_cost": 18000,
                    "migration_effort": "medium",
                },
            )
            rep_id = d.get("id")
            assert rep_id
            results.ok("add_replacement", f"id={rep_id}")
        except Exception as e:
            results.fail("add_replacement", e)

        # ── 2. List replacements ──────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/replacement-map")
            assert len(d) >= 1
            results.ok("list_replacements", f"{len(d)} entries")
        except Exception as e:
            results.fail("list_replacements", e)

        # ── 3. Add refresh plan item ──────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/refresh-plan",
                {
                    "device_label": "Core-R1",
                    "old_model": "ASR1001-X",
                    "eol_date": "2025-06-30",
                    "priority": "critical",
                    "replacement_model": "C8300-2N2S-4T2X",
                    "replacement_cost": 18000,
                    "target_year": 2026,
                },
            )
            ref_id = d.get("id")
            assert ref_id
            results.ok("add_refresh_item", f"id={ref_id}")
        except Exception as e:
            results.fail("add_refresh_item", e)

        # ── 4. Budget by year ─────────────────────────────────────────────
        try:
            d = api(driver, "GET", f"/network/api/projects/{pid}/refresh-plan")
            assert d.get("total_cost") >= 18000
            by_yr = d.get("budget_by_year") or {}
            assert "2026" in by_yr or 2026 in by_yr, f"No 2026: {by_yr}"
            results.ok("budget_by_year", f"total=${d['total_cost']}, years={list(d.get('budget_by_year', {}).keys())}")
        except Exception as e:
            results.fail("budget_by_year", e)

        # ── 5. Auto-generate refresh plan ─────────────────────────────────
        try:
            d = api(driver, "POST", f"/network/api/projects/{pid}/auto-refresh-plan")
            assert d.get("items_created") >= 1
            results.ok("auto_refresh_plan", f"created={d['items_created']}")
        except Exception as e:
            results.fail("auto_refresh_plan", e)

        # ── 6. Budget forecast ────────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/budget-forecast")
            assert "forecast" in d
            assert d.get("grand_total") >= 18000
            results.ok("budget_forecast", f"grand_total=${d['grand_total']}, years={len(d['forecast'])}")
        except Exception as e:
            results.fail("budget_forecast", e)

        # ── 7. Delete ─────────────────────────────────────────────────────
        try:
            if rep_id:
                api(driver, "DELETE", f"/network/api/replacement-map/{rep_id}")
            if ref_id:
                api(driver, "DELETE", f"/network/api/refresh-plan/{ref_id}")
            results.ok("delete_items")
        except Exception as e:
            results.fail("delete_items", e)

        # Cleanup
        if pid:
            try:
                api(driver, "DELETE", f"/network/api/projects/{pid}")
            except Exception:
                pass

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Tech Refresh Planner (Phase 6)")
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
