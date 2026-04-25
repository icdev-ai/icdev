# CUI // SP-CTI
"""E2E Selenium lifecycle test — IDC (Infrastructure Design Canvas).

Tests:
  1. Page loads 200         — /infra/ returns content, no SEVERE JS errors
  2. Canvas renders         — /infra/canvas/<id> has .dc-wrap + .dc-canvas-area
  3. IaC panel buttons      — Generate IaC modal has #btn-gen-tf + #btn-gen-ans
  4. POST generate-iac      — /infra/api/export/<id>/terraform returns HCL
  5. POST generate-ansible  — /infra/api/export/<id>/ansible returns YAML

Screenshots: tests/playwright/screenshots/idc-*.png
Pass threshold: >= 4/5
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_NOISE = ("favicon", "/api/projects/progress", "/api/kanban/tasks", "joint.js")

# Shared design ID created in setup — tests 2-5 use it
_design_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestResult:
    def __init__(self):
        self.passed: list[dict] = []
        self.failed: list[dict] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS  {name}{(': ' + detail) if detail else ''}")

    def fail(self, name: str, error) -> None:
        self.failed.append({"test": name, "error": str(error)[:300]})
        print(f"  FAIL  {name} — {str(error)[:300]}")

    def summary(self) -> dict:
        total = len(self.passed) + len(self.failed)
        rate = f"{len(self.passed) / total * 100:.1f}%" if total else "0%"
        return {
            "total": total,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "pass_rate": rate,
            "failures": self.failed,
        }


def create_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


def screenshot(driver: webdriver.Chrome, name: str) -> str:
    path = SCREENSHOT_DIR / f"idc-{name}-1920x1080.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return str(path)


def check_js_errors(driver: webdriver.Chrome) -> list[str]:
    errors: list[str] = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") == "SEVERE":
                msg = entry.get("message", "")
                if not any(n in msg.lower() for n in _NOISE):
                    errors.append(msg)
    except Exception:
        pass
    return errors


def _api_post(path: str, body: dict | None = None) -> tuple[int, dict]:
    """Make a POST request to the dashboard API and return (status, json_body)."""
    url = f"{BASE_URL}{path}"
    payload = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body_bytes = exc.read()
            return exc.code, json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return exc.code, {}


def _create_design() -> str | None:
    """Create a minimal infra design via the API and return its ID."""
    status, data = _api_post(
        "/infra/api/designs",
        {
            "name": "IDC E2E Test Design",
            "description": "Created by e2e_idc_lifecycle.py",
            "graph": {"nodes": [], "edges": []},
            "classification": "CUI",
        },
    )
    if status == 201 and data.get("id"):
        return data["id"]
    return None


# ---------------------------------------------------------------------------
# Test 1: Page loads 200 — /infra/ renders with no SEVERE JS errors
# ---------------------------------------------------------------------------
def test_idc_index_loads(driver: webdriver.Chrome, results: TestResult) -> None:
    """Load /infra/ and verify the page renders with no SEVERE JS errors."""
    try:
        try:
            driver.get_log("browser")
        except Exception:
            pass

        driver.get(f"{BASE_URL}/infra/")
        time.sleep(1.5)

        title = driver.title
        assert title, "Page title is empty — page may not have loaded"

        body_text = driver.find_element(By.TAG_NAME, "body").text
        assert len(body_text) > 10, "Page body is nearly empty"

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("idc_index_loads", f"SEVERE JS error(s): {js_errs[0][:150]}")
        else:
            screenshot(driver, "01-index-loaded")
            results.ok("idc_index_loads", f"title={title[:60]!r}")
    except Exception as exc:
        screenshot(driver, "01-index-loaded-error")
        results.fail("idc_index_loads", str(exc))


# ---------------------------------------------------------------------------
# Test 2: Canvas renders — /infra/canvas/<id> has key DOM elements
# ---------------------------------------------------------------------------
def test_canvas_renders(driver: webdriver.Chrome, results: TestResult) -> None:
    """Navigate to the IDC canvas editor and verify core DOM structure."""
    global _design_id
    try:
        design_id = _design_id
        assert design_id, "No design_id — setup failed to create a design"

        try:
            driver.get_log("browser")
        except Exception:
            pass

        driver.get(f"{BASE_URL}/infra/canvas/{design_id}")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".dc-wrap"))
        )
        time.sleep(1)

        wrap = driver.find_element(By.CSS_SELECTOR, ".dc-wrap")
        assert wrap.is_displayed(), ".dc-wrap not visible"

        toolbar = driver.find_element(By.CSS_SELECTOR, ".dc-toolbar")
        assert toolbar.is_displayed(), ".dc-toolbar not visible"

        canvas_area = driver.find_element(By.CSS_SELECTOR, ".dc-canvas-area")
        assert canvas_area is not None, ".dc-canvas-area not found"

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("canvas_renders", f"SEVERE JS error(s): {js_errs[0][:150]}")
        else:
            screenshot(driver, "02-canvas-renders")
            results.ok("canvas_renders", ".dc-wrap + .dc-toolbar + .dc-canvas-area present")
    except Exception as exc:
        screenshot(driver, "02-canvas-renders-error")
        results.fail("canvas_renders", str(exc))


# ---------------------------------------------------------------------------
# Test 3: IaC panel buttons — modal has #btn-gen-tf and #btn-gen-ans
# ---------------------------------------------------------------------------
def test_iac_panel_buttons(driver: webdriver.Chrome, results: TestResult) -> None:
    """Open the IaC generation modal and assert both generate buttons exist."""
    global _design_id
    try:
        design_id = _design_id
        assert design_id, "No design_id — setup failed to create a design"

        driver.get(f"{BASE_URL}/infra/canvas/{design_id}")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".dc-toolbar"))
        )
        time.sleep(1)

        # Open the IaC panel by calling the JS function directly
        driver.execute_script("if (typeof openIaCPanel === 'function') openIaCPanel();")
        time.sleep(0.5)

        # Both generate buttons must exist in the DOM (panel may be hidden initially)
        btn_tf = driver.find_element(By.ID, "btn-gen-tf")
        assert btn_tf is not None, "#btn-gen-tf not found in DOM"

        btn_ans = driver.find_element(By.ID, "btn-gen-ans")
        assert btn_ans is not None, "#btn-gen-ans not found in DOM"

        screenshot(driver, "03-iac-panel-buttons")
        results.ok(
            "iac_panel_buttons",
            "#btn-gen-tf (Terraform) + #btn-gen-ans (Ansible) present",
        )
    except Exception as exc:
        screenshot(driver, "03-iac-panel-buttons-error")
        results.fail("iac_panel_buttons", str(exc))


# ---------------------------------------------------------------------------
# Test 4: POST generate-iac returns HCL
# ---------------------------------------------------------------------------
def test_generate_iac_returns_hcl(driver: webdriver.Chrome, results: TestResult) -> None:
    """POST /infra/api/export/<id>/terraform and verify the response contains HCL."""
    global _design_id
    try:
        design_id = _design_id
        assert design_id, "No design_id — setup failed to create a design"

        # Use JS fetch from within the browser context (avoids CORS/cookie issues)
        driver.get(f"{BASE_URL}/infra/")
        time.sleep(0.5)
        driver.set_script_timeout(15)

        data = driver.execute_async_script(
            """
            var done = arguments[arguments.length - 1];
            fetch('/infra/api/export/' + arguments[0] + '/terraform', {method: 'POST'})
                .then(function(r) { return r.json().then(function(d) { d.__status = r.status; return d; }); })
                .then(function(d) { done(d); })
                .catch(function(e) { done({ok: false, error: String(e)}); });
            """,
            design_id,
        )

        assert data is not None, "No response from terraform export endpoint"
        assert data.get("ok") is not False, f"Fetch error: {data.get('error')}"
        status = data.get("__status", 200)
        assert status == 200, f"Expected 200, got {status}: {data}"

        assert data.get("format") == "terraform", \
            f"Expected format='terraform', got {data.get('format')!r}"
        assert data.get("data"), "Response 'data' field is empty"

        # Decode base64 and check for HCL content
        hcl_bytes = base64.b64decode(data["data"])
        hcl_text = hcl_bytes.decode("utf-8")
        assert "CUI" in hcl_text or "terraform" in hcl_text.lower() or "#" in hcl_text, \
            "Decoded content does not look like HCL"

        screenshot(driver, "04-generate-iac-hcl")
        results.ok(
            "generate_iac_returns_hcl",
            f"format=terraform, {len(hcl_text)} chars of HCL returned",
        )
    except Exception as exc:
        screenshot(driver, "04-generate-iac-hcl-error")
        results.fail("generate_iac_returns_hcl", str(exc))


# ---------------------------------------------------------------------------
# Test 5: POST generate-ansible returns YAML
# ---------------------------------------------------------------------------
def test_generate_ansible_returns_yaml(driver: webdriver.Chrome, results: TestResult) -> None:
    """POST /infra/api/export/<id>/ansible and verify the response contains YAML."""
    global _design_id
    try:
        design_id = _design_id
        assert design_id, "No design_id — setup failed to create a design"

        driver.get(f"{BASE_URL}/infra/")
        time.sleep(0.5)
        driver.set_script_timeout(15)

        data = driver.execute_async_script(
            """
            var done = arguments[arguments.length - 1];
            fetch('/infra/api/export/' + arguments[0] + '/ansible', {method: 'POST'})
                .then(function(r) { return r.json().then(function(d) { d.__status = r.status; return d; }); })
                .then(function(d) { done(d); })
                .catch(function(e) { done({ok: false, error: String(e)}); });
            """,
            design_id,
        )

        assert data is not None, "No response from ansible export endpoint"
        assert data.get("ok") is not False, f"Fetch error: {data.get('error')}"
        status = data.get("__status", 200)
        assert status == 200, f"Expected 200, got {status}: {data}"

        assert data.get("format") == "ansible", \
            f"Expected format='ansible', got {data.get('format')!r}"
        assert data.get("data"), "Response 'data' field is empty"

        # Decode base64 and check for YAML content
        yaml_bytes = base64.b64decode(data["data"])
        yaml_text = yaml_bytes.decode("utf-8")
        assert "---" in yaml_text or "name:" in yaml_text or "hosts:" in yaml_text, \
            "Decoded content does not look like YAML"

        screenshot(driver, "05-generate-ansible-yaml")
        results.ok(
            "generate_ansible_returns_yaml",
            f"format=ansible, {len(yaml_text)} chars of YAML returned",
        )
    except Exception as exc:
        screenshot(driver, "05-generate-ansible-yaml-error")
        results.fail("generate_ansible_returns_yaml", str(exc))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    global _design_id

    print("=" * 60)
    print("IDC E2E Lifecycle Test")
    print("=" * 60)

    # Setup: create a design to use across tests
    print("\n[Setup] Creating test design via API...")
    _design_id = _create_design()
    if _design_id:
        print(f"  OK  design_id={_design_id}")
    else:
        print("  WARN  Could not create design via API — canvas tests will skip")

    results = TestResult()
    driver = create_driver()
    driver.implicitly_wait(5)

    try:
        print("\n[Tests]")
        test_idc_index_loads(driver, results)
        test_canvas_renders(driver, results)
        test_iac_panel_buttons(driver, results)
        test_generate_iac_returns_hcl(driver, results)
        test_generate_ansible_returns_yaml(driver, results)
    finally:
        driver.quit()

    print()
    print("=" * 60)
    s = results.summary()
    print(f"Results: {s['passed']}/{s['total']} passed ({s['pass_rate']})")
    threshold = 4
    if s["failures"]:
        print("Failures:")
        for f in s["failures"]:
            print(f"  - {f['test']}: {f['error']}")
    passed = s["passed"] >= threshold
    print(f"Pass threshold: {threshold}/5 — {'PASSED' if passed else 'FAILED'}")
    print("=" * 60)
    print()
    print(json.dumps(s, indent=2))

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
# CUI // SP-CTI
