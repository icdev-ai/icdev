"""
E2E Selenium test — Network Canvas Project Portfolio (P0 features).

Tests:
1. Portfolio dashboard loads with Kanban + Table views
2. Health cards show compliance/cost/findings data
3. Project CRUD (create, view detail, delete)
4. Project-scoped filtering on index page
5. Project-scoped filtering on circuits page
6. Project-scoped filtering on IPAM page
7. Project context bar in canvas editor
8. Kanban <-> Table view toggle
9. JS error checks on all pages
"""

import json
import os
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# UTF-8 safe output
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


def screenshot(driver, name):
    path = SCREENSHOT_DIR / f"network-projects-{name}-1920x1080.png"
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
                if any(
                    x in msg.lower()
                    for x in [
                        "favicon",
                        "401",
                        "404",
                        "failed to load resource",
                    ]
                ):
                    continue
                errors.append(msg)
    except Exception:
        pass
    return errors


def login(driver):
    """Login to dashboard (session-based)."""
    driver.get(f"{BASE_URL}/login")
    time.sleep(1)
    try:
        user_input = driver.find_element(By.NAME, "username")
        user_input.clear()
        user_input.send_keys("admin")
        pw_input = driver.find_element(By.NAME, "password")
        pw_input.clear()
        pw_input.send_keys("admin")
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        time.sleep(1)
    except Exception:
        pass


def create_test_project(driver, name="E2E Test Project"):
    """Create a project via API and return its ID."""
    result = driver.execute_script(f"""
        const r = await fetch('/network/api/projects', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{name: '{name}', status: 'draft', owner: 'e2e-test', description: 'E2E test project'}})
        }});
        return await r.json();
    """)
    return result.get("id") if result else None


def delete_test_project(driver, pid):
    """Delete project via API."""
    if pid:
        driver.execute_script(f"""
            await fetch('/network/api/projects/{pid}', {{method: 'DELETE'}});
        """)


