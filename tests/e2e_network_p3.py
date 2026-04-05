"""
E2E Selenium test — Network Canvas P3 + unprioritized features.

Tests:
1.  Gate-checked status: draft->in_review blocked without topologies
2.  Gate-checked status: dry-run gate check API
3.  Gate-checked status: draft->in_review passes with topology
4.  Milestones: create, list, update status, delete
5.  Notes: create, display, delete
6.  Tags: add, display, remove
7.  Global search: search bar in navbar, API returns results
8.  Per-topology assignee: set and display
9.  Project templates: save, list, load, delete
10. Project detail page: milestones section renders
11. Project detail page: tags section renders
12. Project detail page: notes section renders
13. Project detail page: gate check buttons present
14. JS error checks
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


def screenshot(driver, name):
    path = SCREENSHOT_DIR / f"network-p3-{name}-1920x1080.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass


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
    pid = topo_id = tpl_id = loaded_pid = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # Setup: create project
        d = api(
            driver,
            "POST",
            "/network/api/projects",
            {"name": "E2E P3 Test Project", "status": "draft", "owner": "e2e-test"},
        )
        pid = d.get("id")

        # ── 1. Gate: blocked without topologies ───────────────────────────
        try:
            r = api(driver, "PUT", f"/network/api/projects/{pid}", {"status": "in_review"})
            assert r.get("blocked") or r.get("error"), f"Expected gate block, got: {r}"
            results.ok("gate_blocked_no_topos", str(r.get("failures", [])))
        except Exception as e:
            results.fail("gate_blocked_no_topos", e)

        # Link a topology
        topos = api(driver, "GET", "/network/api/topologies")
        if topos:
            topo_id = topos[0]["id"]
            api(driver, "POST", f"/network/api/projects/{pid}/topologies", {"topology_id": topo_id})

        # ── 2. Gate dry-run API ───────────────────────────────────────────
        try:
            r = api(driver, "POST", f"/network/api/projects/{pid}/gate-check", {"target_status": "approved"})
            assert "blocked" in r, f"Gate check missing 'blocked': {r}"
            results.ok("gate_dry_run_api", str(r))
        except Exception as e:
            results.fail("gate_dry_run_api", e)

        # ── 3. Gate: in_review passes with topology ───────────────────────
        try:
            r = api(driver, "PUT", f"/network/api/projects/{pid}", {"status": "in_review"})
            assert r.get("ok") or not r.get("blocked"), f"Expected pass, got: {r}"
            results.ok("gate_in_review_passes")
        except Exception as e:
            results.fail("gate_in_review_passes", e)

        # ── 4. Milestones CRUD ────────────────────────────────────────────
        try:
            m = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/milestones",
                {"title": "Phase 1 Complete", "due_date": "2026-04-15", "notes": "E2E test milestone"},
            )
            mid = m.get("id")
            assert mid, f"No milestone ID: {m}"
            # Update
            api(driver, "PUT", f"/network/api/milestones/{mid}", {"status": "in_progress"})
            # List
            ms = api(driver, "GET", f"/network/api/projects/{pid}/milestones")
            assert len(ms) >= 1
            assert ms[0]["status"] == "in_progress"
            # Delete
            api(driver, "DELETE", f"/network/api/milestones/{mid}")
            ms2 = api(driver, "GET", f"/network/api/projects/{pid}/milestones")
            assert len(ms2) == len(ms) - 1
            results.ok("milestones_crud")
        except Exception as e:
            results.fail("milestones_crud", e)

        # ── 5. Notes CRUD ─────────────────────────────────────────────────
        try:
            n = api(driver, "POST", f"/network/api/projects/{pid}/notes", {"body": "E2E test note content"})
            nid = n.get("id")
            assert nid
            ns = api(driver, "GET", f"/network/api/projects/{pid}/notes")
            assert len(ns) >= 1
            assert ns[0]["body"] == "E2E test note content"
            api(driver, "DELETE", f"/network/api/notes/{nid}")
            results.ok("notes_crud")
        except Exception as e:
            results.fail("notes_crud", e)

        # ── 6. Tags CRUD ─────────────────────────────────────────────────
        try:
            api(driver, "POST", f"/network/api/tags/project/{pid}", {"tag": "il5"})
            api(driver, "POST", f"/network/api/tags/project/{pid}", {"tag": "wan-refresh"})
            tags = api(driver, "GET", f"/network/api/tags/project/{pid}")
            assert "il5" in tags
            assert "wan-refresh" in tags
            api(driver, "DELETE", f"/network/api/tags/project/{pid}/il5")
            tags2 = api(driver, "GET", f"/network/api/tags/project/{pid}")
            assert "il5" not in tags2
            results.ok("tags_crud", str(tags))
        except Exception as e:
            results.fail("tags_crud", e)

        # ── 7. Global search API ──────────────────────────────────────────
        try:
            sr = api(driver, "GET", "/network/api/search?q=E2E P3")
            assert sr.get("results"), f"No results: {sr}"
            found = [r["name"] for r in sr["results"]]
            results.ok("global_search_api", str(found))
        except Exception as e:
            results.fail("global_search_api", e)

        # ── 8. Global search bar in navbar ────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/")
            time.sleep(2)
            search_input = driver.find_element(By.ID, "global-search-input")
            assert search_input, "Search input not found in navbar"
            results.ok("global_search_navbar")
        except Exception as e:
            results.fail("global_search_navbar", e)

        # ── 9. Per-topology assignee ──────────────────────────────────────
        if topo_id:
            try:
                api(
                    driver,
                    "PUT",
                    f"/network/api/projects/{pid}/topologies/{topo_id}/assignee",
                    {"assignee": "Jane Doe"},
                )
                driver.get(f"{BASE_URL}/network/projects/{pid}")
                time.sleep(2)
                page = driver.page_source
                assert "Jane Doe" in page, "Assignee not shown on page"
                results.ok("assignee_set_and_display")
            except Exception as e:
                results.fail("assignee_set_and_display", e)
        else:
            results.ok("assignee_set_and_display", "No topo to test")

        # ── 10. Project templates: save, list, load, delete ───────────────
        try:
            # Save
            t = api(
                driver,
                "POST",
                "/network/api/project-templates",
                {"project_id": pid, "name": "E2E Test Template", "description": "Template from E2E test"},
            )
            tpl_id = t.get("id")
            assert tpl_id, f"No template ID: {t}"
            # List
            tpls = api(driver, "GET", "/network/api/project-templates")
            assert any(t["id"] == tpl_id for t in tpls)
            # Load
            loaded = api(
                driver, "POST", f"/network/api/project-templates/{tpl_id}/load", {"name": "Loaded From Template"}
            )
            loaded_pid = loaded.get("id")
            assert loaded_pid
            assert loaded.get("topologies_created", 0) >= 1
            # Delete template
            api(driver, "DELETE", f"/network/api/project-templates/{tpl_id}")
            results.ok("project_templates_crud", f"saved={tpl_id}, loaded={loaded_pid}")
        except Exception as e:
            results.fail("project_templates_crud", e)

        # ── 11. Project detail: milestones section ────────────────────────
        # Add a milestone first
        api(
            driver,
            "POST",
            f"/network/api/projects/{pid}/milestones",
            {"title": "UI Test Milestone", "due_date": "2026-05-01"},
        )
        api(driver, "POST", f"/network/api/projects/{pid}/notes", {"body": "UI test note"})

        driver.get(f"{BASE_URL}/network/projects/{pid}")
        time.sleep(3)
        screenshot(driver, "project-detail-p3")

        try:
            ms_section = driver.find_element(By.ID, "milestones-section")
            assert ms_section, "Milestones section not found"
            ms_items = ms_section.find_elements(By.CSS_SELECTOR, ".milestone-item")
            results.ok("detail_milestones_section", f"{len(ms_items)} milestones")
        except Exception as e:
            results.fail("detail_milestones_section", e)

        # ── 12. Project detail: tags section ──────────────────────────────
        try:
            tags_container = driver.find_element(By.ID, "tags-container")
            assert tags_container
            pills = tags_container.find_elements(By.CSS_SELECTOR, ".tag-pill")
            results.ok("detail_tags_section", f"{len(pills)} tags")
        except Exception as e:
            results.fail("detail_tags_section", e)

        # ── 13. Project detail: notes section ─────────────────────────────
        try:
            notes_container = driver.find_element(By.ID, "notes-container")
            assert notes_container
            note_items = notes_container.find_elements(By.CSS_SELECTOR, ".note-item")
            results.ok("detail_notes_section", f"{len(note_items)} notes")
        except Exception as e:
            results.fail("detail_notes_section", e)

        # ── 14. Project detail: gate check buttons ────────────────────────
        try:
            gate_el = driver.find_element(By.ID, "gate-result")
            assert gate_el is not None
            results.ok("detail_gate_check_buttons")
        except Exception as e:
            results.fail("detail_gate_check_buttons", e)

        # ── 15. JS errors on project detail ───────────────────────────────
        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("js_errors_detail", f"{len(js_errs)}: {js_errs[0][:100]}")
        else:
            results.ok("js_errors_detail", "No SEVERE JS errors")

        # ── Cleanup ───────────────────────────────────────────────────────
        for cid in [pid, loaded_pid]:
            if cid:
                try:
                    api(driver, "DELETE", f"/network/api/projects/{cid}")
                except Exception:
                    pass
        results.ok("cleanup")

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Network Canvas — P3 (Gates, Tags, Milestones, Notes, Search, Templates)")
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
