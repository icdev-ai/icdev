"""
E2E Selenium test — Geolocation + Map View (Phase 4).

Tests:
1.  Set device geolocation
2.  List device geolocations
3.  Bulk set geolocation
4.  Geo sites aggregation
5.  Geo links for map arcs
6.  Map page loads
7.  Map nav link present
8.  Delete geolocation
9.  Clear All regression
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


def run_tests():
    results = TestResult()
    driver = create_driver()
    geo_ids = []

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # Create a topology for geo
        t = api(
            driver,
            "POST",
            "/network/api/topologies",
            {
                "name": "Geo Test Topo",
                "graph_json": {
                    "nodes": [
                        {"id": "r1", "label": "DC-East-R1", "type": "router", "x": 100, "y": 100},
                        {"id": "r2", "label": "DC-West-R1", "type": "router", "x": 300, "y": 100},
                    ],
                    "edges": [],
                },
            },
        )
        topo_id = t.get("id")

        # ── 1. Set device geo ─────────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/device-geo",
                {
                    "topology_id": topo_id,
                    "node_id": "r1",
                    "label": "DC-East-R1",
                    "site_name": "DC-East",
                    "latitude": 38.9072,
                    "longitude": -77.0369,
                    "city": "Washington",
                    "state": "DC",
                    "facility": "Equinix DC6",
                },
            )
            assert d.get("id")
            geo_ids.append(d["id"])
            results.ok("set_device_geo", f"id={d['id']}")
        except Exception as e:
            results.fail("set_device_geo", e)

        # ── 2. List geolocations ──────────────────────────────────────────
        try:
            d = api(driver, "GET", f"/network/api/device-geo?topology_id={topo_id}")
            assert len(d) >= 1
            assert d[0]["site_name"] == "DC-East"
            results.ok("list_device_geo", f"{len(d)} entries")
        except Exception as e:
            results.fail("list_device_geo", e)

        # ── 3. Bulk set geo ───────────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/device-geo/bulk",
                {
                    "devices": [
                        {
                            "topology_id": topo_id,
                            "node_id": "r2",
                            "label": "DC-West-R1",
                            "site_name": "DC-West",
                            "latitude": 37.3382,
                            "longitude": -121.8863,
                            "city": "San Jose",
                            "state": "CA",
                            "facility": "Equinix SV5",
                        },
                    ]
                },
            )
            assert d.get("set") >= 1
            results.ok("bulk_set_geo", f"set={d['set']}")
        except Exception as e:
            results.fail("bulk_set_geo", e)

        # ── 4. Geo sites aggregation ──────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/geo-sites")
            assert len(d) >= 2
            sites = {s["site_name"] for s in d}
            assert "DC-East" in sites
            assert "DC-West" in sites
            results.ok("geo_sites_aggregation", f"{len(d)} sites: {sites}")
        except Exception as e:
            results.fail("geo_sites_aggregation", e)

        # ── 5. Geo links ─────────────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/geo-links")
            # May be empty if no interconnects with geo
            assert isinstance(d, list)
            results.ok("geo_links", f"{len(d)} links")
        except Exception as e:
            results.fail("geo_links", e)

        # ── 6. Map page loads ─────────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/map")
            time.sleep(3)
            title = driver.find_element(By.CSS_SELECTOR, "#map-toolbar")
            assert title
            container = driver.find_element(By.ID, "map-container")
            assert container
            results.ok("map_page_loads")
        except Exception as e:
            results.fail("map_page_loads", e)

        # ── 7. Map nav link ───────────────────────────────────────────────
        try:
            assert "Map" in driver.page_source, "Nav link Map not in page"
            results.ok("map_nav_link")
        except Exception as e:
            results.fail("map_nav_link", e)

        # ── 8. Delete geo ─────────────────────────────────────────────────
        if geo_ids:
            try:
                d = api(driver, "DELETE", f"/network/api/device-geo/{geo_ids[0]}")
                assert d.get("ok")
                results.ok("delete_device_geo")
            except Exception as e:
                results.fail("delete_device_geo", e)

        # ── 9. Clear All regression ───────────────────────────────────────
        try:
            d = api(driver, "DELETE", "/network/api/topologies/clear-all")
            assert d.get("cleared") == "topologies"
            results.ok("clear_all_regression")
        except Exception as e:
            results.fail("clear_all_regression", e)

        # Restore
        try:
            api(
                driver,
                "POST",
                "/network/api/topologies",
                {
                    "name": "Restored After Geo Test",
                    "graph_json": {
                        "nodes": [{"id": "r1", "label": "R1", "type": "router", "x": 100, "y": 100}],
                        "edges": [],
                    },
                },
            )
        except Exception:
            pass

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Geolocation + Map View (Phase 4)")
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
