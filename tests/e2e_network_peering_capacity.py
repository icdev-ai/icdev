"""
E2E Selenium test — Peering + Capacity + Facilities + Readiness.

Tests:
1.  Create peering agreement (multi-routing)
2.  Peering evaluation with auto-scoring
3.  Peering cost-benefit analysis
4.  Peering page loads
5.  Port inventory CRUD
6.  Fiber inventory CRUD
7.  Carrier availability CRUD
8.  Capacity page loads
9.  Create facility with power/cooling
10. Create rack in facility
11. Facilities page loads
12. Readiness checker cross-layer
13. Clear All regression
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
    peer_id = fac_id = pid = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # Setup project
        d = api(driver, "POST", "/network/api/projects", {"name": "E2E Peering Test", "status": "draft"})
        pid = d.get("id")

        # ── 1. Create peering agreement ───────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/peering-agreements",
                {
                    "peer_name": "CloudFlare",
                    "peer_asn": "13335",
                    "our_asn": "64512",
                    "peering_type": "settlement_free",
                    "routing_method": "bgp",
                    "purpose": "Reduce latency for CDN traffic",
                    "purpose_category": "content_delivery",
                    "business_justification": "Save $5K/mo transit cost",
                    "port_speed": "10G",
                    "monthly_cost": 0,
                    "project_id": pid,
                },
            )
            peer_id = d.get("id")
            assert peer_id
            results.ok("create_peering", f"id={peer_id}")
        except Exception as e:
            results.fail("create_peering", e)

        # Also create static route peering
        try:
            d = api(
                driver,
                "POST",
                "/network/api/peering-agreements",
                {
                    "peer_name": "Enterprise Customer",
                    "routing_method": "static",
                    "peering_type": "paid",
                    "purpose": "Dedicated customer link",
                    "purpose_category": "customer",
                    "monthly_cost": 2000,
                },
            )
            assert d.get("id")
            results.ok("create_static_peering")
        except Exception as e:
            results.fail("create_static_peering", e)

        # ── 2. Peer evaluation ────────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/peering-evaluations",
                {
                    "peer_name": "Akamai",
                    "peer_asn": "20940",
                    "traffic_volume": 5000,
                    "geographic_overlap": "high",
                    "noc_quality": "excellent",
                    "prefix_count": 500,
                },
            )
            assert d.get("id")
            evals = api(driver, "GET", "/network/api/peering-evaluations")
            our = next(e for e in evals if e.get("peer_name") == "Akamai")
            assert float(our["score"]) > 0
            results.ok("peer_evaluation", f"score={our['score']}, rec={our['recommendation']}")
        except Exception as e:
            results.fail("peer_evaluation", e)

        # ── 3. Cost-benefit ───────────────────────────────────────────────
        if peer_id:
            try:
                d = api(driver, "GET", f"/network/api/peering-cost-benefit/{peer_id}")
                assert "peering_cost_monthly" in d
                assert "transit_equivalent_monthly" in d
                results.ok("peering_cost_benefit", f"savings=${d.get('monthly_savings', 0)}/mo")
            except Exception as e:
                results.fail("peering_cost_benefit", e)

        # ── 4. Peering page ───────────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/peering")
            time.sleep(2)
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "Peering" in title.text
            results.ok("peering_page_loads")
        except Exception as e:
            results.fail("peering_page_loads", e)

        # ── 5. Port inventory ─────────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/port-inventory",
                {
                    "device_label": "Core-R1",
                    "total_ports": 48,
                    "used_ports": 36,
                },
            )
            assert d.get("id")
            ports = api(driver, "GET", "/network/api/port-inventory")
            assert len(ports) >= 1
            results.ok("port_inventory", f"avail={ports[0]['total_ports'] - ports[0]['used_ports']}")
        except Exception as e:
            results.fail("port_inventory", e)

        # ── 6. Fiber inventory ────────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/fiber-inventory",
                {
                    "path_name": "DC-East to DC-West",
                    "path_a": "DC-East",
                    "path_z": "DC-West",
                    "total_strands": 48,
                    "lit_strands": 12,
                    "total_lambdas": 80,
                    "active_lambdas": 24,
                    "conduit_ducts": 4,
                    "conduit_used": 2,
                },
            )
            assert d.get("id")
            fibers = api(driver, "GET", "/network/api/fiber-inventory")
            assert len(fibers) >= 1
            assert int(fibers[0]["available_strands"]) == 36
            results.ok(
                "fiber_inventory",
                f"avail_strands={fibers[0]['available_strands']}, avail_lambdas={fibers[0]['available_lambdas']}",
            )
        except Exception as e:
            results.fail("fiber_inventory", e)

        # ── 7. Carrier availability ───────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/carrier-availability",
                {
                    "carrier": "Lumen",
                    "path_name": "DC-East to DC-West",
                    "service_type": "wavelength",
                    "available_bandwidth": "up to 100G",
                    "lead_time_days": 45,
                    "monthly_cost_est": 8000,
                },
            )
            assert d.get("id")
            results.ok("carrier_availability")
        except Exception as e:
            results.fail("carrier_availability", e)

        # ── 8. Capacity page ──────────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/capacity")
            time.sleep(2)
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "Capacity" in title.text
            results.ok("capacity_page_loads")
        except Exception as e:
            results.fail("capacity_page_loads", e)

        # ── 9. Create facility ────────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/facilities",
                {
                    "name": "DC-East-1",
                    "facility_type": "colo",
                    "operator": "Equinix",
                    "city": "Ashburn",
                    "state": "VA",
                    "total_racks": 10,
                    "used_racks": 7,
                    "total_power_kw": 50,
                    "used_power_kw": 35,
                    "total_cooling_tons": 15,
                    "used_cooling_tons": 10,
                    "ups_capacity_kva": 60,
                    "ups_load_kva": 40,
                    "ups_runtime_min": 15,
                },
            )
            fac_id = d.get("id")
            assert fac_id
            results.ok("create_facility", f"id={fac_id}")
        except Exception as e:
            results.fail("create_facility", e)

        # ── 10. Create rack ───────────────────────────────────────────────
        if fac_id:
            try:
                d = api(
                    driver,
                    "POST",
                    "/network/api/racks",
                    {
                        "facility_id": fac_id,
                        "rack_name": "Row-A Rack-1",
                        "total_ru": 42,
                        "used_ru": 28,
                        "max_power_kw": 5.0,
                        "current_power_kw": 3.2,
                        "power_circuit_a": "PDU-A-01",
                        "power_circuit_b": "PDU-B-01",
                    },
                )
                assert d.get("id")
                racks = api(driver, "GET", f"/network/api/racks?facility_id={fac_id}")
                assert len(racks) >= 1
                results.ok("create_rack", f"avail_ru={racks[0]['total_ru'] - racks[0]['used_ru']}")
            except Exception as e:
                results.fail("create_rack", e)

        # ── 11. Facilities page ───────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/facilities")
            time.sleep(2)
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "Facilities" in title.text
            results.ok("facilities_page_loads")
        except Exception as e:
            results.fail("facilities_page_loads", e)

        # ── 12. Readiness checker ─────────────────────────────────────────
        if pid:
            try:
                d = api(driver, "GET", f"/network/api/readiness-check/{pid}")
                assert "checks" in d
                assert "readiness_pct" in d
                assert d.get("total") >= 1
                results.ok(
                    "readiness_checker",
                    f"ready={d.get('ready')}, pct={d.get('readiness_pct')}%, checks={d.get('total')}",
                )
            except Exception as e:
                results.fail("readiness_checker", e)

        # ── 13. Clear All ─────────────────────────────────────────────────
        try:
            d = api(driver, "DELETE", "/network/api/topologies/clear-all")
            assert d.get("cleared") == "topologies"
            results.ok("clear_all_regression")
        except Exception as e:
            results.fail("clear_all_regression", e)

        # Restore + cleanup
        try:
            api(
                driver,
                "POST",
                "/network/api/topologies",
                {
                    "name": "Restored",
                    "graph_json": {
                        "nodes": [{"id": "r1", "label": "R1", "type": "router", "x": 100, "y": 100}],
                        "edges": [],
                    },
                },
            )
        except Exception:
            pass
        if pid:
            try:
                api(driver, "DELETE", f"/network/api/projects/{pid}")
            except Exception:
                pass
        if fac_id:
            try:
                api(driver, "DELETE", f"/network/api/facilities/{fac_id}")
            except Exception:
                pass

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Peering + Capacity + Facilities + Readiness")
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
