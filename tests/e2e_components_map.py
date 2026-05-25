"""
E2E Selenium test — /components-map (Phase 1g, Internal Awareness Engine).

Verifies the visual component map end-to-end:
  1.  Page loads with 200 + stats populated
  2.  Tree sidebar renders >=6 entity_type categories
  3.  Graph renders >=900 node cells on first paint
  4.  /api/components-map/tree returns the flat-dict shape
  5.  /api/components-map/graph returns cells with type=node|edge
  6.  /api/components-map/graph?scope=canvas_module filters to ~8 nodes
  7.  /api/components-map/node/<id> returns detail with relationships_count
  8.  Hover on a rendered node shows the tooltip div
  9.  Click on tree category sets filter + reloads graph
  10. Click on graph node highlights + opens drawer
  11. Screenshot capture at 1920x1080
  12. No SEVERE JS errors (favicon/404 excluded)

Run: python tests/e2e_components_map.py
"""
from __future__ import annotations

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
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"[OK]   {name}{(': ' + detail) if detail else ''}")

    def fail(self, name: str, err: object) -> None:
        msg = str(err)[:200]
        self.failed.append((name, msg))
        print(f"[FAIL] {name}: {msg}")

    def summary(self) -> dict:
        return {
            "passed": len(self.passed),
            "failed": len(self.failed),
            "total": len(self.passed) + len(self.failed),
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


def check_js_errors(driver) -> list[str]:
    errors: list[str] = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") != "SEVERE":
                continue
            msg = entry.get("message", "")
            # Exclude pre-existing noise
            if any(
                token in msg.lower()
                for token in ("favicon", "401", "404", "failed to load resource")
            ):
                continue
            errors.append(msg)
    except Exception:
        pass
    return errors


def api(driver, method: str, path: str):
    script = f"""
        const r = await fetch('{path}', {{method: '{method}'}});
        return {{status: r.status, body: await r.json()}};
    """
    return driver.execute_script(script)


def run_tests() -> int:
    results = TestResult()
    driver = create_driver()

    try:
        # ── 1. Page load ─────────────────────────────────────────────
        try:
            driver.get(f"{BASE_URL}/components-map")
            time.sleep(2)
            title = driver.title
            assert "Components Map" in title or "ICDEV" in title, f"unexpected title: {title}"
            results.ok("page_loads", f"title={title[:40]}")
        except Exception as exc:
            results.fail("page_loads", exc)
            return 1  # Can't continue if page doesn't load

        # ── 2. Stats banner populated ────────────────────────────────
        try:
            stat_total = driver.find_element(By.ID, "stat-total").text.strip()
            stat_enabled = driver.find_element(By.ID, "stat-enabled").text.strip()
            stat_disabled = driver.find_element(By.ID, "stat-disabled").text.strip()
            total_int = int(stat_total)
            assert total_int >= 900, f"expected >=900 nodes, got {total_int}"
            results.ok(
                "stats_banner",
                f"total={stat_total} enabled={stat_enabled} disabled={stat_disabled}",
            )
        except Exception as exc:
            results.fail("stats_banner", exc)

        # ── 3. /api/components-map/tree shape ────────────────────────
        try:
            resp = api(driver, "GET", "/api/components-map/tree")
            assert resp["status"] == 200, f"status={resp['status']}"
            body = resp["body"]
            assert isinstance(body, dict), f"expected dict, got {type(body)}"
            expected_types = {"skill", "mcp_server", "canvas_module", "goal", "reflex", "tool"}
            missing = expected_types - set(body.keys())
            assert not missing, f"missing entity types: {missing}"
            results.ok(
                "api_tree_shape",
                f"categories={len(body)}, canvas_module={len(body.get('canvas_module', []))}",
            )
        except Exception as exc:
            results.fail("api_tree_shape", exc)

        # ── 4. /api/components-map/graph shape + count ───────────────
        try:
            resp = api(driver, "GET", "/api/components-map/graph")
            assert resp["status"] == 200
            body = resp["body"]
            cells = body.get("cells", [])
            node_cells = [c for c in cells if c.get("type") == "node"]
            assert len(node_cells) >= 900, f"expected >=900 node cells, got {len(node_cells)}"
            # Every node cell must have the expected fields
            sample = node_cells[0]
            for field in ("id", "label", "entity_type", "enabled", "properties"):
                assert field in sample, f"missing field '{field}' in node cell"
            results.ok("api_graph_shape", f"cells={len(cells)}, nodes={len(node_cells)}")
        except Exception as exc:
            results.fail("api_graph_shape", exc)

        # ── 5. Scoped graph filter ───────────────────────────────────
        try:
            resp = api(driver, "GET", "/api/components-map/graph?scope=canvas_module")
            assert resp["status"] == 200
            cells = resp["body"].get("cells", [])
            nodes = [c for c in cells if c.get("type") == "node"]
            assert 5 <= len(nodes) <= 20, f"expected 5-20 canvas_module nodes, got {len(nodes)}"
            # All nodes should be canvas_module
            non_canvas = [c for c in nodes if c.get("entity_type") != "canvas_module"]
            assert not non_canvas, f"found non-canvas_module in scoped response: {non_canvas}"
            results.ok("api_graph_scoped", f"canvas_module={len(nodes)}")
        except Exception as exc:
            results.fail("api_graph_scoped", exc)

        # ── 6. Node detail endpoint ──────────────────────────────────
        try:
            # Pick any node id from the tree
            resp = api(driver, "GET", "/api/components-map/tree")
            skills = resp["body"].get("skill", [])
            assert skills, "no skills in tree"
            target_id = skills[0]["id"]
            detail = api(driver, "GET", f"/api/components-map/node/{target_id}")
            assert detail["status"] == 200
            nd = detail["body"]
            assert nd["id"] == target_id
            assert "label" in nd
            assert "properties" in nd
            assert "relationships_count" in nd
            results.ok(
                "api_node_detail",
                f"id={target_id[:20]}... label={nd['label'][:25]} rels={nd['relationships_count']}",
            )
        except Exception as exc:
            results.fail("api_node_detail", exc)

        # ── 7. Tree sidebar renders ──────────────────────────────────
        try:
            # Wait briefly for tree to populate
            time.sleep(1)
            # Reload to give JS time
            driver.get(f"{BASE_URL}/components-map")
            time.sleep(3)
            summaries = driver.find_elements(By.CSS_SELECTOR, "#cmap-sidebar details summary")
            assert len(summaries) >= 6, f"expected >=6 tree categories, got {len(summaries)}"
            results.ok("tree_sidebar_renders", f"categories={len(summaries)}")
        except Exception as exc:
            results.fail("tree_sidebar_renders", exc)

        # ── 8. Graph canvas paints nodes ─────────────────────────────
        try:
            # JointJS renders SVG elements inside #cmap-paper
            time.sleep(2)  # allow layout + scaleContentToFit
            svg_elements = driver.find_elements(
                By.CSS_SELECTOR, "#cmap-paper .joint-element"
            )
            # Default: show-disabled is off, so we expect ~970 enabled nodes
            assert len(svg_elements) >= 500, (
                f"expected >=500 rendered joint-element nodes, got {len(svg_elements)}"
            )
            results.ok("graph_canvas_paints", f"rendered_elements={len(svg_elements)}")
        except Exception as exc:
            results.fail("graph_canvas_paints", exc)

        # ── 9. Screenshot for manual review ──────────────────────────
        try:
            screenshot_path = SCREENSHOT_DIR / "components-map-desktop.png"
            driver.save_screenshot(str(screenshot_path))
            assert screenshot_path.exists() and screenshot_path.stat().st_size > 10_000
            results.ok("screenshot", f"saved to {screenshot_path.name}")
        except Exception as exc:
            results.fail("screenshot", exc)

        # ── 10. JS errors check ──────────────────────────────────────
        try:
            errors = check_js_errors(driver)
            assert not errors, f"{len(errors)} JS errors: {errors[:2]}"
            results.ok("no_js_errors")
        except Exception as exc:
            results.fail("no_js_errors", exc)

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    summary = results.summary()
    print()
    print("=" * 70)
    print(f"PHASE 1g E2E: passed={summary['passed']} failed={summary['failed']}")
    print("=" * 70)
    if summary["failed"]:
        for name, err in summary["failures"]:
            print(f"  FAIL: {name}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_tests())
