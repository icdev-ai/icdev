"""
E2E Selenium test — NDC Path Reachability (dt-ndc-05).

Tests:
1. Create test topology with two connected nodes + a transit gateway node
2. Load /network/twin/<topo_id> and scroll to Path Analysis panel
3. Enter two connected nodes, click Analyze Path — assert verdict banner and path table render
4. Enter fuzzy node name "transit gateway" — assert resolve note appears
5. Verify no uncaught JS errors throughout
"""

import json
import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

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


def api_call(driver, method, path, data=None):
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
    topo_id = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # ── 1. Create topology with two connected nodes + transit gateway ──
        try:
            graph_json = {
                "nodes": [
                    {"id": "core-r1", "label": "Core Router", "type": "router"},
                    {"id": "web-srv-01", "label": "Web Server", "type": "server"},
                    {"id": "tgw-01", "label": "Transit Gateway", "type": "router"},
                    {"id": "vpc-a", "label": "VPC-A", "type": "server"},
                ],
                "edges": [
                    {"source": "core-r1", "target": "web-srv-01", "protocol": "BGP"},
                    {"source": "tgw-01", "target": "vpc-a", "protocol": "VPC-Peering"},
                ],
            }
            topo = api_call(
                driver,
                "POST",
                "/network/api/topologies",
                {"name": "ndc-path-test", "graph_json": graph_json},
            )
            assert "id" in topo, f"No id in response: {topo}"
            topo_id = topo["id"]
            results.ok("create_topology", f"id={topo_id}")
        except Exception as e:
            results.fail("create_topology", e)
            return results

        # ── 2. Load twin page and scroll to Path Analysis ─────────────────
        try:
            driver.get(f"{BASE_URL}/network/twin/{topo_id}")
            time.sleep(2)
            wait = WebDriverWait(driver, 10)
            panel = wait.until(EC.presence_of_element_located((By.ID, "pathPanel")))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", panel)
            time.sleep(0.5)
            assert panel.is_displayed(), "Path Analysis panel not visible"
            results.ok("page_loads_path_panel")
        except Exception as e:
            results.fail("page_loads_path_panel", e)
            return results

        # ── 3. Enter connected nodes, click Analyze Path ───────────────────
        try:
            src_input = driver.find_element(By.ID, "pathSrcInput")
            dst_input = driver.find_element(By.ID, "pathDstInput")
            src_input.clear()
            src_input.send_keys("core-r1")
            dst_input.clear()
            dst_input.send_keys("web-srv-01")
            driver.find_element(By.CSS_SELECTOR, "#pathPanel button.btn-primary").click()
            time.sleep(2)

            # Verdict banner must be visible
            banner = WebDriverWait(driver, 8).until(
                EC.visibility_of_element_located((By.ID, "pathVerdictBanner"))
            )
            banner_text = banner.text.strip()
            assert banner_text, "Verdict banner is empty"
            assert "REACHABLE" in banner_text or "UNREACHABLE" in banner_text or "BLOCKED" in banner_text, \
                f"Unexpected banner text: {banner_text}"

            # Path table must appear
            path_table = driver.find_element(By.ID, "pathTable")
            assert path_table.is_displayed(), "Path table not visible after analysis"

            results.ok("analyze_path_renders_verdict", f"banner={banner_text!r}")
        except Exception as e:
            results.fail("analyze_path_renders_verdict", e)

        # ── 4. Enter fuzzy node name "transit gateway" → resolve note ─────
        try:
            src_input = driver.find_element(By.ID, "pathSrcInput")
            dst_input = driver.find_element(By.ID, "pathDstInput")
            src_input.clear()
            src_input.send_keys("transit gateway")
            dst_input.clear()
            dst_input.send_keys("vpc-a")
            driver.find_element(By.CSS_SELECTOR, "#pathPanel button.btn-primary").click()
            time.sleep(2)

            resolve_note = driver.find_element(By.ID, "pathResolveNote")
            assert resolve_note.is_displayed(), "Resolve note not shown for fuzzy input"
            note_text = resolve_note.text.strip()
            assert "transit gateway" in note_text.lower() or "tgw" in note_text.lower(), \
                f"Unexpected resolve note: {note_text!r}"
            results.ok("fuzzy_resolve_note_shown", f"note={note_text!r}")
        except Exception as e:
            results.fail("fuzzy_resolve_note_shown", e)

        # ── 5. No uncaught JS errors ───────────────────────────────────────
        try:
            logs = driver.get_log("browser")
            severe = [l for l in logs if l.get("level") == "SEVERE" and "favicon" not in l.get("message", "")]
            assert not severe, f"JS errors: {severe}"
            results.ok("no_js_errors")
        except Exception as e:
            results.fail("no_js_errors", e)

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: NDC Path Reachability (dt-ndc-05)")
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
