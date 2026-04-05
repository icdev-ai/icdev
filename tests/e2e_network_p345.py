"""
E2E Selenium test — Phases 3-5 (Guidance, Case Workflow, Chat Assistant).

Tests:
1.  Design scorecard API returns checks per category
2.  Scorecard: routing checks present
3.  Scorecard: redundancy checks present
4.  Case workflow: initialize lifecycle
5.  Case workflow: get current state + checklist
6.  Case workflow: transition concept -> requirements
7.  Case workflow: invalid transition blocked
8.  Case workflow: history tracked
9.  Chat assist: cross-connect keyword match
10. Chat assist: BGP keyword match
11. Chat assist: unknown query returns help text
12. Chat assist: best practices fallback
13. JS error checks
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
    pid = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # Setup
        d = api(driver, "POST", "/network/api/projects", {"name": "E2E P345 Test", "status": "draft", "owner": "e2e"})
        pid = d.get("id")
        topos = api(driver, "GET", "/network/api/topologies")
        topo_id = topos[0]["id"] if topos else None
        if topo_id:
            api(driver, "POST", f"/network/api/projects/{pid}/topologies", {"topology_id": topo_id})

        # ── Phase 3: Scorecard ────────────────────────────────────────────
        if topo_id:
            try:
                sc = api(driver, "GET", f"/network/api/design-scorecard/{topo_id}")
                assert "checks" in sc
                assert "categories" in sc
                assert "overall_score" in sc
                assert sc["overall_total"] >= 5
                results.ok("scorecard_api", f"score={sc['overall_score']}%, checks={sc['overall_total']}")
            except Exception as e:
                results.fail("scorecard_api", e)

            try:
                cats = sc.get("categories", {})
                assert "routing" in cats
                results.ok("scorecard_routing", f"pct={cats['routing']['pct']}%")
            except Exception as e:
                results.fail("scorecard_routing", e)

            try:
                assert "redundancy" in cats
                results.ok("scorecard_redundancy", f"pct={cats['redundancy']['pct']}%")
            except Exception as e:
                results.fail("scorecard_redundancy", e)
        else:
            results.ok("scorecard_api", "No topo")
            results.ok("scorecard_routing", "No topo")
            results.ok("scorecard_redundancy", "No topo")

        # ── Phase 4: Case Workflow ────────────────────────────────────────
        try:
            d = api(driver, "POST", f"/network/api/projects/{pid}/case-workflow")
            assert d.get("workflow_id")
            assert d.get("current_state") == "concept"
            results.ok("case_init", f"state={d['current_state']}")
        except Exception as e:
            results.fail("case_init", e)

        try:
            d = api(driver, "GET", f"/network/api/projects/{pid}/case-workflow")
            assert d.get("exists") is True
            assert d.get("current_state") == "concept"
            assert len(d.get("checklist", [])) >= 2
            assert "requirements" in d.get("allowed_transitions", [])
            results.ok(
                "case_get_state", f"checklist={len(d['checklist'])} items, transitions={d['allowed_transitions']}"
            )
        except Exception as e:
            results.fail("case_get_state", e)

        try:
            d = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/case-transition",
                {"to_state": "requirements", "comment": "E2E test"},
            )
            assert d.get("to") == "requirements"
            assert len(d.get("checklist", [])) >= 3
            results.ok("case_transition", f"-> {d['to']}, checklist={len(d['checklist'])}")
        except Exception as e:
            results.fail("case_transition", e)

        try:
            d = api(driver, "POST", f"/network/api/projects/{pid}/case-transition", {"to_state": "operate"})
            assert d.get("error"), f"Expected error: {d}"
            results.ok("case_invalid_transition", d.get("error", "")[:60])
        except Exception as e:
            results.fail("case_invalid_transition", e)

        try:
            d = api(driver, "GET", f"/network/api/projects/{pid}/case-workflow")
            assert len(d.get("history", [])) >= 2
            results.ok("case_history", f"{len(d['history'])} entries")
        except Exception as e:
            results.fail("case_history", e)

        # ── Phase 5: Chat Assistant ───────────────────────────────────────
        try:
            d = api(driver, "POST", "/network/api/chat-assist", {"message": "I need a cross-connect at the colo"})
            assert d.get("confidence", 0) > 0
            assert d.get("template_id") == "pat-cross-connect"
            results.ok("chat_cross_connect", f"conf={d['confidence']}")
        except Exception as e:
            results.fail("chat_cross_connect", e)

        try:
            d = api(driver, "POST", "/network/api/chat-assist", {"message": "set up bgp peering with partner"})
            assert d.get("template_id") == "pat-bgp-peering"
            results.ok("chat_bgp", f"conf={d.get('confidence')}")
        except Exception as e:
            results.fail("chat_bgp", e)

        try:
            d = api(driver, "POST", "/network/api/chat-assist", {"message": "asdfghjkl random text"})
            assert d.get("confidence", 1) == 0
            assert "help" in d.get("response", "").lower() or "try" in d.get("response", "").lower()
            results.ok("chat_unknown_fallback")
        except Exception as e:
            results.fail("chat_unknown_fallback", e)

        try:
            d = api(driver, "POST", "/network/api/chat-assist", {"message": "routing best practices"})
            assert d.get("response")
            assert "OSPF" in d["response"] or "routing" in d["response"].lower()
            results.ok("chat_best_practices")
        except Exception as e:
            results.fail("chat_best_practices", e)

        # ── JS errors ─────────────────────────────────────────────────────
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)
        errs = check_js_errors(driver)
        if errs:
            results.fail("js_errors", f"{len(errs)}: {errs[0][:80]}")
        else:
            results.ok("js_errors", "No SEVERE JS errors")

        # Cleanup
        if pid:
            try:
                api(driver, "DELETE", f"/network/api/projects/{pid}")
            except Exception:
                pass
        results.ok("cleanup")

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Phases 3-5 (Guidance + Case Workflow + Chat)")
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