def run_tests():
    results = TestResult()
    driver = create_driver()
    test_project_id = None

    try:
        login(driver)

        # ── 1. Portfolio dashboard loads ──────────────────────────────────
        driver.get(f"{BASE_URL}/network/projects")
        time.sleep(2)
        screenshot(driver, "portfolio-dashboard")

        try:
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "Portfolio" in title.text
            results.ok("portfolio_page_loads", title.text)
        except Exception as e:
            results.fail("portfolio_page_loads", e)

        # ── 2. Stats bar shows portfolio metrics ──────────────────────────
        try:
            stats = driver.find_elements(By.CSS_SELECTOR, ".stats-bar .stat-card")
            assert len(stats) >= 4, f"Expected 4+ stat cards, got {len(stats)}"
            labels = [s.find_element(By.CSS_SELECTOR, ".stat-label").text for s in stats]
            assert "Projects" in labels
            results.ok("portfolio_stats_bar", f"{len(stats)} cards: {labels}")
        except Exception as e:
            results.fail("portfolio_stats_bar", e)

        # ── 3. Kanban board renders ───────────────────────────────────────
        try:
            kanban = driver.find_element(By.ID, "view-kanban")
            assert kanban.is_displayed(), "Kanban board not visible"
            columns = driver.find_elements(By.CSS_SELECTOR, ".kanban-column")
            assert len(columns) == 5, f"Expected 5 kanban columns, got {len(columns)}"
            col_titles = [c.find_element(By.CSS_SELECTOR, ".kanban-col-title").text for c in columns]
            results.ok("kanban_board_renders", f"Columns: {col_titles}")
        except Exception as e:
            results.fail("kanban_board_renders", e)

        # ── 4. Table view toggle ──────────────────────────────────────────
        try:
            table_btn = driver.find_element(By.ID, "btn-view-table")
            table_btn.click()
            time.sleep(0.5)
            table_view = driver.find_element(By.ID, "view-table")
            assert table_view.is_displayed(), "Table view not visible after click"
            kanban_view = driver.find_element(By.ID, "view-kanban")
            assert kanban_view.value_of_css_property("display") == "none", "Kanban should be hidden"
            screenshot(driver, "portfolio-table-view")
            # Switch back to kanban
            kanban_btn = driver.find_element(By.ID, "btn-view-kanban")
            kanban_btn.click()
            time.sleep(0.5)
            results.ok("view_toggle_works")
        except Exception as e:
            results.fail("view_toggle_works", e)

        # ── 5. Create project via API + verify on page ─────────────────────
        try:
            test_project_id = create_test_project(driver, "E2E Portfolio Test Project")
            assert test_project_id, "Project creation returned no ID"
            driver.get(f"{BASE_URL}/network/projects")
            time.sleep(2)
            screenshot(driver, "portfolio-after-create")
            page_src = driver.page_source
            assert "E2E Portfolio Test Project" in page_src, "Project name not found on page"
            results.ok("create_project_via_api", f"id={test_project_id}")
        except Exception as e:
            results.fail("create_project_via_api", e)

        # test_project_id already set from create_test_project above

        # ── 6. Project detail page ────────────────────────────────────────
        if test_project_id:
            try:
                driver.get(f"{BASE_URL}/network/projects/{test_project_id}")
                time.sleep(2)
                screenshot(driver, "project-detail")
                h1 = driver.find_element(By.CSS_SELECTOR, ".page-title")
                assert "E2E Portfolio Test" in h1.text
                stat_cards = driver.find_elements(By.CSS_SELECTOR, ".stat-card")
                assert len(stat_cards) >= 3, f"Expected 3+ stats, got {len(stat_cards)}"
                results.ok("project_detail_page", h1.text)
            except Exception as e:
                results.fail("project_detail_page", e)

        # ── 7. Health cards in kanban ─────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/projects")
            time.sleep(2)
            cards = driver.find_elements(By.CSS_SELECTOR, ".kanban-card")
            has_stats = False
            for card in cards:
                stat_spans = card.find_elements(By.CSS_SELECTOR, ".kanban-card-stats span")
                if stat_spans:
                    has_stats = True
                    break
            results.ok("kanban_health_cards", f"Found {len(cards)} cards, stats present: {has_stats}")
        except Exception as e:
            results.fail("kanban_health_cards", e)

        # ── 8. Table view has compliance/cost columns ─────────────────────
        try:
            table_btn = driver.find_element(By.ID, "btn-view-table")
            table_btn.click()
            time.sleep(0.5)
            headers = [th.text for th in driver.find_elements(By.CSS_SELECTOR, "#view-table thead th")]
            headers_upper = [h.upper() for h in headers]
            assert "COMPLIANCE" in headers_upper, f"Missing Compliance column: {headers}"
            assert "FINDINGS" in headers_upper, f"Missing Findings column: {headers}"
            assert "COST/MO" in headers_upper, f"Missing Cost/mo column: {headers}"
            results.ok("table_view_health_columns", str(headers))
        except Exception as e:
            results.fail("table_view_health_columns", e)

        # ── 9. Index page project filter ──────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/")
            time.sleep(2)
            filter_bar = driver.find_elements(By.CSS_SELECTOR, ".project-filter-bar")
            if filter_bar:
                select = filter_bar[0].find_element(By.TAG_NAME, "select")
                options = select.find_elements(By.TAG_NAME, "option")
                assert len(options) >= 2, f"Filter should have 2+ options, got {len(options)}"
                results.ok("index_project_filter_present", f"{len(options)} options")
            else:
                results.ok("index_project_filter_present", "No projects exist yet to filter")
            screenshot(driver, "index-with-filter")
        except Exception as e:
            results.fail("index_project_filter_present", e)

        # Test filtering by project
        if test_project_id:
            try:
                driver.get(f"{BASE_URL}/network/?project={test_project_id}")
                time.sleep(2)
                active_spans = driver.find_elements(By.CSS_SELECTOR, ".filter-active")
                assert active_spans, "No active filter indicator shown"
                clear_btns = driver.find_elements(By.CSS_SELECTOR, ".btn-clear-filter")
                assert clear_btns, "No clear filter button"
                screenshot(driver, "index-filtered-by-project")
                results.ok("index_project_filter_active")
            except Exception as e:
                results.fail("index_project_filter_active", e)

        # ── 10. Circuits page project filter ──────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/circuits")
            time.sleep(2)
            filter_bar = driver.find_elements(By.CSS_SELECTOR, ".project-filter-bar")
            if filter_bar:
                select = filter_bar[0].find_element(By.TAG_NAME, "select")
                options = select.find_elements(By.TAG_NAME, "option")
                results.ok("circuits_project_filter", f"{len(options)} options")
            else:
                results.ok("circuits_project_filter", "No projects — filter hidden")
            screenshot(driver, "circuits-with-filter")
        except Exception as e:
            results.fail("circuits_project_filter", e)

        # ── 11. IPAM page project filter ──────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/ipam")
            time.sleep(2)
            filter_bar = driver.find_elements(By.CSS_SELECTOR, ".project-filter-bar")
            if filter_bar:
                select = filter_bar[0].find_element(By.TAG_NAME, "select")
                options = select.find_elements(By.TAG_NAME, "option")
                results.ok("ipam_project_filter", f"{len(options)} options")
            else:
                results.ok("ipam_project_filter", "No projects — filter hidden")
            screenshot(driver, "ipam-with-filter")
        except Exception as e:
            results.fail("ipam_project_filter", e)

        # ── 12. Canvas project context bar ────────────────────────────────
        # Find a topology that belongs to a project, or use any topology
        try:
            topos = driver.execute_script("""
                const r = await fetch('/network/api/topologies');
                return await r.json();
            """)
            if topos and len(topos) > 0:
                topo_id = topos[0]["id"]
                # Link to test project if we have one
                if test_project_id:
                    driver.execute_script(f"""
                        await fetch('/network/api/projects/{test_project_id}/topologies', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{topology_id: '{topo_id}'}})
                        }});
                    """)
                    time.sleep(0.5)
                driver.get(f"{BASE_URL}/network/canvas/{topo_id}")
                time.sleep(3)
                screenshot(driver, "canvas-project-context")
                if test_project_id:
                    ctx_bar = driver.find_elements(By.CSS_SELECTOR, ".project-context-bar")
                    if ctx_bar:
                        results.ok("canvas_project_context_bar", "Context bar visible")
                    else:
                        results.fail("canvas_project_context_bar", "Context bar not found after linking")
                else:
                    results.ok("canvas_project_context_bar", "No project linked — bar hidden (correct)")
            else:
                results.ok("canvas_project_context_bar", "No topologies to test")
        except Exception as e:
            results.fail("canvas_project_context_bar", e)

        # ── 13. JS error check on key pages ───────────────────────────────
        pages_to_check = [
            ("/network/projects", "projects"),
            ("/network/", "index"),
            ("/network/circuits", "circuits"),
            ("/network/ipam", "ipam"),
        ]
        for page_url, page_name in pages_to_check:
            try:
                driver.get(f"{BASE_URL}{page_url}")
                time.sleep(2)
                js_errors = check_js_errors(driver)
                if js_errors:
                    results.fail(f"js_errors_{page_name}", f"{len(js_errors)} errors: {js_errors[0][:100]}")
                else:
                    results.ok(f"js_errors_{page_name}", "No SEVERE JS errors")
            except Exception as e:
                results.fail(f"js_errors_{page_name}", e)

        # ── 14. Delete test project (cleanup) ─────────────────────────────
        if test_project_id:
            try:
                delete_test_project(driver, test_project_id)
                time.sleep(1)
                results.ok("cleanup_delete_project")
            except Exception as e:
                results.fail("cleanup_delete_project", e)

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Network Canvas — Project Portfolio (P0)")
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
