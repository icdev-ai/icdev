"""
E2E Selenium test — Device Command Profiles + Discovery Config (Phase 2).

Tests:
1.  List built-in device profiles (7 vendors)
2.  Get profile with commands (Cisco IOS)
3.  Get profile commands include routing, BGP, OSPF
4.  Create user-defined profile (custom vendor)
5.  Update user profile commands
6.  Delete user profile
7.  Cannot delete built-in profile
8.  Add custom command to built-in profile
9.  Create discovery config with safety constraints
10. Discovery config enforces read_only=1
11. Store collected config output
12. Retrieve collected config
13. Clear All regression
14. JS error checks
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
    user_prof_id = None
    disc_id = None
    config_id = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # ── 1. List built-in profiles ─────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/device-profiles")
            assert len(d) >= 7, f"Expected 7+ profiles, got {len(d)}"
            vendors = [p["vendor"] for p in d]
            assert "Cisco" in vendors
            assert "Juniper" in vendors
            assert "Palo Alto" in vendors
            assert "Fortinet" in vendors
            assert "Nokia" in vendors
            results.ok("list_builtin_profiles", f"{len(d)} profiles, vendors: {set(vendors)}")
        except Exception as e:
            results.fail("list_builtin_profiles", e)

        # ── 2. Get Cisco IOS profile ──────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/device-profiles/prof-cisco-ios")
            assert d.get("vendor") == "Cisco"
            assert d.get("commands")
            cmds = d["commands"]
            assert "running_config" in cmds
            assert "routing_table_v4" in cmds
            results.ok("get_cisco_ios_profile", f"{len(cmds)} commands")
        except Exception as e:
            results.fail("get_cisco_ios_profile", e)

        # ── 3. Profile has routing/BGP/OSPF commands ──────────────────────
        try:
            cmds = d["commands"]
            assert "bgp_summary" in cmds
            assert "ospf_neighbors" in cmds
            assert "routing_table_v6" in cmds
            assert cmds["running_config"]["command"] == "show running-config"
            results.ok("profile_has_routing_commands")
        except Exception as e:
            results.fail("profile_has_routing_commands", e)

        # ── 4. Create user profile ────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/device-profiles",
                {
                    "vendor": "Huawei",
                    "platform": "VRP",
                    "description": "Huawei routers and switches",
                    "commands": {
                        "running_config": {
                            "command": "display current-configuration",
                            "parser": "vrp_config",
                            "timeout_sec": 30,
                        },
                        "routing_table_v4": {
                            "command": "display ip routing-table",
                            "parser": "vrp_route",
                            "timeout_sec": 15,
                        },
                        "interfaces": {
                            "command": "display ip interface brief",
                            "parser": "vrp_interface",
                            "timeout_sec": 10,
                        },
                        "version": {"command": "display version", "parser": "vrp_version", "timeout_sec": 10},
                    },
                },
            )
            user_prof_id = d.get("id")
            assert user_prof_id
            results.ok("create_user_profile", f"id={user_prof_id}")
        except Exception as e:
            results.fail("create_user_profile", e)

        # ── 5. Update user profile ────────────────────────────────────────
        if user_prof_id:
            try:
                api(
                    driver,
                    "PUT",
                    f"/network/api/device-profiles/{user_prof_id}",
                    {
                        "description": "Huawei VRP (Updated)",
                        "commands_json": {
                            "routing_table_v4": {
                                "command": "display ip routing-table verbose",
                                "parser": "vrp_route_v",
                                "timeout_sec": 20,
                            }
                        },
                    },
                )
                d = api(driver, "GET", f"/network/api/device-profiles/{user_prof_id}")
                assert "Updated" in d.get("description", "")
                results.ok("update_user_profile")
            except Exception as e:
                results.fail("update_user_profile", e)

        # ── 6. Delete user profile ────────────────────────────────────────
        if user_prof_id:
            try:
                d = api(driver, "DELETE", f"/network/api/device-profiles/{user_prof_id}")
                assert d.get("ok")
                results.ok("delete_user_profile")
                user_prof_id = None
            except Exception as e:
                results.fail("delete_user_profile", e)

        # ── 7. Cannot delete built-in ─────────────────────────────────────
        try:
            d = api(driver, "DELETE", "/network/api/device-profiles/prof-cisco-ios")
            assert d.get("error")
            results.ok("cannot_delete_builtin", d.get("error", ""))
        except Exception as e:
            results.fail("cannot_delete_builtin", e)

        # ── 8. Add command to built-in ────────────────────────────────────
        try:
            api(
                driver,
                "PUT",
                "/network/api/device-profiles/prof-cisco-ios",
                {
                    "commands": {
                        "custom_check": {"command": "show processes cpu", "parser": "ios_cpu", "timeout_sec": 10}
                    }
                },
            )
            d = api(driver, "GET", "/network/api/device-profiles/prof-cisco-ios")
            assert "custom_check" in d.get("commands", {})
            results.ok("add_command_to_builtin")
        except Exception as e:
            results.fail("add_command_to_builtin", e)

        # ── 9. Create discovery config ────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/discovery-configs",
                {
                    "name": "E2E Test Scan",
                    "profile_id": "prof-cisco-ios",
                    "targets": ["10.0.0.0/24", "192.168.1.1"],
                    "method": "ssh",
                    "rate_limit_per_sec": 0.5,
                    "max_concurrent": 3,
                    "hop_limit": 1,
                    "max_devices": 50,
                },
            )
            disc_id = d.get("id")
            assert disc_id
            results.ok("create_discovery_config", f"id={disc_id}")
        except Exception as e:
            results.fail("create_discovery_config", e)

        # ── 10. Discovery enforces read_only ──────────────────────────────
        try:
            configs = api(driver, "GET", "/network/api/discovery-configs")
            our_config = next((c for c in configs if c.get("id") == disc_id), None)
            assert our_config
            assert our_config.get("read_only") == 1, f"read_only should be 1, got {our_config.get('read_only')}"
            results.ok("discovery_read_only_enforced")
        except Exception as e:
            results.fail("discovery_read_only_enforced", e)

        # ── 11. Store collected config ────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/collected-configs",
                {
                    "device_ip": "10.0.0.1",
                    "hostname": "core-r1",
                    "profile_id": "prof-cisco-ios",
                    "command_name": "version",
                    "output_text": "Cisco IOS XE Software, Version 17.09.04a\nuptime is 45 days",
                    "parsed_json": {"version": "17.09.04a", "uptime_days": 45},
                },
            )
            config_id = d.get("id")
            assert config_id
            results.ok("store_collected_config", f"id={config_id}")
        except Exception as e:
            results.fail("store_collected_config", e)

        # ── 12. Retrieve collected config ─────────────────────────────────
        if config_id:
            try:
                d = api(driver, "GET", f"/network/api/collected-configs/{config_id}")
                assert d.get("hostname") == "core-r1"
                assert d.get("output_text")
                assert d.get("parsed", {}).get("version") == "17.09.04a"
                results.ok("retrieve_collected_config")
            except Exception as e:
                results.fail("retrieve_collected_config", e)

        # ── 13. Clear All regression ──────────────────────────────────────
        try:
            d = api(driver, "DELETE", "/network/api/topologies/clear-all")
            assert d.get("cleared") == "topologies"
            d2 = api(driver, "DELETE", "/network/api/simulations/clear-all")
            assert d2.get("cleared") == "simulations"
            results.ok("clear_all_regression")
        except Exception as e:
            results.fail("clear_all_regression", e)

        # Restore topology
        api(
            driver,
            "POST",
            "/network/api/topologies",
            {
                "name": "Restored After Profiles Test",
                "graph_json": {
                    "nodes": [{"id": "r1", "label": "R1", "type": "router", "x": 100, "y": 100}],
                    "edges": [],
                },
            },
        )

        # ── 14. JS errors ─────────────────────────────────────────────────
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)
        errs = check_js_errors(driver)
        if errs:
            results.fail("js_errors", f"{len(errs)}: {errs[0][:80]}")
        else:
            results.ok("js_errors", "No SEVERE JS errors")

    finally:
        if user_prof_id:
            try:
                api(driver, "DELETE", f"/network/api/device-profiles/{user_prof_id}")
            except Exception:
                pass
        if disc_id:
            try:
                api(driver, "DELETE", f"/network/api/discovery-configs/{disc_id}")
            except Exception:
                pass
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Device Command Profiles + Discovery (Phase 2)")
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
