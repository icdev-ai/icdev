"""
E2E Selenium test — Innovation Flywheel (Phase 7).

Tests:
1.  Submit innovation idea with auto-scoring
2.  Vote on idea
3.  Update idea status
4.  Add tech radar item
5.  Move tech radar ring (tracks moved_from)
6.  Tech radar by_ring grouping
7.  Add lesson learned
8.  List lessons by project
9.  Delete idea/radar/lesson
"""

import json
import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

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
    idea_id = radar_id = lesson_id = None

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # ── 1. Submit idea with auto-scoring ──────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/innovation-ideas",
                {
                    "title": "Replace MPLS with SD-WAN to reduce WAN cost 30%",
                    "description": "Migrate 50 branch sites from MPLS to SD-WAN over 18 months",
                    "category": "cost_reduction",
                    "submitted_by": "noc-engineer-1",
                    "impact_score": 8,
                    "feasibility_score": 7,
                    "cost_score": 6,
                },
            )
            idea_id = d.get("id")
            assert idea_id
            # Weighted: 8*0.4 + 7*0.35 + 6*0.25 = 3.2 + 2.45 + 1.5 = 7.15
            assert d.get("total_score") == 7.15, f"Score: {d.get('total_score')}"
            results.ok("submit_idea", f"id={idea_id}, score={d['total_score']}")
        except Exception as e:
            results.fail("submit_idea", e)

        # ── 2. Vote ───────────────────────────────────────────────────────
        if idea_id:
            try:
                api(driver, "POST", f"/network/api/innovation-ideas/{idea_id}/vote")
                api(driver, "POST", f"/network/api/innovation-ideas/{idea_id}/vote")
                ideas = api(driver, "GET", "/network/api/innovation-ideas")
                our = next(i for i in ideas if i["id"] == idea_id)
                assert our["votes"] >= 2
                results.ok("vote_idea", f"votes={our['votes']}")
            except Exception as e:
                results.fail("vote_idea", e)

        # ── 3. Update status ──────────────────────────────────────────────
        if idea_id:
            try:
                api(driver, "PUT", f"/network/api/innovation-ideas/{idea_id}", {"status": "approved"})
                ideas = api(driver, "GET", "/network/api/innovation-ideas")
                our = next(i for i in ideas if i["id"] == idea_id)
                assert our["status"] == "approved"
                results.ok("update_idea_status")
            except Exception as e:
                results.fail("update_idea_status", e)

        # ── 4. Add tech radar item ────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/tech-radar",
                {
                    "technology": "SD-WAN (Cisco vManage)",
                    "ring": "trial",
                    "category": "networking",
                    "description": "Evaluating for branch WAN replacement",
                },
            )
            radar_id = d.get("id")
            assert radar_id
            results.ok("add_tech_radar", f"id={radar_id}")
        except Exception as e:
            results.fail("add_tech_radar", e)

        # ── 5. Move ring (tracks moved_from) ──────────────────────────────
        if radar_id:
            try:
                api(driver, "PUT", f"/network/api/tech-radar/{radar_id}", {"ring": "adopt"})
                radar = api(driver, "GET", "/network/api/tech-radar")
                our = next(i for i in radar["items"] if i["id"] == radar_id)
                assert our["ring"] == "adopt"
                assert our.get("moved_from") == "trial"
                results.ok("move_radar_ring", f"trial -> {our['ring']}")
            except Exception as e:
                results.fail("move_radar_ring", e)

        # ── 6. by_ring grouping ───────────────────────────────────────────
        try:
            radar = api(driver, "GET", "/network/api/tech-radar")
            assert "by_ring" in radar
            assert "adopt" in radar["by_ring"]
            results.ok("tech_radar_by_ring", f"rings: {list(radar['by_ring'].keys())}")
        except Exception as e:
            results.fail("tech_radar_by_ring", e)

        # ── 7. Add lesson learned ─────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/lessons-learned",
                {
                    "title": "OSPF area 0 split caused 30-minute outage",
                    "category": "technical",
                    "what_happened": "ABR lost adjacency during maintenance",
                    "root_cause": "Single ABR between area 0 and area 1",
                    "lesson": "Always have 2+ ABRs between OSPF areas",
                    "recommendation": "Add second ABR before next maintenance",
                    "submitted_by": "network-engineer-2",
                },
            )
            lesson_id = d.get("id")
            assert lesson_id
            results.ok("add_lesson_learned", f"id={lesson_id}")
        except Exception as e:
            results.fail("add_lesson_learned", e)

        # ── 8. List lessons ───────────────────────────────────────────────
        try:
            d = api(driver, "GET", "/network/api/lessons-learned")
            assert len(d) >= 1
            assert d[0]["title"]
            results.ok("list_lessons", f"{len(d)} lessons")
        except Exception as e:
            results.fail("list_lessons", e)

        # ── 9. Delete all ─────────────────────────────────────────────────
        try:
            if idea_id:
                api(driver, "DELETE", f"/network/api/innovation-ideas/{idea_id}")
            if radar_id:
                api(driver, "DELETE", f"/network/api/tech-radar/{radar_id}")
            if lesson_id:
                api(driver, "DELETE", f"/network/api/lessons-learned/{lesson_id}")
            results.ok("delete_all")
        except Exception as e:
            results.fail("delete_all", e)

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Innovation Flywheel (Phase 7)")
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
