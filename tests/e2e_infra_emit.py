# CUI // SP-CTI
"""E2E Selenium test — /infra/emit IaC generation form.

Fills the emit form with a minimal Ansible graph, submits it, and asserts
that at least one generated file tab is visible in the results pane.

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - Database initialised (infra_designs table present)

Run:
  python tests/e2e_infra_emit.py
  pytest tests/e2e_infra_emit.py -v
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.browser.driver_manager import get_driver  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = _PROJECT_ROOT / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Minimal Ansible-compatible graph: one package node (no UnsupportedResourceError)
_ANSIBLE_GRAPH = json.dumps({
    "nodes": [
        {
            "id": "n1",
            "type": "package",
            "label": "nginx",
            "metadata": {"name": "nginx", "state": "present"},
        }
    ],
    "edges": [],
})


class TestResult:
    def __init__(self):
        self.passed: list[dict] = []
        self.failed: list[dict] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS  {name} {detail}")

    def fail(self, name: str, error) -> None:
        self.failed.append({"test": name, "error": str(error)})
        print(f"  FAIL  {name} — {error}")

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


def _screenshot(driver, name: str) -> str:
    path = SCREENSHOT_DIR / f"{name}.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return str(path)


# ---------------------------------------------------------------------------
# Test 1: Page loads with form controls
# ---------------------------------------------------------------------------
def test_emit_page_loads(driver, results: TestResult) -> None:
    """Navigate to /infra/emit and verify core form controls are present."""
    try:
        driver.get(f"{BASE_URL}/infra/emit")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "emit-form"))
        )
        assert driver.find_element(By.ID, "target").is_displayed(), "#target not visible"
        assert driver.find_element(By.ID, "csp").is_displayed(), "#csp not visible"
        assert driver.find_element(By.ID, "project").is_displayed(), "#project not visible"
        assert driver.find_element(By.ID, "emit-btn").is_displayed(), "#emit-btn not visible"
        _screenshot(driver, "infra-emit-01-load")
        results.ok("emit_page_loads", "form controls rendered")
    except Exception as exc:
        _screenshot(driver, "infra-emit-01-load-error")
        results.fail("emit_page_loads", exc)


# ---------------------------------------------------------------------------
# Test 2: Form submission renders ≥1 generated file tab
# ---------------------------------------------------------------------------
def test_emit_form_submission_renders_files(driver, results: TestResult) -> None:
    """Fill form, submit, assert ≥1 file tab visible in #emit-results."""
    try:
        driver.get(f"{BASE_URL}/infra/emit")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "emit-form"))
        )

        # Select target → triggers JS to update CSP availability
        Select(driver.find_element(By.ID, "target")).select_by_value("ansible")
        # Brief pause for JS change handler to update CSP options
        time.sleep(0.3)

        # Select CSP (ansible supports all CSPs including aws)
        Select(driver.find_element(By.ID, "csp")).select_by_value("aws")

        # Set graph JSON via JS to avoid slow send_keys on long strings
        driver.execute_script(
            "document.getElementById('project').value = arguments[0];",
            _ANSIBLE_GRAPH,
        )

        _screenshot(driver, "infra-emit-02-form-filled")

        # Click submit
        driver.execute_script(
            "document.getElementById('emit-btn').click();"
        )

        # Wait for results pane to become visible (display:block via .visible class)
        WebDriverWait(driver, 20).until(
            EC.visibility_of_element_located((By.ID, "emit-results"))
        )

        # Assert ≥1 file tab rendered in the tab bar
        tabs = driver.find_elements(By.CSS_SELECTOR, "#tab-bar .tab-btn")
        assert len(tabs) >= 1, f"Expected ≥1 file tab in #tab-bar, got {len(tabs)}"

        # Assert result title is present
        title = driver.find_element(By.ID, "result-title")
        assert title.is_displayed(), "#result-title not displayed"

        # Final screenshot (acceptance criteria: playwright/screenshots/infra-emit.png)
        _screenshot(driver, "infra-emit")
        results.ok(
            "emit_form_submission_renders_files",
            f"{len(tabs)} file tab(s) rendered",
        )
    except Exception as exc:
        _screenshot(driver, "infra-emit-error")
        results.fail("emit_form_submission_renders_files", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    results = TestResult()
    driver = get_driver(headless=True, window_size=(1920, 1080))
    driver.implicitly_wait(5)
    try:
        test_emit_page_loads(driver, results)
        test_emit_form_submission_renders_files(driver, results)
    finally:
        driver.quit()

    print()
    print("=" * 60)
    s = results.summary()
    print(f"Results: {s['passed']}/{s['total']} passed ({s['pass_rate']})")
    if s["failures"]:
        print("Failures:")
        for f in s["failures"]:
            print(f"  - {f['test']}: {f['error']}")
    print("=" * 60)
    print()
    print(json.dumps(s, indent=2))

    return 0 if not s["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
# CUI // SP-CTI
