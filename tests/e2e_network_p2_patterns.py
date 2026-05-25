"""
E2E Selenium test — Design Pattern Library (Phase 2 of Workbench).

Tests:
1.  List patterns API returns built-in patterns
2.  Get single pattern with graph_json
3.  Pattern categories API
4.  Create user-defined pattern
5.  Update user-defined pattern
6.  Delete user-defined pattern
7.  Cannot delete built-in pattern
8.  Save pattern from selection API
9.  Filter by category
10. JS error checks
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
    user_pat_id = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # ── 1. List patterns ──────────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/design-patterns")
            assert len(d) >= 9, f"Expected 9+ patterns, got {len(d)}"
            builtin = [p for p in d if p.get("is_builtin")]
            assert len(builtin) >= 9
            results.ok("list_patterns", f"{len(d)} total, {len(builtin)} built-in")
        except Exception as e:
            results.fail("list_patterns", e)

        # ── 2. Get single pattern ─────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/design-patterns/pat-redundant-core")
            assert d.get("name") == "Redundant Core"
            assert d.get("graph_json", {}).get("nodes")
            assert len(d["graph_json"]["nodes"]) == 2
            results.ok("get_single_pattern", d["name"])
        except Exception as e:
            results.fail("get_single_pattern", e)

        # ── 3. Categories API ─────────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/design-patterns/categories")
            cats = [c["category"] for c in d]
            assert "routing" in cats
            assert "security" in cats
            assert "wan" in cats
            results.ok("categories_api", str(cats))
        except Exception as e:
            results.fail("categories_api", e)

        # ── 4. Create user pattern ────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/design-patterns",
                {
                    "name": "E2E Custom Pattern",
                    "category": "custom",
                    "description": "Test user-defined pattern",
                    "graph_json": {
                        "nodes": [
                            {"id": "n1", "label": "R1", "type": "router", "x": 100, "y": 100},
                            {"id": "n2", "label": "SW1", "type": "switch-l3", "x": 300, "y": 100},
                        ],
                        "edges": [{"id": "e1", "source": "n1", "target": "n2", "label": "10GbE", "protocol": "lacp"}],
                    },
                    "tags": ["e2e", "test"],
                },
            )
            user_pat_id = d.get("id")
            assert user_pat_id
            results.ok("create_user_pattern", f"id={user_pat_id}")
        except Exception as e:
            results.fail("create_user_pattern", e)

        # ── 5. Update user pattern ────────────────────────────────────────
        if user_pat_id:
            try:
                d = api(
                    driver,
                    "PUT",
                    f"/network/api/design-patterns/{user_pat_id}",
                    {"name": "E2E Custom Pattern (Updated)", "tags": ["e2e", "updated"]},
                )
                assert d.get("ok")
                # Verify
                d2 = api(driver, "GET", f"/network/api/design-patterns/{user_pat_id}")
                assert d2["name"] == "E2E Custom Pattern (Updated)"
                results.ok("update_user_pattern")
            except Exception as e:
                results.fail("update_user_pattern", e)

        # ── 6. Delete user pattern ────────────────────────────────────────
        if user_pat_id:
            try:
                d = api(driver, "DELETE", f"/network/api/design-patterns/{user_pat_id}")
                assert d.get("ok")
                results.ok("delete_user_pattern")
                user_pat_id = None  # already deleted
            except Exception as e:
                results.fail("delete_user_pattern", e)

        # ── 7. Cannot delete built-in ─────────────────────────────────────
        try:
            d = api(driver, "DELETE", "/network/api/design-patterns/pat-redundant-core")
            assert d.get("error"), f"Expected error, got: {d}"
            results.ok("cannot_delete_builtin", d.get("error", ""))
        except Exception as e:
            results.fail("cannot_delete_builtin", e)

        # ── 8. Save from selection ────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/design-patterns/save-from-selection",
                {
                    "name": "E2E Selection Pattern",
                    "category": "custom",
                    "graph_json": {
                        "nodes": [{"id": "s1", "label": "FW1", "type": "firewall", "x": 0, "y": 0}],
                        "edges": [],
                    },
                },
            )
            sel_id = d.get("id")
            assert sel_id
            # Cleanup
            api(driver, "DELETE", f"/network/api/design-patterns/{sel_id}")
            results.ok("save_from_selection")
        except Exception as e:
            results.fail("save_from_selection", e)

        # ── 9. Filter by category ─────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/design-patterns?category=wan")
            assert len(d) >= 2
            assert all(p["category"] == "wan" for p in d)
            results.ok("filter_by_category", f"{len(d)} wan patterns")
        except Exception as e:
            results.fail("filter_by_category", e)

        # ── 10. JS errors ─────────────────────────────────────────────────
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)
        errs = check_js_errors(driver)
        if errs:
            results.fail("js_errors", f"{len(errs)}: {errs[0][:80]}")
        else:
            results.ok("js_errors", "No SEVERE JS errors")

    finally:
        # Cleanup any remaining test patterns
        if user_pat_id:
            try:
                api(driver, "DELETE", f"/network/api/design-patterns/{user_pat_id}")
            except Exception:
                pass
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Design Pattern Library (Phase 2)")
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
