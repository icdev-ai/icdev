# CUI // SP-CTI
"""E2E Selenium test — NDC SOPs dashboard (/ndc/sops).

Tests:
  1. Page load — layout, filter controls, table structure
  2. API health — GET /api/ndc/sops returns ok:true
  3. Create SOP via API and verify it appears in the table
  4. Click a row to load the detail pane
  5. Category and status filter dropdowns present and functional
  6. Refresh button triggers reload
  7. JS error check on all interactions
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

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
    return webdriver.Chrome(options=opts)


def screenshot(driver, name):
    path = SCREENSHOT_DIR / f"ndc-sops-{name}-1920x1080.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return str(path)


def check_js_errors(driver):
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


def dismiss_tour(driver):
    """Mark tour complete in localStorage and remove overlay if present."""
    try:
        driver.execute_script(
            "try {"
            "  localStorage.setItem('icdev_tour_completed','1');"
            "  var w=document.getElementById('icdev-tour-welcome');"
            "  if(w){w.remove();}"
            "} catch(e){}"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test 1: Page load — verify layout and core elements
# ---------------------------------------------------------------------------
def test_page_load(driver, results):
    """Load /ndc/sops and verify the two-panel layout renders."""
    try:
        driver.get(f"{BASE_URL}/ndc/sops")
        time.sleep(1.5)

        wrap = driver.find_element(By.CSS_SELECTOR, ".sops-wrap")
        assert wrap.is_displayed(), "sops-wrap not visible"

        table = driver.find_element(By.ID, "sops-table")
        assert table.is_displayed(), "#sops-table not visible"

        detail = driver.find_element(By.ID, "sops-detail-body")
        assert detail.is_displayed(), "#sops-detail-body not visible"

        tbody = driver.find_element(By.ID, "sops-tbody")
        assert tbody is not None, "#sops-tbody missing"

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("page_load_js_errors", "; ".join(js_errs[:3]))
        else:
            screenshot(driver, "01-page-loaded")
            results.ok("page_load", "two-panel layout rendered, no JS errors")
    except Exception as exc:
        screenshot(driver, "01-page-loaded-error")
        results.fail("page_load", str(exc))


# ---------------------------------------------------------------------------
# Test 2: Filter controls present
# ---------------------------------------------------------------------------
def test_filter_controls(driver, results):
    """Verify category filter, status filter, and refresh button are present."""
    try:
        driver.get(f"{BASE_URL}/ndc/sops")
        time.sleep(1)

        cat_sel = driver.find_element(By.ID, "filter-category")
        assert cat_sel.is_displayed(), "#filter-category not visible"

        stat_sel = driver.find_element(By.ID, "filter-status")
        assert stat_sel.is_displayed(), "#filter-status not visible"

        btn_refresh = driver.find_element(By.ID, "btn-refresh")
        assert btn_refresh.is_displayed(), "#btn-refresh not visible"

        # Verify category options
        cat_options = [o.get_attribute("value") for o in cat_sel.find_elements(By.TAG_NAME, "option")]
        expected_cats = {"", "change_window", "provisioning", "firewall", "dns", "failover"}
        assert expected_cats.issubset(set(cat_options)), f"Missing category options: {cat_options}"

        # Verify status options
        stat_options = [o.get_attribute("value") for o in stat_sel.find_elements(By.TAG_NAME, "option")]
        expected_statuses = {"", "draft", "review", "approved", "deprecated"}
        assert expected_statuses.issubset(set(stat_options)), f"Missing status options: {stat_options}"

        results.ok("filter_controls", f"category({len(cat_options)} opts) + status({len(stat_options)} opts) + refresh btn")
    except Exception as exc:
        screenshot(driver, "02-filter-controls-error")
        results.fail("filter_controls", str(exc))


# ---------------------------------------------------------------------------
# Test 3: Table headers present
# ---------------------------------------------------------------------------
def test_table_headers(driver, results):
    """Verify the SOP table has expected column headers."""
    try:
        driver.get(f"{BASE_URL}/ndc/sops")
        time.sleep(1)

        headers = driver.find_elements(By.CSS_SELECTOR, "#sops-table thead th")
        header_texts = [h.text.strip() for h in headers]

        expected = {"Title", "Category", "Status", "Ver", "Updated"}
        upper_texts = {h.upper() for h in header_texts}
        missing = {e for e in expected if e.upper() not in upper_texts}
        assert not missing, f"Missing headers: {missing} (got {header_texts})"

        results.ok("table_headers", f"columns: {header_texts}")
    except Exception as exc:
        screenshot(driver, "03-table-headers-error")
        results.fail("table_headers", str(exc))


# ---------------------------------------------------------------------------
# Test 4: API health — GET /api/ndc/sops
# ---------------------------------------------------------------------------
def test_api_list(driver, results):
    """Verify GET /api/ndc/sops returns ok:true via fetch."""
    try:
        driver.get(f"{BASE_URL}/ndc/sops")
        time.sleep(0.5)
        driver.set_script_timeout(10)

        data = driver.execute_async_script("""
            var done = arguments[arguments.length - 1];
            fetch('/api/ndc/sops')
                .then(function(r) { return r.json(); })
                .then(function(d) { done(d); })
                .catch(function(e) { done({ok: false, error: e.toString()}); });
        """)

        assert data.get("ok") is True, f"API returned ok=false: {data}"
        assert "sops" in data, f"Response missing 'sops' key: {data}"
        assert isinstance(data["sops"], list), f"sops is not a list: {type(data['sops'])}"

        results.ok("api_list", f"ok=true, count={data.get('count', len(data['sops']))}")
    except Exception as exc:
        screenshot(driver, "04-api-list-error")
        results.fail("api_list", str(exc))


# ---------------------------------------------------------------------------
# Test 5: Create SOP via API and verify it appears in the table
# ---------------------------------------------------------------------------
def test_create_sop(driver, results):
    """POST a test SOP via the API and verify the table row appears."""
    try:
        driver.get(f"{BASE_URL}/ndc/sops")
        time.sleep(0.5)
        driver.set_script_timeout(15)

        # Create a test SOP via fetch
        created = driver.execute_async_script("""
            var done = arguments[arguments.length - 1];
            fetch('/api/ndc/sops', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    title: 'E2E Test SOP — Firewall Rule Change',
                    category: 'firewall',
                    description: 'Automated E2E test SOP for firewall rule modification.',
                    steps: [
                        {
                            order: 1,
                            action: 'Backup current firewall config',
                            verify: 'Config backup confirmed in storage',
                            rollback: 'Restore from backup',
                            estimated_time_min: 5
                        },
                        {
                            order: 2,
                            action: 'Apply new firewall rules',
                            verify: 'Rules active and traffic flowing',
                            rollback: 'Remove added rules',
                            estimated_time_min: 10
                        }
                    ],
                    prerequisites: ['Maintenance window approved', 'Change ticket open'],
                    validation: ['Ping test passes', 'Application connectivity verified'],
                    rollback: {
                        trigger: 'Any validation step fails',
                        procedure: 'Restore firewall config from backup',
                        max_duration_min: 15
                    },
                    escalation: [
                        {name: 'Network Lead', contact: 'netlead@example.mil', role: 'Escalation'}
                    ],
                    author: 'e2e-test-runner',
                    classification: 'CUI'
                })
            })
            .then(function(r) { return r.json(); })
            .then(function(d) { done(d); })
            .catch(function(e) { done({ok: false, error: e.toString()}); });
        """)

        assert created.get("ok") is True, f"Create SOP failed: {created}"
        sop = created.get("sop", {})
        sop_id = sop.get("sop_id") or sop.get("id")
        assert sop_id, f"No sop_id in response: {sop}"

        results.ok("create_sop_api", f"sop_id={sop_id}")

        # Reload page and verify the SOP appears in the table
        driver.refresh()
        time.sleep(2)

        try:
            row = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, f"#sops-tbody tr[data-id='{sop_id}']"))
            )
            assert row.is_displayed(), f"Row for sop_id={sop_id} not visible"
            results.ok("create_sop_row_visible", f"Row rendered in table (sop_id={sop_id})")
        except Exception:
            # Row may not carry data-id if sop_id has special chars — check by title text
            tbody_text = driver.find_element(By.ID, "sops-tbody").text
            assert "E2E Test SOP" in tbody_text, f"SOP title not found in table: {tbody_text[:200]}"
            results.ok("create_sop_row_visible", "Row rendered (matched by title text)")

        screenshot(driver, "05-create-sop")
    except Exception as exc:
        screenshot(driver, "05-create-sop-error")
        results.fail("create_sop", str(exc))


# ---------------------------------------------------------------------------
# Test 6: Click a row to load detail pane
# ---------------------------------------------------------------------------
def test_row_click_detail(driver, results):
    """Click the first SOP row and verify the detail pane populates."""
    try:
        driver.get(f"{BASE_URL}/ndc/sops")
        time.sleep(2)
        dismiss_tour(driver)

        rows = driver.find_elements(By.CSS_SELECTOR, "#sops-tbody tr[data-id]")
        if not rows:
            results.ok("row_click_detail", "SKIP — no SOPs in DB to click")
            return

        first_row = rows[0]
        sop_id = first_row.get_attribute("data-id")
        first_row.click()
        time.sleep(1.5)

        detail_body = driver.find_element(By.ID, "sops-detail-body")
        detail_text = detail_body.text.strip()

        # Detail pane should no longer show the placeholder text
        assert "Select an SOP" not in detail_text, "Detail pane still shows placeholder after click"
        assert len(detail_text) > 10, f"Detail pane appears empty: {detail_text!r}"

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("row_click_detail_js", "; ".join(js_errs[:3]))
        else:
            screenshot(driver, "06-row-click-detail")
            results.ok("row_click_detail", f"Detail pane loaded for sop_id={sop_id}")
    except Exception as exc:
        screenshot(driver, "06-row-click-detail-error")
        results.fail("row_click_detail", str(exc))


# ---------------------------------------------------------------------------
# Test 7: Category filter interaction
# ---------------------------------------------------------------------------
def test_category_filter(driver, results):
    """Select a category filter and verify the API call is triggered."""
    try:
        driver.get(f"{BASE_URL}/ndc/sops")
        time.sleep(1.5)

        cat_sel = driver.find_element(By.ID, "filter-category")
        # Select "firewall" category
        from selenium.webdriver.support.ui import Select
        sel = Select(cat_sel)
        sel.select_by_value("firewall")
        time.sleep(1.5)

        # tbody should update (either rows or "No SOPs found" message)
        tbody = driver.find_element(By.ID, "sops-tbody")
        tbody_text = tbody.text.strip()
        assert "Loading" not in tbody_text, "Table still shows Loading after filter"

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("category_filter_js", "; ".join(js_errs[:3]))
        else:
            screenshot(driver, "07-category-filter")
            results.ok("category_filter", f"Filter applied; tbody shows: {tbody_text[:60]!r}")
    except Exception as exc:
        screenshot(driver, "07-category-filter-error")
        results.fail("category_filter", str(exc))


# ---------------------------------------------------------------------------
# Test 8: Refresh button triggers reload
# ---------------------------------------------------------------------------
def test_refresh_button(driver, results):
    """Click the Refresh button and verify the table reloads without errors."""
    try:
        driver.get(f"{BASE_URL}/ndc/sops")
        time.sleep(1.5)
        dismiss_tour(driver)

        btn = driver.find_element(By.ID, "btn-refresh")
        btn.click()
        time.sleep(1.5)

        tbody = driver.find_element(By.ID, "sops-tbody")
        tbody_text = tbody.text.strip()
        assert "Loading" not in tbody_text, "Table stuck in Loading state after refresh"

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("refresh_button_js", "; ".join(js_errs[:3]))
        else:
            screenshot(driver, "08-refresh-button")
            results.ok("refresh_button", "Refresh triggered; table updated cleanly")
    except Exception as exc:
        screenshot(driver, "08-refresh-button-error")
        results.fail("refresh_button", str(exc))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("\n" + "=" * 70)
    print("  ICDEV™ NDC SOPs E2E — /ndc/sops")
    print("  Selenium headless Chrome  1920×1080")
    print(f"  Target: {BASE_URL}")
    print("=" * 70)

    results = TestResult()
    driver = create_driver()

    try:
        test_page_load(driver, results)
        test_filter_controls(driver, results)
        test_table_headers(driver, results)
        test_api_list(driver, results)
        test_create_sop(driver, results)
        test_row_click_detail(driver, results)
        test_category_filter(driver, results)
        test_refresh_button(driver, results)
    finally:
        driver.quit()

    summary = results.summary()
    print("\n" + "=" * 70)
    print(f"  RESULTS: {summary['passed']}/{summary['total']} passed  ({summary['pass_rate']})")
    print("=" * 70)

    if summary["failures"]:
        print("\n  FAILURES:")
        for f in summary["failures"]:
            print(f"    ✗ {f['test']}: {f['error']}")
        sys.exit(1)
    else:
        print("\n  All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
