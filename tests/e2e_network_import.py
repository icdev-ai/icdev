"""
E2E Selenium test — Intelligent Import & Stitching (Phase 1 Network Intelligence).

Tests:
1.  Semantic import: DrawIO nodes auto-classified from labels
2.  Semantic import: router, firewall, switch correctly identified
3.  Bulk import: multiple files at once
4.  Bulk import: auto-group under project
5.  Stitch/merge: combine 2 topologies
6.  Stitch with interconnects
7.  Import-as-audit: import + compliance + classification
8.  Re-classify nodes API
9.  Clear All regression
10. JS error checks
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

# Sample DrawIO XML with recognizable device labels
SAMPLE_DRAWIO = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile><diagram name="Test">
<mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="r1" value="Core-Router-01" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="100" y="100" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="fw1" value="Firewall-DMZ" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="300" y="100" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="sw1" value="Dist-Switch-L3" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="100" y="250" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="srv1" value="App-Server-01" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="300" y="250" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="e1" value="10GbE" style="" edge="1" source="r1" target="fw1" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e2" value="LACP" style="" edge="1" source="r1" target="sw1" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel>
</diagram></mxfile>"""

SAMPLE_DRAWIO_2 = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile><diagram name="WAN">
<mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="wan-r" value="WAN-Router" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="100" y="100" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="isp" value="ISP-Cloud" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="300" y="100" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="e1" value="BGP" style="" edge="1" source="wan-r" target="isp" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel>
</diagram></mxfile>"""


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
    topo_ids = []

    try:
        login(driver)
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)

        # ── 1. Semantic import: DrawIO ────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/import",
                {"format": "drawio", "content": SAMPLE_DRAWIO, "name": "E2E Semantic Import Test"},
            )
            assert d.get("id"), f"No id: {d}"
            topo_ids.append(d["id"])
            assert d["nodes"] == 4
            assert d["edges"] == 2
            results.ok("semantic_import_drawio", f"id={d['id']}, {d['nodes']}N/{d['edges']}E")
        except Exception as e:
            results.fail("semantic_import_drawio", e)

        # ── 2. Verify classification ──────────────────────────────────────
        if topo_ids:
            try:
                t = api(driver, "GET", f"/network/api/topologies/{topo_ids[0]}")
                gj = t.get("graph_json", "{}")
                graph = json.loads(gj) if isinstance(gj, str) else gj
                types = {n["label"]: n["type"] for n in graph["nodes"]}
                assert types.get("Core-Router-01") == "router", f"Router: {types.get('Core-Router-01')}"
                assert types.get("Firewall-DMZ") == "firewall", f"FW: {types.get('Firewall-DMZ')}"
                assert types.get("Dist-Switch-L3") == "switch-l3", f"SW: {types.get('Dist-Switch-L3')}"
                assert types.get("App-Server-01") == "server", f"Srv: {types.get('App-Server-01')}"
                results.ok("classification_correct", str(types))
            except Exception as e:
                results.fail("classification_correct", e)

        # ── 3. Bulk import ────────────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/import/bulk",
                {
                    "files": [
                        {"format": "drawio", "content": SAMPLE_DRAWIO, "name": "Bulk Import A"},
                        {"format": "drawio", "content": SAMPLE_DRAWIO_2, "name": "Bulk Import B"},
                    ]
                },
            )
            assert d.get("imported") == 2
            assert d.get("failed") == 0
            for r in d["results"]:
                if r.get("id"):
                    topo_ids.append(r["id"])
            results.ok("bulk_import", f"imported={d['imported']}")
        except Exception as e:
            results.fail("bulk_import", e)

        # ── 4. Bulk import with project grouping ──────────────────────────
        try:
            proj = api(driver, "POST", "/network/api/projects", {"name": "E2E Import Project", "status": "draft"})
            pid = proj.get("id")
            d = api(
                driver,
                "POST",
                "/network/api/import/bulk",
                {
                    "project_id": pid,
                    "files": [
                        {"format": "drawio", "content": SAMPLE_DRAWIO, "name": "Project Import"},
                    ],
                },
            )
            assert d.get("imported") == 1
            # Verify linked to project
            topos = api(driver, "GET", "/network/api/topologies")
            project_import = next((t for t in topos if t["name"] == "Project Import"), None)
            assert project_import
            topo_ids.append(project_import["id"])
            # Cleanup project
            api(driver, "DELETE", f"/network/api/projects/{pid}")
            results.ok("bulk_import_with_project")
        except Exception as e:
            results.fail("bulk_import_with_project", e)

        # ── 5. Stitch/merge 2 topologies ──────────────────────────────────
        try:
            assert len(topo_ids) >= 2
            d = api(
                driver,
                "POST",
                "/network/api/import/stitch",
                {"topology_ids": topo_ids[:2], "name": "E2E Stitched Topology"},
            )
            assert d.get("id"), f"No stitch id: {d}"
            assert d.get("source_topologies") == 2
            assert d.get("nodes") >= 6  # 4 + 2 from both
            topo_ids.append(d["id"])
            results.ok("stitch_topologies", f"nodes={d['nodes']}, edges={d['edges']}")
        except Exception as e:
            results.fail("stitch_topologies", e)

        # ── 6. Stitch with interconnects ──────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/import/stitch",
                {
                    "topology_ids": topo_ids[:2],
                    "name": "E2E Stitched With IC",
                    "interconnects": [
                        {
                            "source_node_id": f"{topo_ids[0][:8]}_r1",
                            "target_node_id": f"{topo_ids[1][:8]}_r1",
                            "label": "BGP Peering",
                            "protocol": "bgp",
                        }
                    ],
                },
            )
            assert d.get("edges", 0) >= 3  # original edges + interconnect
            topo_ids.append(d["id"])
            results.ok("stitch_with_interconnects", f"edges={d['edges']}")
        except Exception as e:
            results.fail("stitch_with_interconnects", e)

        # ── 7. Import-as-audit ────────────────────────────────────────────
        try:
            d = api(
                driver,
                "POST",
                "/network/api/import/audit",
                {"format": "drawio", "content": SAMPLE_DRAWIO, "name": "E2E Audit Import"},
            )
            assert d.get("id")
            assert d.get("compliance"), f"No compliance: {d}"
            assert "scores" in d["compliance"]
            assert d.get("classified_types"), f"No types: {d}"
            topo_ids.append(d["id"])
            results.ok("import_as_audit", f"findings={d['compliance']['findings']}, types={d['classified_types']}")
        except Exception as e:
            results.fail("import_as_audit", e)

        # ── 8. Re-classify nodes ──────────────────────────────────────────
        try:
            d = api(driver, "POST", "/network/api/classify-nodes", {"topology_id": topo_ids[0]})
            assert "reclassified" in d
            results.ok("reclassify_nodes", f"changed={d['reclassified']}")
        except Exception as e:
            results.fail("reclassify_nodes", e)

        # ── 9. Clear All regression ───────────────────────────────────────
        try:
            d = api(driver, "DELETE", "/network/api/topologies/clear-all")
            assert d.get("cleared") == "topologies"
            results.ok("clear_all_regression")
        except Exception as e:
            results.fail("clear_all_regression", e)

        # Restore topology
        try:
            api(
                driver,
                "POST",
                "/network/api/topologies",
                {
                    "name": "Restored After Import Test",
                    "graph_json": {
                        "nodes": [
                            {"id": "r1", "label": "R1", "type": "router", "x": 100, "y": 100},
                        ],
                        "edges": [],
                    },
                },
            )
        except Exception:
            pass

        # ── 10. JS errors ─────────────────────────────────────────────────
        driver.get(f"{BASE_URL}/network/")
        time.sleep(2)
        errs = check_js_errors(driver)
        if errs:
            results.fail("js_errors", f"{len(errs)}: {errs[0][:80]}")
        else:
            results.ok("js_errors", "No SEVERE JS errors")

    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("E2E: Intelligent Import & Stitching (Phase 1)")
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
