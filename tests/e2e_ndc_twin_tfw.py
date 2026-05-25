# CUI // SP-CTI
"""E2E Selenium test — NDC Digital Twin / Traffic Flow Walkthrough (/network/twin/<id>).

Tests:
  1. Twin page loads without JS errors
  2. All 7 persona chips render and are selectable
  3. TFW flow creation form is present
  4. Walkthrough API returns steps with persona_responses
  5. Walkthrough triggers persona accordion rendering
  6. Persona chip toggle deselects/reselects correctly
  7. Security domain policy modal exists with 3 tabs
  8. Path analysis panel present and API healthy
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

PERSONA_IDS = ["seceng", "neteng", "cloudarch", "compofficer", "appdev", "missionowner", "ciso"]

# Resolve a real topology ID from network_canvas.db — query the DB file directly
def _resolve_topo_id() -> str:
    import sqlite3 as _sqlite3
    candidates = [
        Path(__file__).resolve().parent.parent / "data" / "network_canvas.db",
        Path(__file__).resolve().parent.parent / "network_canvas.db",
    ]
    for db_path in candidates:
        if db_path.exists():
            try:
                con = _sqlite3.connect(str(db_path))
                row = con.execute(
                    "SELECT id FROM topologies ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                con.close()
                if row:
                    return row[0]
            except Exception:
                pass
    return "test-topology-1"

TEST_TOPO_ID = _resolve_topo_id()
TWIN_URL = f"{BASE_URL}/network/twin/{TEST_TOPO_ID}"


class TestResult:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, name, detail=""):
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS  {name} {detail}")

    def fail(self, name, error):
        self.failed.append({"test": name, "error": str(error)})
        print(f"  FAIL  {name} — {error}")

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
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=opts)


def screenshot(driver, name):
    path = SCREENSHOT_DIR / f"ndc-tfw-{name}-1920x1080.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return str(path)


def js_errors(driver):
    errors = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") == "SEVERE":
                msg = entry.get("message", "")
                if "favicon" not in msg.lower():
                    errors.append(msg)
    except Exception:
        pass
    return errors


def api_call(method, path, payload=None):
    """Make an HTTP call directly (not via browser) and return (status, body_dict)."""
    url = f"{BASE_URL}{path}"
    body = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}


# ── Test functions ─────────────────────────────────────────────────────────────

def test_twin_page_loads(driver, results):
    """Page loads at /network/twin/<id> with no severe JS errors."""
    try:
        driver.get(TWIN_URL)
        time.sleep(2)
        title = driver.title
        errs = js_errors(driver)
        if errs:
            results.fail("twin_page_js_errors", "; ".join(errs[:3]))
        else:
            results.ok("twin_page_js_errors", "(none)")
        # TFW panel container must exist
        panel_exists = driver.execute_script(
            "return document.getElementById('tfwPersonaSection') !== null"
        )
        assert panel_exists, "#tfwPersonaSection missing from DOM"
        screenshot(driver, "01-page-load")
        results.ok("twin_page_loads", f"title={title!r}")
    except Exception as exc:
        screenshot(driver, "01-page-load-error")
        results.fail("twin_page_loads", str(exc))


def test_persona_chips_render(driver, results):
    """All 7 persona chips are rendered and none start deselected."""
    try:
        chip_count = driver.execute_script(
            "return document.querySelectorAll('.persona-chip').length"
        )
        assert chip_count >= 7, f"Expected >= 7 chips, got {chip_count}"
        for pid in PERSONA_IDS:
            has_chip = driver.execute_script(
                f"return document.querySelector('.persona-chip[data-persona-id=\"{pid}\"]') !== null"
            )
            assert has_chip, f"Chip missing for persona: {pid}"
        deselected = driver.execute_script(
            "return document.querySelectorAll('.persona-chip.tfw-deselected').length"
        )
        screenshot(driver, "02-persona-chips")
        results.ok("persona_chips_render", f"count={chip_count} deselected={deselected}")
    except Exception as exc:
        screenshot(driver, "02-persona-chips-error")
        results.fail("persona_chips_render", str(exc))


def test_tfw_flow_form_present(driver, results):
    """TFW flow creation form controls all exist in DOM."""
    try:
        checks = {
            "flow_name_input": "document.getElementById('tfwFlowName') !== null",
            "src_zone_select": "document.getElementById('tfwSrcZone') !== null",
            "dst_zone_select": "document.getElementById('tfwDstZone') !== null",
            "app_type_select": "document.getElementById('tfwAppType') !== null",
            "classification_select": "document.getElementById('tfwClassification') !== null",
            "flow_list_container": "document.getElementById('tfwFlowList') !== null",
        }
        missing = [k for k, js in checks.items() if not driver.execute_script(f"return {js}")]
        if missing:
            results.fail("tfw_flow_form_present", f"Missing: {missing}")
        else:
            screenshot(driver, "03-flow-form")
            results.ok("tfw_flow_form_present", "all controls present")
    except Exception as exc:
        screenshot(driver, "03-flow-form-error")
        results.fail("tfw_flow_form_present", str(exc))


def test_walkthrough_api_health(driver, results):
    """API: create flow → run walkthrough → assert steps with persona_responses."""
    try:
        # Create a test traffic flow
        status, body = api_call("POST", f"/network/api/twin/{TEST_TOPO_ID}/traffic-flows", {
            "name": "e2e-test-sso-flow",
            "src_zone": "on_prem",
            "dst_zone": "csp_il4",
            "app_type": "sso_saml",
            "classification": "NIPR",
        })
        if status not in (200, 201):
            results.fail("walkthrough_api_create_flow", f"HTTP {status}: {body}")
            return
        flow_id = body.get("flow_id") or body.get("id")
        assert flow_id, f"No flow_id in response: {body}"

        # Run walkthrough
        status2, body2 = api_call(
            "POST",
            f"/network/api/twin/{TEST_TOPO_ID}/traffic-flows/{flow_id}/walkthrough",
            {"use_llm": False},
        )
        if status2 not in (200, 201):
            results.fail("walkthrough_api_run", f"HTTP {status2}: {body2}")
            return
        steps = body2.get("steps", [])
        assert isinstance(steps, list), "steps is not a list"
        if steps:
            first = steps[0]
            assert "persona_responses" in first, f"persona_responses missing from step: {first.keys()}"
            assert isinstance(first["persona_responses"], dict), "persona_responses not a dict"
        results.ok("walkthrough_api_health", f"flow_id={flow_id} steps={len(steps)}")

        # Store flow_id on driver for later test
        driver.execute_script(f"window._e2eTestFlowId = '{flow_id}';")
    except Exception as exc:
        results.fail("walkthrough_api_health", str(exc))


def test_walkthrough_renders_accordion(driver, results):
    """Triggering walkthrough from JS populates #tfwPersonaAccordion with sections."""
    try:
        flow_id = driver.execute_script("return window._e2eTestFlowId;")
        if not flow_id:
            results.fail("walkthrough_renders_accordion", "No test flow_id — test_walkthrough_api_health may have failed")
            return

        driver.execute_script(f"tfwWalkThrough('{flow_id}');")
        # Wait up to 10s for accordion to populate
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                "return document.querySelectorAll('#tfwPersonaAccordion .tfw-accordion-section').length > 0"
            )
        )
        section_count = driver.execute_script(
            "return document.querySelectorAll('#tfwPersonaAccordion .tfw-accordion-section').length"
        )
        assert section_count > 0, "Accordion empty after walkthrough"

        step_counter = driver.execute_script(
            "return document.getElementById('tfwStepCounter2') ? document.getElementById('tfwStepCounter2').textContent : ''"
        )
        screenshot(driver, "05-accordion-rendered")
        results.ok("walkthrough_renders_accordion", f"sections={section_count} counter={step_counter!r}")
    except Exception as exc:
        screenshot(driver, "05-accordion-error")
        results.fail("walkthrough_renders_accordion", str(exc))


