#!/usr/bin/env python3
# CUI // SP-CTI
"""Selenium E2E — /lineage page: seed lineage data, navigate, assert ≥1 node + 1 edge.

Acceptance:
  1. GET /lineage renders the SVG container and controls.
  2. Loading a seeded project shows ≥1 node circle + ≥1 edge line in the SVG.
  3. Switching to a non-existent project narrows visible set to 0 (filter behavior).
  4. Screenshot saved to playwright/screenshots/data-lineage.png.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.db.storage import get_connection  # noqa: E402

BASE = "http://127.0.0.1:5050"
TEST_PROJECT = "e2e-lineage-test-001"
SCREENSHOT = Path("playwright/screenshots/data-lineage.png")
BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = str(BASE_DIR / "data" / "icdev.db")
# Use high integer IDs unlikely to collide with real data
_SEED_ID_1 = 990001
_SEED_ID_2 = 990002


# ---------------------------------------------------------------------------
# Seed / cleanup
# ---------------------------------------------------------------------------

def _seed_lineage() -> None:
    """Insert 2 digital_thread_links rows for TEST_PROJECT."""
    conn = get_connection(db_path=DB_PATH)
    try:
        # Clean stale test rows first, then insert fresh ones
        conn.execute(
            "DELETE FROM digital_thread_links WHERE id IN (?, ?)",
            (_SEED_ID_1, _SEED_ID_2),
        )
        # Row 1: requirement → design element (2 nodes + 1 edge in the lineage graph)
        conn.execute(
            "INSERT INTO digital_thread_links "
            "(id, project_id, source_type, source_id, target_type, target_id, link_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_SEED_ID_1, TEST_PROJECT, "doors_requirement", "REQ-E2E-001", "sysml_element", "SYS-E2E-001", "satisfies"),
        )
        # Row 2: design element → test file (1 new node + 1 new edge — SYS-E2E-001 already seen)
        conn.execute(
            "INSERT INTO digital_thread_links "
            "(id, project_id, source_type, source_id, target_type, target_id, link_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_SEED_ID_2, TEST_PROJECT, "sysml_element", "SYS-E2E-001", "test_file", "TST-E2E-001", "verifies"),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_lineage() -> None:
    conn = get_connection(db_path=DB_PATH)
    try:
        conn.execute(
            "DELETE FROM digital_thread_links WHERE id IN (?, ?)",
            (_SEED_ID_1, _SEED_ID_2),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Driver factory + helpers
# ---------------------------------------------------------------------------

def _chrome() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


def _dismiss_tour(driver: webdriver.Chrome) -> None:
    """Remove the ICDEV onboarding tour overlay if present."""
    driver.execute_script(
        "var el = document.getElementById('icdev-tour-welcome');"
        "if (el) el.remove();"
        "document.querySelectorAll('.icdev-tour-welcome,.tour-overlay,.modal-overlay.active')"
        ".forEach(function(e){e.remove();});"
    )


def _js_click(driver: webdriver.Chrome, element) -> None:
    """Click via JS to bypass overlay interception."""
    driver.execute_script("arguments[0].click()", element)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_page_loads() -> None:
    """Page at /lineage renders SVG container and project-id input."""
    driver = _chrome()
    try:
        driver.get(f"{BASE}/lineage")
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "lin-svg"))
        )
        assert driver.find_element(By.ID, "lin-project-id"), "project-id input missing"
        assert driver.find_element(By.ID, "lin-nodes"), "lin-nodes stat missing"
        assert driver.find_element(By.ID, "lin-edges"), "lin-edges stat missing"
    finally:
        driver.quit()


def test_graph_renders_nodes_and_edges() -> None:
    """Load seeded project → ≥1 node + ≥1 edge in SVG; filter to empty project → 0."""
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    driver = _chrome()
    try:
        driver.get(f"{BASE}/lineage")
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.ID, "lin-project-id")))

        # Dismiss tour overlay before interacting
        _dismiss_tour(driver)

        # Load seeded project
        driver.find_element(By.ID, "lin-project-id").send_keys(TEST_PROJECT)
        _js_click(driver, driver.find_element(By.XPATH, "//button[contains(text(),'Load Lineage')]"))
        time.sleep(2)

        # Stat cards must update from "--"
        nodes_text = driver.find_element(By.ID, "lin-nodes").text
        assert nodes_text != "--", f"node count not updated: {nodes_text!r}"
        node_count = int(nodes_text)
        assert node_count >= 1, f"expected ≥1 node, got {node_count}"

        edges_text = driver.find_element(By.ID, "lin-edges").text
        assert edges_text != "--", f"edge count not updated: {edges_text!r}"
        edge_count = int(edges_text)
        assert edge_count >= 1, f"expected ≥1 edge, got {edge_count}"

        # SVG must contain circle (node) and line (edge) elements
        circles = driver.execute_script(
            "return document.querySelectorAll('#lin-svg circle').length"
        )
        lines = driver.execute_script(
            "return document.querySelectorAll('#lin-svg line').length"
        )
        assert circles >= 1, f"SVG has no <circle> elements (nodes): circles={circles}"
        assert lines >= 1, f"SVG has no <line> elements (edges): lines={lines}"

        # Screenshot with nodes visible
        driver.save_screenshot(str(SCREENSHOT))
        print(f"screenshot: {SCREENSHOT}")

        # Filter: non-existent project narrows visible set to 0
        _dismiss_tour(driver)
        inp = driver.find_element(By.ID, "lin-project-id")
        inp.clear()
        inp.send_keys("no-such-project-xyz")
        _js_click(driver, driver.find_element(By.XPATH, "//button[contains(text(),'Load Lineage')]"))
        time.sleep(2)

        nodes_after = driver.find_element(By.ID, "lin-nodes").text
        assert nodes_after == "0", (
            f"filter should show 0 nodes for non-existent project, got: {nodes_after!r}"
        )
    finally:
        driver.quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    t0 = time.time()
    _seed_lineage()
    try:
        test_page_loads()
        test_graph_renders_nodes_and_edges()
    finally:
        _cleanup_lineage()
    print(f"PASS in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
