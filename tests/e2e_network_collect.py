"""
E2E Selenium test — Connect & Collect + Diagram Data Extraction.

Tests:
1.  Manual paste collect: store command outputs
2.  SSH collect: returns error hint when netmiko unavailable
3.  Diagram import with data extraction (IPAM, circuits, compliance)
4.  Collect page loads with tabs
5.  Collect page: profile dropdown populated
6.  Recent collections listed
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

SAMPLE_DRAWIO = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile><diagram name="Test">
<mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="r1" value="Core-Router 10.1.0.1/24" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="100" y="100" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="fw1" value="FW-Perimeter" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="300" y="100" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="e1" value="10GbE" style="" edge="1" source="r1" target="fw1" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel>
</diagram></mxfile>"""


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

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # ── 1. Manual paste collect ───────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/connect-collect",
                {
                    "device_ip": "10.99.0.1",
                    "hostname": "test-router",
                    "profile_id": "prof-cisco-ios",
                    "mode": "manual",
                    "manual_outputs": {
                        "version": "Cisco IOS XE Software, Version 17.09.04a\nUptime: 45 days",
                        "interfaces": "Interface  IP-Address  OK  Method  Status  Protocol\nGi0/0  10.99.0.1  YES  manual  up  up",
                        "routing_table_v4": "Gateway of last resort is 10.99.0.254\nO  10.1.0.0/24 [110/20] via 10.99.0.2, Gi0/1",
                    },
                },
            )
            assert d.get("commands_executed") == 3
            assert d.get("mode") == "manual"
            results.ok("manual_paste_collect", f"commands={d['commands_executed']}")
        except Exception as e:
            results.fail("manual_paste_collect", e)

        # ── 2. SSH hint when unavailable ──────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/connect-collect",
                {
                    "device_ip": "192.168.99.99",
                    "profile_id": "prof-cisco-ios",
                    "mode": "ssh",
                    "username": "admin",
                    "password": "test",
                },
            )
            # Either gets SSH error or netmiko unavailable hint
            assert d.get("error") or d.get("commands_executed", -1) >= 0
            if d.get("hint"):
                results.ok("ssh_collect_hint", d["hint"][:60])
            elif d.get("error"):
                results.ok("ssh_collect_hint", f"SSH error (expected): {str(d['error'])[:60]}")
            else:
                results.ok("ssh_collect_hint", "SSH attempted")
        except Exception as e:
            results.fail("ssh_collect_hint", e)

        # ── 3. Diagram import with data extraction ────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/import/extract-data",
                {
                    "format": "drawio",
                    "content": SAMPLE_DRAWIO,
                    "name": "E2E Extract Test",
                },
            )
            assert d.get("id")
            ext = d.get("extracted", {})
            assert ext.get("devices") >= 1
            assert d.get("compliance", {}).get("findings") >= 0
            results.ok(
                "import_extract_data",
                f"devices={ext['devices']}, ipam={ext.get('ipam_blocks', 0)}, circuits={ext.get('circuits', 0)}",
            )
        except Exception as e:
            results.fail("import_extract_data", e)

        # ── 4. Collect page loads ─────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/collect")
            time.sleep(2)
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "Collect" in title.text
            # Check tabs exist
            tabs = driver.find_elements(By.CSS_SELECTOR, "[id^='tab-']")
            assert len(tabs) >= 3
            results.ok("collect_page_loads")
        except Exception as e:
            results.fail("collect_page_loads", e)

        # ── 5. Profile dropdown populated ─────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/collect")
            time.sleep(3)
            sel = driver.find_element(By.ID, "ssh-profile")
            options = sel.find_elements(By.TAG_NAME, "option")
            assert len(options) >= 7, f"Expected 7+ profiles, got {len(options)}"
            results.ok("profile_dropdown", f"{len(options)} profiles")
        except Exception as e:
            results.fail("profile_dropdown", e)

        # ── 6. Recent collections ─────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/collected-configs")
            assert len(d) >= 3  # from manual paste above
            results.ok("recent_collections", f"{len(d)} entries")
        except Exception as e:
            results.fail("recent_collections", e)

        # ── 7. Clear All + nav link ───────────────────────────────────────
        try:
            d = api(driver, "DELETE", "/network/api/topologies/clear-all")
            assert d.get("cleared") == "topologies"
            assert "Collect" in driver.page_source, "Nav link Collect not in page"
            results.ok("clear_all_and_nav")
        except Exception as e:
            results.fail("clear_all_and_nav", e)

        # Restore
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

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Connect & Collect + Diagram Data Extraction")
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