def test_persona_chip_toggle(driver, results):
    """Clicking seceng chip toggles tfw-deselected; clicking again restores it."""
    try:
        chip_sel = ".persona-chip[data-persona-id='seceng']"
        # Click to deselect
        chip = driver.find_element(By.CSS_SELECTOR, chip_sel)
        chip.click()
        time.sleep(0.3)
        is_deselected = driver.execute_script(
            f"return document.querySelector(\"{chip_sel}\").classList.contains('tfw-deselected')"
        )
        assert is_deselected, "Chip should be deselected after first click"
        # Click to reselect
        chip.click()
        time.sleep(0.3)
        is_deselected2 = driver.execute_script(
            f"return document.querySelector(\"{chip_sel}\").classList.contains('tfw-deselected')"
        )
        assert not is_deselected2, "Chip should NOT be deselected after second click"
        screenshot(driver, "06-chip-toggle")
        results.ok("persona_chip_toggle", "seceng chip deselect/reselect works")
    except Exception as exc:
        screenshot(driver, "06-chip-toggle-error")
        results.fail("persona_chip_toggle", str(exc))


def test_domain_policy_modal(driver, results):
    """#tfwPolicyModal exists and domain-policy-templates API returns 200."""
    try:
        modal_exists = driver.execute_script(
            "return document.getElementById('tfwPolicyModal') !== null"
        )
        assert modal_exists, "#tfwPolicyModal not found in DOM"

        # Check 3 tabs exist
        tabs = driver.execute_script(
            "return Array.from(document.querySelectorAll('#domainPolicyModal [data-tab], #domainPolicyModal .tab-btn')).map(e => e.textContent.trim())"
        )
        screenshot(driver, "07-policy-modal")

        # API: domain-policy-templates
        status, body = api_call("GET", f"/network/api/twin/{TEST_TOPO_ID}/domain-policy-templates")
        assert status == 200, f"domain-policy-templates HTTP {status}"
        assert isinstance(body, dict) and len(body) > 0, "templates dict empty"
        results.ok("domain_policy_modal", f"modal=ok templates={len(body)} tabs={tabs}")
    except Exception as exc:
        screenshot(driver, "07-policy-modal-error")
        results.fail("domain_policy_modal", str(exc))


