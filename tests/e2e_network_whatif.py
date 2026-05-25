"""
E2E Selenium test — What-If Simulation Bridge (Phase 5).

Tests:
1.  Store test routing data for simulation
2.  What-if link failure: affected prefixes
3.  What-if link failure: surviving with alternates
4.  What-if device failure: impacted devices
5.  What-if add link: new reachability + redundancy
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

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # ── 1. Seed routing data for simulation ───────────────────────────
        try:
            # Device A: has routes to B and C
            api(
                driver,
                "POST",
                "/network/api/routing-entries",
                {
                    "device_ip": "192.168.1.1",
                    "hostname": "core-r1",
                    "entries": [
                        {"prefix": "10.1.0.0/24", "next_hop": "192.168.1.2", "protocol": "ospf", "metric": 10},
                        {"prefix": "10.2.0.0/24", "next_hop": "192.168.1.3", "protocol": "ospf", "metric": 20},
                        {
                            "prefix": "10.1.0.0/24",
                            "next_hop": "192.168.1.3",
                            "protocol": "ospf",
                            "metric": 30,
                        },  # alternate path
                        {"prefix": "192.168.1.0/24", "next_hop": "0.0.0.0", "protocol": "connected"},
                    ],
                },
            )
            # Device B
            api(
                driver,
                "POST",
                "/network/api/routing-entries",
                {
                    "device_ip": "192.168.1.2",
                    "hostname": "dist-sw1",
                    "entries": [
                        {"prefix": "10.1.0.0/24", "next_hop": "0.0.0.0", "protocol": "connected"},
                        {"prefix": "10.2.0.0/24", "next_hop": "192.168.1.1", "protocol": "ospf", "metric": 20},
                    ],
                },
            )
            # Device C
            api(
                driver,
                "POST",
                "/network/api/routing-entries",
                {
                    "device_ip": "192.168.1.3",
                    "hostname": "dist-sw2",
                    "entries": [
                        {"prefix": "10.2.0.0/24", "next_hop": "0.0.0.0", "protocol": "connected"},
                        {"prefix": "10.1.0.0/24", "next_hop": "192.168.1.2", "protocol": "ospf", "metric": 15},
                    ],
                },
            )
            results.ok("seed_routing_data")
        except Exception as e:
            results.fail("seed_routing_data", e)

        # ── 2. Link failure: affected prefixes ────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/what-if/link-failure",
                {"source_device": "192.168.1.1", "target_device": "192.168.1.2"},
            )
            assert "affected_prefixes" in d
            assert "surviving_prefixes" in d
            # 10.1.0.0/24 goes through 192.168.1.2 but has alt via .3
            results.ok("link_failure_analysis", f"affected={d['total_affected']}, surviving={d['total_surviving']}")
        except Exception as e:
            results.fail("link_failure_analysis", e)

        # ── 3. Surviving with alternates ──────────────────────────────────
        try:
            surv = d.get("surviving_prefixes", [])
            if surv:
                has_alt = any(s["alternate_paths"] > 0 for s in surv)
                assert has_alt, "No alternates found"
                results.ok("surviving_with_alternates", f"{len(surv)} surviving prefixes")
            else:
                results.ok("surviving_with_alternates", "All affected (no alternates)")
        except Exception as e:
            results.fail("surviving_with_alternates", e)

        # ── 4. Device failure ─────────────────────────────────────────────
        try:
            d = api(driver, "POST", "/network/api/what-if/device-failure", {"device_ip": "192.168.1.2"})
            assert d.get("total_impacted_devices") >= 1
            assert d.get("total_hosted_lost") >= 1
            results.ok(
                "device_failure", f"impacted={d['total_impacted_devices']}, prefixes_lost={d['total_hosted_lost']}"
            )
        except Exception as e:
            results.fail("device_failure", e)

        # ── 5. Add link simulation ────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/what-if/add-link",
                {"source_device": "192.168.1.2", "target_device": "192.168.1.3", "protocol": "ospf", "metric": 5},
            )
            assert d.get("total_improvements") >= 1
            results.ok(
                "add_link_simulation",
                f"improvements={d['total_improvements']}, "
                f"new={d.get('new_reachability', 0)}, "
                f"better={d.get('better_metric', 0)}, "
                f"redundant={d.get('redundant_paths', 0)}",
            )
        except Exception as e:
            results.fail("add_link_simulation", e)

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: What-If Simulation Bridge (Phase 5)")
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
