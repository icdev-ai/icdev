"""
E2E Selenium test — Network Canvas Phase A (Review Board Pipeline + SAFe Bridge).

Tests:
1.  Review boards API returns ARB/ERB/CCB
2.  Initialize project phases (4 phases)
3.  Submit review to ARB
4.  Review package auto-generated with compliance/BOM data
5.  Approve review decision
6.  Reject review decision
7.  ROI calculator: save and compute NPV/payback
8.  Pipeline API returns full view
9.  Project detail: pipeline section renders
10. Project detail: phase progress bar
11. Project detail: board cards with review status
12. Project detail: ROI summary display
13. Submit Review modal present
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
    path = SCREENSHOT_DIR / f"network-phaseA-{name}-1920x1080.png"
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

        # Setup: create project + link topology
        d = api(
            driver,
            "POST",
            "/network/api/projects",
            {"name": "E2E PhaseA Pipeline Project", "status": "draft", "owner": "e2e-test"},
        )
        pid = d.get("id")
        topos = api(driver, "GET", "/network/api/topologies")
        topo_id = None
        if topos:
            topo_id = topos[0]["id"]
            api(driver, "POST", f"/network/api/projects/{pid}/topologies", {"topology_id": topo_id})

        # ── 1. Review boards API ──────────────────────────────────────────
        try:
            boards = api(driver, "GET", "/network/api/review-boards")
            assert len(boards) >= 3, f"Expected 3+ boards, got {len(boards)}"
            names = [b["short_name"] for b in boards]
            assert "ARB" in names and "ERB" in names and "CCB" in names
            results.ok("review_boards_api", str(names))
        except Exception as e:
            results.fail("review_boards_api", e)

        # ── 2. Initialize phases ──────────────────────────────────────────
        try:
            r = api(driver, "POST", f"/network/api/projects/{pid}/init-phases")
            assert r.get("ok") or r.get("phases"), f"Init failed: {r}"
            results.ok("init_phases")
        except Exception as e:
            results.fail("init_phases", e)

        # ── 3. Submit ARB review ──────────────────────────────────────────
        arb_review_id = None
        try:
            r = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/reviews",
                {
                    "board_id": "board-arb",
                    "phase": 1,
                    "reviewers": ["architect@dod.mil", "isso@dod.mil"],
                    "scheduled_date": "2026-04-01",
                },
            )
            arb_review_id = r.get("id")
            assert arb_review_id, f"No review ID: {r}"
            results.ok("submit_arb_review", f"id={arb_review_id}")
        except Exception as e:
            results.fail("submit_arb_review", e)

        # ── 4. Review package auto-generated ──────────────────────────────
        try:
            assert r.get("package"), "No package in review response"
            pkg = r["package"]
            assert "compliance_pct" in pkg
            assert "total_capex" in pkg
            assert "total_devices" in pkg
            assert "generated_at" in pkg
            results.ok(
                "review_package_generated", f"devices={pkg['total_devices']}, compliance={pkg['compliance_pct']}"
            )
        except Exception as e:
            results.fail("review_package_generated", e)

        # ── 5. Approve review ─────────────────────────────────────────────
        if arb_review_id:
            try:
                r = api(
                    driver,
                    "POST",
                    f"/network/api/reviews/{arb_review_id}/decide",
                    {"decision": "approved", "notes": "Architecture meets standards"},
                )
                assert r.get("ok") and r.get("decision") == "approved"
                results.ok("approve_review")
            except Exception as e:
                results.fail("approve_review", e)

        # ── 6. Submit + reject ERB review ─────────────────────────────────
        try:
            r = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/reviews",
                {"board_id": "board-erb", "phase": 2, "reviewers": ["engineer@dod.mil"]},
            )
            erb_id = r.get("id")
            r2 = api(
                driver,
                "POST",
                f"/network/api/reviews/{erb_id}/decide",
                {"decision": "rejected", "notes": "BOM needs vendor quotes"},
            )
            assert r2.get("decision") == "rejected"
            results.ok("reject_review")
        except Exception as e:
            results.fail("reject_review", e)

        # ── 7. ROI calculator ─────────────────────────────────────────────
        try:
            r = api(
                driver,
                "PUT",
                f"/network/api/projects/{pid}/roi",
                {
                    "capex": 250000,
                    "opex_annual": 36000,
                    "savings_annual": 120000,
                    "justification": "Replace aging MPLS with SD-WAN",
                    "alternatives": "Maintain current MPLS; Evaluate SASE",
                },
            )
            roi = r.get("roi", {})
            assert roi.get("payback_months") > 0, f"No payback: {roi}"
            assert roi.get("npv_5yr") != 0, f"Zero NPV: {roi}"
            results.ok("roi_calculator", f"payback={roi['payback_months']}mo, NPV=${roi['npv_5yr']}")
        except Exception as e:
            results.fail("roi_calculator", e)

        # ── 8. Pipeline API ───────────────────────────────────────────────
        try:
            r = api(driver, "GET", f"/network/api/projects/{pid}/pipeline")
            assert r.get("boards"), "No boards in pipeline"
            assert r.get("reviews"), "No reviews in pipeline"
            assert r.get("phases"), "No phases in pipeline"
            assert r.get("bridge"), "No SAFe bridge in pipeline"
            results.ok(
                "pipeline_api", f"boards={len(r['boards'])}, reviews={len(r['reviews'])}, phases={len(r['phases'])}"
            )
        except Exception as e:
            results.fail("pipeline_api", e)

        # ── 9. Project detail: pipeline section ───────────────────────────
        driver.get(f"{BASE_URL}/network/projects/{pid}")
        time.sleep(3)
        screenshot(driver, "project-detail-pipeline")

        try:
            pipeline = driver.find_element(By.ID, "pipeline-section")
            assert pipeline, "Pipeline section not found"
            results.ok("detail_pipeline_section")
        except Exception as e:
            results.fail("detail_pipeline_section", e)

        # ── 10. Phase progress bar ────────────────────────────────────────
        try:
            steps = driver.find_elements(By.CSS_SELECTOR, ".phase-step")
            assert len(steps) == 4, f"Expected 4 phases, got {len(steps)}"
            results.ok("detail_phase_progress", f"{len(steps)} phases")
        except Exception as e:
            results.fail("detail_phase_progress", e)

        # ── 11. Board cards with reviews ──────────────────────────────────
        try:
            board_cards = driver.find_elements(By.CSS_SELECTOR, ".board-card")
            assert len(board_cards) >= 3, f"Expected 3+ board cards, got {len(board_cards)}"
            review_items = driver.find_elements(By.CSS_SELECTOR, ".board-review-item")
            assert len(review_items) >= 2, f"Expected 2+ review items, got {len(review_items)}"
            results.ok("detail_board_cards", f"{len(board_cards)} boards, {len(review_items)} reviews")
        except Exception as e:
            results.fail("detail_board_cards", e)

        # ── 12. ROI summary ───────────────────────────────────────────────
        try:
            roi_el = driver.find_element(By.ID, "roi-summary")
            assert roi_el, "ROI summary not found"
            roi_text = roi_el.text
            assert "Payback" in roi_text
            assert "NPV" in roi_text
            results.ok("detail_roi_summary")
        except Exception as e:
            results.fail("detail_roi_summary", e)

        # ── 13. Submit Review modal ───────────────────────────────────────
        try:
            modal = driver.find_element(By.ID, "review-modal")
            assert modal, "Review modal not in DOM"
            results.ok("review_modal_present")
        except Exception as e:
            results.fail("review_modal_present", e)

        # ── 14. JS errors ─────────────────────────────────────────────────
        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("js_errors_pipeline", f"{len(js_errs)}: {js_errs[0][:100]}")
        else:
            results.ok("js_errors_pipeline", "No SEVERE JS errors")

        # ── Cleanup ───────────────────────────────────────────────────────
        if pid:
            api(driver, "DELETE", f"/network/api/projects/{pid}")
        results.ok("cleanup")

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Network Canvas — Phase A (Review Board Pipeline + SAFe Bridge)")
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
