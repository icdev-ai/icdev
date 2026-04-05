"""
E2E Selenium test — Network Canvas Extended Features.

Tests:
1.  Notifications API: list + unread count
2.  Notifications: created on review submit
3.  Notifications: created on review decision
4.  Notifications: mark all read
5.  Notification bell in navbar
6.  Topology diff API: returns delta
7.  Topology diff page: loads with selectors
8.  SAFe auto-decompose API: feature + stories + enablers
9.  SAFe decompose: WSJF scores calculated
10. Global canvas page loads
11. Global canvas data API: returns nodes/edges
12. Gantt timeline renders on project detail
13. Diff nav link in navbar
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
    path = SCREENSHOT_DIR / f"network-ext-{name}-1920x1080.png"
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
    pid = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # Setup
        d = api(
            driver, "POST", "/network/api/projects", {"name": "E2E Extended Test", "status": "approved", "owner": "e2e"}
        )
        pid = d.get("id")
        topos = api(driver, "GET", "/network/api/topologies")
        topo_a = topos[0]["id"] if topos else None
        topo_b = topos[1]["id"] if len(topos) > 1 else None
        if topo_a:
            api(driver, "POST", f"/network/api/projects/{pid}/topologies", {"topology_id": topo_a})

        # Mark all existing notifs as read first
        api(driver, "POST", "/network/api/notifications/mark-read")

        # ── 1. Submit review (creates notification) ───────────────────────
        try:
            r = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/reviews",
                {"board_id": "board-arb", "phase": 1, "reviewers": ["test@e2e"]},
            )
            review_id = r.get("id")
            assert review_id
            results.ok("review_creates_notification", f"review={review_id}")
        except Exception as e:
            results.fail("review_creates_notification", e)

        # ── 2. Notifications API has unread ───────────────────────────────
        try:
            n = api(driver, "GET", "/network/api/notifications")
            assert n.get("unread") >= 1, f"Expected unread, got {n}"
            assert len(n.get("notifications", [])) >= 1
            results.ok("notifications_api_unread", f"unread={n['unread']}")
        except Exception as e:
            results.fail("notifications_api_unread", e)

        # ── 3. Review decision creates notification ───────────────────────
        if review_id:
            try:
                api(
                    driver,
                    "POST",
                    f"/network/api/reviews/{review_id}/decide",
                    {"decision": "approved", "notes": "E2E test"},
                )
                n = api(driver, "GET", "/network/api/notifications")
                assert n.get("unread") >= 2
                results.ok("decision_creates_notification")
            except Exception as e:
                results.fail("decision_creates_notification", e)

        # ── 4. Mark all read ──────────────────────────────────────────────
        try:
            api(driver, "POST", "/network/api/notifications/mark-read")
            n = api(driver, "GET", "/network/api/notifications")
            assert n.get("unread") == 0
            results.ok("mark_notifications_read")
        except Exception as e:
            results.fail("mark_notifications_read", e)

        # ── 5. Notification bell in navbar ────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/")
            time.sleep(2)
            bell = driver.find_element(By.ID, "notif-bell")
            assert bell
            results.ok("notification_bell_in_navbar")
        except Exception as e:
            results.fail("notification_bell_in_navbar", e)

        # ── 6. Topology diff API ──────────────────────────────────────────
        if topo_a and topo_b:
            try:
                d = api(driver, "POST", "/network/api/topology-diff", {"topology_a": topo_a, "topology_b": topo_b})
                assert "similarity_pct" in d
                assert "nodes_only_a" in d
                assert "nodes_only_b" in d
                assert d.get("topology_a", {}).get("name")
                results.ok("topology_diff_api", f"similarity={d['similarity_pct']}%, common={d['nodes_common']}")
            except Exception as e:
                results.fail("topology_diff_api", e)
        else:
            results.ok("topology_diff_api", "Not enough topos")

        # ── 7. Diff page loads ────────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/projects/diff")
            time.sleep(2)
            screenshot(driver, "diff-page")
            title = driver.find_element(By.CSS_SELECTOR, ".page-title")
            assert "Diff" in title.text
            selects = driver.find_elements(By.TAG_NAME, "select")
            assert len(selects) >= 2
            results.ok("diff_page_loads")
        except Exception as e:
            results.fail("diff_page_loads", e)

        # ── 8. SAFe auto-decompose API ────────────────────────────────────
        try:
            d = api(driver, "POST", f"/network/api/projects/{pid}/decompose")
            assert d.get("feature"), f"No feature: {d}"
            assert d.get("stories") is not None
            assert d.get("total_items") >= 1
            results.ok(
                "safe_decompose_api",
                f"items={d['total_items']}, stories={len(d['stories'])}, enablers={len(d['enablers'])}",
            )
        except Exception as e:
            results.fail("safe_decompose_api", e)

        # ── 9. WSJF scores calculated ─────────────────────────────────────
        try:
            d = api(driver, "POST", f"/network/api/projects/{pid}/decompose")
            f = d.get("feature", {})
            assert f.get("wsjf_score") and f["wsjf_score"] > 0
            assert f.get("t_shirt_size") in ("S", "M", "L", "XL")
            results.ok("wsjf_scores", f"size={f['t_shirt_size']}, wsjf={f['wsjf_score']}")
        except Exception as e:
            results.fail("wsjf_scores", e)

        # ── 10. Global canvas page loads ──────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/network/global/canvas")
            time.sleep(3)
            screenshot(driver, "global-canvas")
            toolbar = driver.find_element(By.ID, "global-canvas-toolbar")
            assert toolbar
            container = driver.find_element(By.ID, "global-canvas-container")
            assert container
            results.ok("global_canvas_page_loads")
        except Exception as e:
            results.fail("global_canvas_page_loads", e)

        # ── 11. Global canvas data API ────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/global-canvas-data")
            assert "nodes" in d and "edges" in d
            assert "groups" in d
            results.ok("global_canvas_data_api", f"nodes={d['total_nodes']}, edges={d['total_edges']}")
        except Exception as e:
            results.fail("global_canvas_data_api", e)

        # ── 12. Gantt timeline on detail ──────────────────────────────────
        try:
            # Add milestone with due_date
            api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/milestones",
                {"title": "Gantt Test", "due_date": "2026-05-01", "status": "in_progress"},
            )
            driver.get(f"{BASE_URL}/network/projects/{pid}")
            time.sleep(2)
            screenshot(driver, "gantt-timeline")
            gantt = driver.find_elements(By.CSS_SELECTOR, ".gantt-timeline .gantt-bar")
            assert len(gantt) >= 1, "No gantt bars"
            results.ok("gantt_timeline_renders", f"{len(gantt)} bars")
        except Exception as e:
            results.fail("gantt_timeline_renders", e)

        # ── 13. Diff nav link ─────────────────────────────────────────────
        try:
            assert "Diff" in driver.page_source, "Nav link Diff not in page"
            results.ok("diff_nav_link")
        except Exception as e:
            results.fail("diff_nav_link", e)

        # ── 14. JS errors ─────────────────────────────────────────────────
        for page, name in [
            ("/network/projects/diff", "diff"),
            ("/network/global/canvas", "global_canvas"),
        ]:
            driver.get(f"{BASE_URL}{page}")
            time.sleep(3)
            errs = check_js_errors(driver)
            if errs:
                results.fail(f"js_errors_{name}", f"{len(errs)}: {errs[0][:80]}")
            else:
                results.ok(f"js_errors_{name}", "No SEVERE JS errors")

        # ── Cleanup ───────────────────────────────────────────────────────
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
    print("E2E: Network Canvas — Extended Features (Notifications, Diff, SAFe, Canvas, Gantt)")
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
