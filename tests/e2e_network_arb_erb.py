"""
E2E Selenium test — ARB/ERB Documentation (Architect Workbench).

Tests:
1.  Alternatives: add criteria + options with weighted scoring
2.  Alternatives: weighted total auto-computed
3.  Risk Register: CRUD with probability x impact scoring
4.  Risk Register: score auto-computed
5.  Enhanced BOM: CRUD with extended cost calculation
6.  Enhanced BOM: totals by category
7.  Lab Test Results: CRUD with measurements
8.  Migration Plan: CRUD with phases and dependencies
9.  Capacity Projections: auto-compute growth
10. Standards Alignment: CRUD
11. Resource Plan: CRUD with labor cost totals
12. Business Case Generator: pulls all data together
13. Milestone dependencies (predecessor_id)
14. Clear All Topologies regression test
15. Clear All Simulations regression test
16. JS error checks
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

        # Setup: create project + topology for testing
        d = api(
            driver, "POST", "/network/api/projects", {"name": "E2E ARB/ERB Test", "status": "draft", "owner": "e2e"}
        )
        pid = d.get("id")

        # Create a topology for clear-all regression test
        topo_d = api(
            driver,
            "POST",
            "/network/api/topologies",
            {
                "name": "E2E ClearAll Test Topo",
                "graph_json": {"nodes": [{"id": "n1", "label": "R1", "type": "router", "x": 0, "y": 0}], "edges": []},
            },
        )
        topo_d.get("id")

        # ── 1. Alternatives: criteria + options ───────────────────────────
        try:
            api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/alternatives/criteria",
                {"name": "Cost", "weight_pct": 30, "sort_order": 1},
            )
            api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/alternatives/criteria",
                {"name": "Performance", "weight_pct": 25, "sort_order": 2},
            )
            api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/alternatives/criteria",
                {"name": "Redundancy", "weight_pct": 20, "sort_order": 3},
            )
            r = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/alternatives/options",
                {
                    "option_name": "SD-WAN",
                    "description": "Software-defined WAN",
                    "is_recommended": True,
                    "scores": {"Cost": {"score": 7}, "Performance": {"score": 8}, "Redundancy": {"score": 9}},
                },
            )
            assert r.get("id")
            api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/alternatives/options",
                {
                    "option_name": "MPLS",
                    "description": "Traditional MPLS",
                    "scores": {"Cost": {"score": 4}, "Performance": {"score": 9}, "Redundancy": {"score": 7}},
                },
            )
            results.ok("alternatives_create")
        except Exception as e:
            results.fail("alternatives_create", e)

        # ── 2. Weighted total ─────────────────────────────────────────────
        try:
            d = api(driver, "GET", f"/network/api/projects/{pid}/alternatives")
            opts = d.get("options", [])
            assert len(opts) >= 2
            sdwan = next(o for o in opts if o["option_name"] == "SD-WAN")
            assert sdwan["total_score"] > 0
            results.ok("alternatives_weighted_score", f"SD-WAN={sdwan['total_score']}")
        except Exception as e:
            results.fail("alternatives_weighted_score", e)

        # ── 3. Risk Register CRUD ─────────────────────────────────────────
        try:
            r = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/risks",
                {
                    "title": "Circuit lead time delay",
                    "category": "schedule",
                    "probability": "high",
                    "impact": "high",
                    "mitigation": "Order early",
                    "owner": "PM",
                },
            )
            risk_id = r.get("id")
            assert risk_id
            api(driver, "PUT", f"/network/api/risks/{risk_id}", {"status": "mitigated"})
            results.ok("risk_register_crud")
        except Exception as e:
            results.fail("risk_register_crud", e)

        # ── 4. Risk score auto-computed ───────────────────────────────────
        try:
            risks = api(driver, "GET", f"/network/api/projects/{pid}/risks")
            assert len(risks) >= 1
            r = risks[0]
            assert r.get("risk_score") == 9  # high(3) * high(3)
            results.ok("risk_score_computed", f"score={r['risk_score']}")
        except Exception as e:
            results.fail("risk_score_computed", e)

        # ── 5. Enhanced BOM CRUD ──────────────────────────────────────────
        try:
            r = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/bom-items",
                {
                    "category": "hardware",
                    "vendor": "Cisco",
                    "model": "ASR1001-X",
                    "part_number": "ASR1001-X=",
                    "quantity": 2,
                    "unit_cost": 15000,
                    "annual_maint": 3000,
                    "lead_time_days": 30,
                    "contract_vehicle": "GSA",
                },
            )
            bom_id = r.get("id")
            assert bom_id
            results.ok("bom_item_create")
        except Exception as e:
            results.fail("bom_item_create", e)

        # ── 6. BOM totals ─────────────────────────────────────────────────
        try:
            d = api(driver, "GET", f"/network/api/projects/{pid}/bom-items")
            assert d.get("totals", {}).get("hardware") == 30000
            assert d["totals"]["annual_maint"] == 3000
            results.ok("bom_totals", f"hw=${d['totals']['hardware']}, maint=${d['totals']['annual_maint']}")
        except Exception as e:
            results.fail("bom_totals", e)

        # ── 7. Lab Test Results ───────────────────────────────────────────
        try:
            r = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/lab-tests",
                {
                    "test_name": "OSPF Convergence",
                    "category": "failover",
                    "methodology": "Kill primary link, measure convergence",
                    "result": "pass",
                    "measurements": {"convergence_ms": 450, "throughput_mbps": 9200},
                    "tested_by": "e2e-tester",
                },
            )
            assert r.get("id")
            tests = api(driver, "GET", f"/network/api/projects/{pid}/lab-tests")
            assert len(tests) >= 1
            results.ok("lab_tests_crud", f"result={tests[0]['result']}")
        except Exception as e:
            results.fail("lab_tests_crud", e)

        # ── 8. Migration Plan ─────────────────────────────────────────────
        try:
            r1 = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/migration-phases",
                {"phase_num": 1, "title": "Pre-staging", "duration_days": 5, "rollback_criteria": "N/A"},
            )
            r2 = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/migration-phases",
                {
                    "phase_num": 2,
                    "title": "Cutover",
                    "duration_days": 1,
                    "parallel_run": True,
                    "rollback_criteria": "Revert BGP to old path",
                    "dependencies": [r1.get("id")],
                },
            )
            assert r2.get("id")
            phases = api(driver, "GET", f"/network/api/projects/{pid}/migration-phases")
            assert len(phases) >= 2
            results.ok("migration_plan_crud", f"{len(phases)} phases")
        except Exception as e:
            results.fail("migration_plan_crud", e)

        # ── 9. Capacity Projections ───────────────────────────────────────
        try:
            r = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/capacity-projections",
                {"metric_name": "bandwidth_gbps", "current_value": 10, "growth_rate_pct": 25},
            )
            assert r.get("id")
            caps = api(driver, "GET", f"/network/api/projects/{pid}/capacity-projections")
            assert len(caps) >= 1
            c = caps[0]
            # 10 * 1.25 = 12.5 year 1
            assert float(c["year1_value"]) == 12.5
            # 10 * 1.25^5 ≈ 30.52
            assert float(c["year5_value"]) > 30
            results.ok("capacity_projections", f"y1={c['year1_value']}, y5={c['year5_value']}")
        except Exception as e:
            results.fail("capacity_projections", e)

        # ── 10. Standards Alignment ───────────────────────────────────────
        try:
            r = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/standards-checks",
                {
                    "standard": "Enterprise Reference Architecture",
                    "check_item": "Uses approved vendor list",
                    "status": "compliant",
                },
            )
            assert r.get("id")
            checks = api(driver, "GET", f"/network/api/projects/{pid}/standards-checks")
            assert len(checks) >= 1
            results.ok("standards_alignment")
        except Exception as e:
            results.fail("standards_alignment", e)

        # ── 11. Resource Plan ─────────────────────────────────────────────
        try:
            api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/resource-plan",
                {
                    "phase": "implementation",
                    "role": "Network Engineer",
                    "name": "Jane Doe",
                    "hours": 40,
                    "rate_per_hour": 150,
                },
            )
            api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/resource-plan",
                {
                    "phase": "implementation",
                    "role": "Security Analyst",
                    "name": "John Smith",
                    "hours": 16,
                    "rate_per_hour": 175,
                    "is_contractor": True,
                },
            )
            d = api(driver, "GET", f"/network/api/projects/{pid}/resource-plan")
            assert d.get("total_hours") == 56
            assert d.get("total_labor_cost") == 8800  # 40*150 + 16*175
            results.ok("resource_plan", f"hours={d['total_hours']}, cost=${d['total_labor_cost']}")
        except Exception as e:
            results.fail("resource_plan", e)

        # ── 12. Business Case Generator ───────────────────────────────────
        try:
            bc = api(driver, "GET", f"/network/api/projects/{pid}/business-case")
            assert bc.get("project", {}).get("name") == "E2E ARB/ERB Test"
            assert bc.get("alternatives_analysis", {}).get("options")
            assert bc.get("risk_register")
            assert bc.get("detailed_bom", {}).get("items")
            assert bc.get("capacity_projections")
            assert bc.get("migration_plan")
            assert bc.get("lab_test_results")
            assert bc.get("standards_alignment")
            assert bc.get("resource_plan", {}).get("resources")
            assert bc.get("generated_at")
            results.ok("business_case_generator", f"sections: {len([k for k in bc if bc[k]])}")
        except Exception as e:
            results.fail("business_case_generator", e)

        # ── 13. Milestone dependencies ────────────────────────────────────
        try:
            m1 = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/milestones",
                {"title": "Equipment Ordered", "due_date": "2026-04-01"},
            )
            m2 = api(
                driver,
                "POST",
                f"/network/api/projects/{pid}/milestones",
                {"title": "Equipment Received", "due_date": "2026-05-01"},
            )
            # Update m2 to depend on m1 (via PUT)
            api(
                driver, "PUT", f"/network/api/milestones/{m2['id']}", {"predecessor_id": m1["id"]}
            )  # uses existing update API
            ms = api(driver, "GET", f"/network/api/projects/{pid}/milestones")
            assert len(ms) >= 2
            results.ok("milestone_dependencies")
        except Exception as e:
            results.fail("milestone_dependencies", e)

        # ── 14. Export Markdown ────────────────────────────────────────────
        try:
            d = api(driver, "POST", f"/network/api/projects/{pid}/business-case/export", {"format": "markdown"})
            assert d.get("format") == "markdown"
            assert d.get("filename", "").endswith(".md")
            content = d.get("content", "")
            assert "# Business Case:" in content
            assert "## Risk Register" in content
            assert "## Bill of Materials" in content
            results.ok("export_markdown", f"{len(content)} chars")
        except Exception as e:
            results.fail("export_markdown", e)

        # ── 15. Export HTML ───────────────────────────────────────────────
        try:
            d = api(driver, "POST", f"/network/api/projects/{pid}/business-case/export", {"format": "html"})
            assert d.get("format") == "html"
            assert d.get("filename", "").endswith(".html")
            content = d.get("content", "")
            assert "<html>" in content
            assert "<table>" in content
            results.ok("export_html", f"{len(content)} chars")
        except Exception as e:
            results.fail("export_html", e)

        # ── 16. Export DOCX ───────────────────────────────────────────────
        try:
            d = api(driver, "POST", f"/network/api/projects/{pid}/business-case/export", {"format": "docx"})
            assert d.get("format") == "docx"
            assert d.get("filename", "").endswith(".docx")
            assert d.get("content_b64"), "No DOCX content"
            assert d.get("size_bytes", 0) > 1000, "DOCX too small"
            results.ok("export_docx", f"{d['size_bytes']} bytes")
        except Exception as e:
            results.fail("export_docx", e)

        # ── Clear All Topologies regression ───────────────────────────────
        try:
            d = api(driver, "DELETE", "/network/api/topologies/clear-all")
            assert d.get("cleared") == "topologies", f"Got: {d}"
            # Verify empty
            topos = api(driver, "GET", "/network/api/topologies")
            assert len(topos) == 0, f"Expected 0 topos, got {len(topos)}"
            results.ok("clear_all_topologies_regression")
        except Exception as e:
            results.fail("clear_all_topologies_regression", e)

        # ── 15. Clear All Simulations regression ──────────────────────────
        try:
            d = api(driver, "DELETE", "/network/api/simulations/clear-all")
            assert d.get("cleared") == "simulations"
            results.ok("clear_all_simulations_regression")
        except Exception as e:
            results.fail("clear_all_simulations_regression", e)

        # Restore a topology so other test suites don't break
        try:
            api(
                driver,
                "POST",
                "/network/api/topologies",
                {
                    "name": "Restored After ClearAll",
                    "graph_json": {
                        "nodes": [
                            {"id": "r1", "label": "Core-R1", "type": "router", "x": 100, "y": 100},
                            {"id": "r2", "label": "Core-R2", "type": "router", "x": 300, "y": 100},
                            {"id": "fw1", "label": "FW-1", "type": "firewall", "x": 200, "y": 250},
                        ],
                        "edges": [
                            {"id": "e1", "source": "r1", "target": "r2", "label": "OSPF", "protocol": "ospf"},
                            {"id": "e2", "source": "r1", "target": "fw1", "label": "10GbE", "protocol": ""},
                        ],
                    },
                },
            )
        except Exception:
            pass

        # ── 16. JS errors ─────────────────────────────────────────────────
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
    print("E2E: ARB/ERB Documentation (Architect Workbench)")
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
