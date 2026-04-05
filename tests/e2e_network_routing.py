"""
E2E Selenium test — Routing Table Topology + Config Sync (Phase 3).

Tests:
1.  Store routing entries for device A
2.  Store routing entries for device B
3.  List routing entries by device IP
4.  Generate topology from routing tables
5.  Generated topology has correct nodes and edges
6.  Config-to-canvas sync updates device properties
7.  Clear All regression
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

        # ── 1. Store routes for device A ──────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/routing-entries",
                {
                    "device_ip": "10.0.0.1",
                    "hostname": "core-r1",
                    "entries": [
                        {
                            "prefix": "10.1.0.0/24",
                            "next_hop": "10.0.0.2",
                            "protocol": "ospf",
                            "metric": 110,
                            "interface": "Gi0/0",
                        },
                        {
                            "prefix": "10.2.0.0/24",
                            "next_hop": "10.0.0.3",
                            "protocol": "bgp",
                            "metric": 20,
                            "admin_distance": 20,
                            "interface": "Gi0/1",
                        },
                        {"prefix": "10.0.0.0/24", "next_hop": "0.0.0.0", "protocol": "connected", "interface": "Gi0/0"},
                    ],
                },
            )
            assert d.get("stored") == 3
            results.ok("store_routes_device_a", f"stored={d['stored']}")
        except Exception as e:
            results.fail("store_routes_device_a", e)

        # ── 2. Store routes for device B ──────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/routing-entries",
                {
                    "device_ip": "10.0.0.2",
                    "hostname": "dist-sw1",
                    "entries": [
                        {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1", "protocol": "ospf", "interface": "Gi0/0"},
                        {
                            "prefix": "10.1.0.0/24",
                            "next_hop": "0.0.0.0",
                            "protocol": "connected",
                            "interface": "Vlan100",
                        },
                        {"prefix": "10.3.0.0/24", "next_hop": "10.0.0.4", "protocol": "ospf", "interface": "Gi0/2"},
                    ],
                },
            )
            assert d.get("stored") == 3
            results.ok("store_routes_device_b", f"stored={d['stored']}")
        except Exception as e:
            results.fail("store_routes_device_b", e)

        # ── 3. List by device IP ──────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/routing-entries?device_ip=10.0.0.1")
            assert len(d) >= 3
            protos = {r["protocol"] for r in d}
            assert "ospf" in protos and "bgp" in protos
            results.ok("list_routes_by_device", f"{len(d)} entries")
        except Exception as e:
            results.fail("list_routes_by_device", e)

        # ── 4. Generate topology from routes ──────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/routing-topology",
                {"device_ips": ["10.0.0.1", "10.0.0.2"], "name": "E2E Routing Topology"},
            )
            assert d.get("id")
            assert d.get("nodes") >= 3  # r1, sw1, + next-hops
            assert d.get("edges") >= 2
            topo_id = d["id"]
            results.ok(
                "generate_routing_topology",
                f"nodes={d['nodes']}, edges={d['edges']}, devices={d['devices_discovered']}",
            )
        except Exception as e:
            results.fail("generate_routing_topology", e)
            topo_id = None

        # ── 5. Verify topology structure ──────────────────────────────────
        if topo_id:
            try:
                t = api(driver, "GET", f"/network/api/topologies/{topo_id}")
                gj = t.get("graph_json", "{}")
                graph = json.loads(gj) if isinstance(gj, str) else gj
                labels = {n.get("label", "") for n in graph.get("nodes", [])}
                assert "core-r1" in labels, f"Missing core-r1 in {labels}"
                # dist-sw1 may appear as hostname or IP depending on data
                has_dist = "dist-sw1" in labels or "10.0.0.2" in labels
                assert has_dist, f"Missing dist-sw1/10.0.0.2 in {labels}"
                # Check edges represent actual routing relationships
                assert len(graph["edges"]) >= 2
                results.ok("verify_routing_topology", f"labels: {labels}")
            except Exception as e:
                results.fail("verify_routing_topology", e)

        # ── 6. Config-to-canvas sync ──────────────────────────────────────
        try:
            # Create a topology with a device matching collected config
            t = api(
                driver,
                "POST",
                "/network/api/topologies",
                {
                    "name": "Sync Test Topo",
                    "graph_json": {
                        "nodes": [
                            {
                                "id": "n1",
                                "label": "core-r1",
                                "type": "router",
                                "x": 100,
                                "y": 100,
                                "config": {"ip": "10.0.0.1"},
                            },
                        ],
                        "edges": [],
                    },
                },
            )
            sync_topo = t.get("id")
            # Store a version config for this device
            api(
                driver,
                "POST",
                "/network/api/collected-configs",
                {
                    "device_ip": "10.0.0.1",
                    "hostname": "core-r1",
                    "command_name": "version",
                    "output_text": "Cisco IOS XE 17.9",
                    "parsed_json": {"version": "17.9.4a", "model": "ASR1001-X", "serial": "FOC12345"},
                    "topology_id": sync_topo,
                },
            )
            # Small delay for DB write to commit
            time.sleep(1)
            # Sync
            d = api(driver, "POST", f"/network/api/config-to-canvas/{sync_topo}")
            assert d.get("updated_devices") >= 1, f"Expected 1+ updated, got {d}"
            # Verify synced
            t2 = api(driver, "GET", f"/network/api/topologies/{sync_topo}")
            gj = t2.get("graph_json", "{}")
            graph = json.loads(gj) if isinstance(gj, str) else gj
            cfg = graph.get("nodes", [{}])[0].get("config") or graph.get("nodes", [{}])[0].get("configData") or {}
            assert cfg.get("sw_version") == "17.9.4a"
            assert cfg.get("model") == "ASR1001-X"
            results.ok("config_to_canvas_sync", f"version={cfg.get('sw_version')}, model={cfg.get('model')}")
        except Exception as e:
            results.fail("config_to_canvas_sync", e)

        # ── 7. Clear All regression ───────────────────────────────────────
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
                    "name": "Restored After Routing Test",
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
    print("E2E: Routing Table Topology + Config Sync (Phase 3)")
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