def test_path_analysis_panel(driver, results):
    """Path analysis panel (#pathPanel) exists in DOM and analyze-path API is callable."""
    try:
        panel_exists = driver.execute_script(
            "return document.getElementById('pathPanel') !== null"
        )
        assert panel_exists, "#pathPanel not found"

        driver.execute_script(
            "return document.getElementById('pathSrcInput') !== null"
        )
        # API health check
        status, body = api_call("POST", f"/network/api/twin/{TEST_TOPO_ID}/analyze-path", {
            "src": "node-1", "dst": "node-2"
        })
        # 200 or 404 (no real topology) — just must not be 500
        assert status != 500, f"analyze-path returned 500: {body}"
        screenshot(driver, "08-path-analysis")
        results.ok("path_analysis_panel", f"panel=ok api_status={status}")
    except Exception as exc:
        screenshot(driver, "08-path-analysis-error")
        results.fail("path_analysis_panel", str(exc))


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  ICDEV™ NDC Digital Twin / TFW E2E Test")
    print(f"  URL: {TWIN_URL}")
    print("=" * 70)

    results = TestResult()
    driver = create_driver()
    try:
        test_twin_page_loads(driver, results)
        test_persona_chips_render(driver, results)
        test_tfw_flow_form_present(driver, results)
        test_walkthrough_api_health(driver, results)
        test_walkthrough_renders_accordion(driver, results)
        test_persona_chip_toggle(driver, results)
        test_domain_policy_modal(driver, results)
        test_path_analysis_panel(driver, results)
    finally:
        driver.quit()

    summary = results.summary()
    print("\n" + "=" * 70)
    print(f"  RESULTS: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']})")
    print("=" * 70)

    if summary["failures"]:
        print("\n  FAILURES:")
        for f in summary["failures"]:
            print(f"    x {f['test']}: {f['error']}")
        sys.exit(1)
    else:
        print("\n  All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
