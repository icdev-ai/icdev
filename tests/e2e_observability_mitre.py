#!/usr/bin/env python3
"""Selenium E2E test for /observability/mitre MITRE ATT&CK matrix page.

Tests:
  1. Seed coverage — insert test design with gap/covered techniques
  2. Matrix page renders (title, legend, tactic columns, technique cells, cov-gap cells)
  3. Drill-through to technique detail page
  4. Screenshot at playwright/screenshots/observability-mitre.png

Usage:
    python tests/e2e_observability_mitre.py
"""

import json
import os
import sqlite3
import sys
import time
import traceback
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_ROOT = Path(__file__).resolve().parents[1]
_OC_DB = _ROOT / "data" / "observability_canvas.db"

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


class TestResult:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, name, detail=""):
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS: {name}" + (f" ({detail})" if detail else ""))

    def fail(self, name, error):
        self.failed.append({"test": name, "error": str(error)})
        print(f"  FAIL: {name} -- {error}")

    def summary(self):
        total = len(self.passed) + len(self.failed)
        return {
            "total": total,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "pass_rate": (f"{len(self.passed) / total * 100:.1f}%" if total else "0%"),
            "failures": self.failed,
        }


def screenshot(driver, name):
    path = SCREENSHOT_DIR / f"{name}.png"
    driver.save_screenshot(str(path))
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


def login(driver):
    """Authenticate against the ICDEV dashboard login form."""
    api_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "icdev_test_key_for_testing")
    driver.get(f"{BASE_URL}/login")
    time.sleep(1)
    try:
        key_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "api_key"))
        )
        key_input.clear()
        key_input.send_keys(api_key)
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        time.sleep(1)
    except Exception:
        pass


# ======================================================================
# Coverage seeding helpers
# ======================================================================

def seed_coverage():
    """Insert a minimal test observability design with MITRE gap techniques.

    Uses T1059 and T1566 as gaps, T1078 as covered — all present in the
    bundled enterprise.json catalog.  Returns the design_id.
    """
    design_id = "e2e-mitre-" + uuid.uuid4().hex[:8]
    graph_json = json.dumps({
        "nodes": [
            {
                "id": "bl-01",
                "type": "cmp-baseline",
                "label": "E2E Test Baseline",
                "x": 100,
                "y": 100,
                "config_json": {
                    "techniques": [
                        {"id": "T1059", "covered": False},
                        {"id": "T1566", "covered": False},
                        {"id": "T1078", "covered": True},
                    ]
                },
            }
        ],
        "edges": [],
    })

    conn = sqlite3.connect(str(_OC_DB))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observability_designs (
                id             TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                description    TEXT,
                graph_json     TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
                template_id    TEXT,
                classification TEXT DEFAULT 'CUI',
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO observability_designs (id, name, graph_json) VALUES (?, ?, ?)",
            (design_id, "E2E MITRE Coverage Test", graph_json),
        )
        conn.commit()
    finally:
        conn.close()

    return design_id


def cleanup_coverage(design_id):
    """Remove the seeded test design from the DB."""
    try:
        conn = sqlite3.connect(str(_OC_DB))
        try:
            conn.execute("DELETE FROM observability_designs WHERE id=?", (design_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


# ======================================================================
# Test: Matrix page renders (with seeded coverage)
# ======================================================================
def test_matrix_renders(driver, results, design_id=""):
    url = f"{BASE_URL}/observability/mitre"
    if design_id:
        url += f"?design_id={design_id}"
    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".mitre-grid"))
        )

        # Title
        h1 = driver.find_element(By.CSS_SELECTOR, ".mitre-hero h1")
        assert "MITRE ATT&CK" in h1.text, f"Unexpected title: {h1.text}"

        # Legend (3 items: Catalog, Gap, Covered)
        legend_items = driver.find_elements(By.CSS_SELECTOR, ".mitre-legend-item")
        assert len(legend_items) >= 3, f"Expected ≥3 legend items, got {len(legend_items)}"

        # Tactic columns
        tactic_cols = driver.find_elements(By.CSS_SELECTOR, ".mitre-tactic-col")
        assert len(tactic_cols) >= 1, f"Expected ≥1 tactic column, got {len(tactic_cols)}"

        # Technique cells
        tech_cells = driver.find_elements(By.CSS_SELECTOR, ".mitre-tech-cell")
        assert len(tech_cells) >= 1, f"Expected ≥1 technique cell, got {len(tech_cells)}"

        # Gap cells (only present when design_id is supplied and seeded)
        gap_cells = driver.find_elements(By.CSS_SELECTOR, ".mitre-tech-cell.cov-gap")
        if design_id:
            assert len(gap_cells) >= 1, (
                f"Expected ≥1 gap-colored cell with design_id={design_id}, got 0"
            )

        js_errs = check_js_errors(driver)
        if js_errs:
            results.fail("matrix_js_errors", "; ".join(js_errs[:3]))

        screenshot(driver, "observability-mitre")
        results.ok(
            "matrix_renders",
            f"tactics={len(tactic_cols)}, techniques={len(tech_cells)}, "
            f"legend={len(legend_items)}, gaps={len(gap_cells)}",
        )
    except Exception as exc:
        screenshot(driver, "observability-mitre-error")
        results.fail("matrix_renders", str(exc))


# ======================================================================
# Test: Drill-through to technique detail
# ======================================================================
def test_drill_through(driver, results, design_id=""):
    url = f"{BASE_URL}/observability/mitre"
    if design_id:
        url += f"?design_id={design_id}"
    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".mitre-tech-cell"))
        )

        # Prefer a gap cell when available; fall back to any parent cell
        gap_cells = driver.find_elements(By.CSS_SELECTOR, ".mitre-tech-cell.cov-gap")
        parent_cells = driver.find_elements(
            By.CSS_SELECTOR, ".mitre-tech-cell:not(.sub)"
        )
        candidates = gap_cells if gap_cells else parent_cells
        assert candidates, "No technique cells found"

        first_cell = candidates[0]
        tid = first_cell.find_element(By.CSS_SELECTOR, ".mitre-tech-id").text.strip()
        first_cell.click()

        # Wait for detail page
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".mdt-tid-badge"))
        )

        badge = driver.find_element(By.CSS_SELECTOR, ".mdt-tid-badge")
        assert tid in badge.text, f"Expected TID '{tid}' in badge, got '{badge.text}'"

        # Verify back link present
        back = driver.find_element(By.CSS_SELECTOR, ".mdt-back")
        assert back.is_displayed()

        screenshot(driver, "observability-mitre-detail")
        results.ok("drill_through", f"navigated to detail for {tid}")
    except Exception as exc:
        screenshot(driver, "observability-mitre-drill-error")
        results.fail("drill_through", str(exc))


# ======================================================================
# Main
# ======================================================================
def main():
    print("=" * 60)
    print("Observability MITRE E2E — Selenium Headless Chrome")
    print("=" * 60)
    print(f"Target: {BASE_URL}/observability/mitre")
    print(f"Screenshots: {SCREENSHOT_DIR}")
    print()

    results = TestResult()
    driver = None
    design_id = ""

    try:
        print("[0/4] Seeding coverage data...")
        design_id = seed_coverage()
        print(f"  Seeded design_id: {design_id}")

        driver = create_driver()

        print("[1/4] Login...")
        login(driver)

        print("[2/4] Matrix renders (with seeded gaps)...")
        test_matrix_renders(driver, results, design_id=design_id)

        print("[3/4] Drill-through (gap technique → detail)...")
        test_drill_through(driver, results, design_id=design_id)

        print("[4/4] Cleanup coverage seed...")
        cleanup_coverage(design_id)
        print("  Seed removed.")

    except Exception as exc:
        traceback.print_exc()
        results.fail("setup", str(exc))
    finally:
        if driver:
            driver.quit()
        if design_id:
            cleanup_coverage(design_id)

    print()
    print("=" * 60)
    s = results.summary()
    status = "PASSED" if s["failed"] == 0 else "FAILED"
    print(f"Result: {status} — {s['passed']}/{s['total']} tests passed ({s['pass_rate']})")
    if s["failures"]:
        print("Failures:")
        for f in s["failures"]:
            print(f"  - {f['test']}: {f['error']}")
    print("=" * 60)

    return 0 if s["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
