# CUI // SP-CTI
"""Selenium E2E — /boundary/cato (BDC cATO Dashboard).

Lifecycle:
  1. Login and seed 2 boundary designs via POST /boundary/api/designs
  2. Run assessment on the first design via POST /boundary/api/designs/<id>/assess
  3. Navigate to /boundary/cato
  4. Assert both designs are visible as project rows
  5. Click the assessed design → detail panel opens with control findings
  6. Assert ≥1 control row rendered
  7. Screenshot saved to playwright/screenshots/boundary-cato.png
  8. Cleanup seeded designs via DELETE /boundary/api/designs/<id>

Run: python tests/e2e_boundary_cato.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

DESIGN_NAMES = ["Alpha BDC E2E Project", "Bravo BDC E2E Project"]


class R:
    def __init__(self):
        self.p = []
        self.f = []

    def ok(self, n, d=""):
        self.p.append(n)
        print(f"[OK]   {n}{(': ' + d) if d else ''}")

    def fail(self, n, e):
        self.f.append((n, str(e)[:300]))
        print(f"[FAIL] {n}: {str(e)[:300]}")


def _api(driver, method, path, body=None):
    """Authenticated API call via the browser session (execute_async_script)."""
    script = """
    var done = arguments[arguments.length - 1];
    var method = arguments[0];
    var path = arguments[1];
    var body = arguments[2];
    var opts = {method: method, headers: {'Content-Type': 'application/json'}};
    if (body !== null && body !== undefined) opts.body = JSON.stringify(body);
    fetch(path, opts)
        .then(function(r) { return r.json(); })
        .then(done)
        .catch(function(e) { done({__error: String(e), status: 0}); });
    """
    return driver.execute_async_script(script, method, path, body)


def _login(driver):
    driver.get(f"{BASE_URL}/login")
    time.sleep(1)
    try:
        driver.find_element(By.NAME, "username").send_keys("admin")
        driver.find_element(By.NAME, "password").send_keys("admin")
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        time.sleep(1)
    except Exception:
        pass


def main() -> int:
    res = R()

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=opts)
    design_ids = []

    try:
        # ── 1. Login ──────────────────────────────────────────────────────
        _login(driver)
        driver.get(f"{BASE_URL}/boundary/")
        time.sleep(1.5)
        res.ok("login")

        # ── 2. Seed 2 boundary designs via API ────────────────────────────
        for name in DESIGN_NAMES:
            result = _api(driver, "POST", "/boundary/api/designs", {"name": name, "description": "E2E test — safe to delete"})
            d_id = (result or {}).get("id")
            if d_id:
                design_ids.append(d_id)
                res.ok(f"create_design_{name[:15]}", f"id={d_id}")
            else:
                res.fail(f"create_design_{name[:15]}", f"no id in response: {result}")

        if len(design_ids) < 2:
            res.fail("seed_designs", f"only {len(design_ids)} designs created — stopping")
            return 1

        # ── 3. Run assessment on first design ─────────────────────────────
        assess_result = _api(driver, "POST", f"/boundary/api/designs/{design_ids[0]}/assess", {"type": "full"})
        findings_count = len((assess_result or {}).get("findings", []))
        res.ok("run_assessment", f"score={(assess_result or {}).get('score','?')} findings={findings_count}")

        # ── 4. Navigate to /boundary/cato ─────────────────────────────────
        try:
            driver.get_log("browser")  # flush pre-navigation log entries
        except Exception:
            pass
        driver.get(f"{BASE_URL}/boundary/cato")
        time.sleep(2.5)
        res.ok("page_load", f"title={driver.title[:50]!r}")

        # ── 5. Assert 2 designs visible ───────────────────────────────────
        try:
            wait = WebDriverWait(driver, 10)
            rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.project-row")))
            visible_names = []
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if cells:
                    visible_names.append(cells[0].text.strip())
            matched = sum(1 for n in DESIGN_NAMES if any(n in v for v in visible_names))
            assert matched >= 2, f"expected 2 seeded designs, found {matched} in {visible_names[:5]}"
            res.ok("two_projects_visible", f"matched={matched} total_rows={len(rows)}")
        except Exception as exc:
            res.fail("two_projects_visible", exc)

        # ── 6. Click assessed design row ──────────────────────────────────
        try:
            # Dismiss tour welcome overlay if present (blocks clicks)
            driver.execute_script(
                "var el = document.getElementById('icdev-tour-welcome');"
                "if (el) { el.classList.remove('visible'); el.style.display='none'; }"
            )
            time.sleep(0.3)
            target_row = None
            for row in driver.find_elements(By.CSS_SELECTOR, "tr.project-row"):
                cells = row.find_elements(By.TAG_NAME, "td")
                if cells and DESIGN_NAMES[0] in cells[0].text:
                    target_row = row
                    break
            assert target_row is not None, f"row for {DESIGN_NAMES[0]!r} not found"
            driver.execute_script("arguments[0].click();", target_row)
            time.sleep(2.0)
            res.ok("row_click", f"clicked {DESIGN_NAMES[0]!r}")
        except Exception as exc:
            res.fail("row_click", exc)

        # ── 7. Assert ≥1 control row in detail panel ──────────────────────
        try:
            detail = WebDriverWait(driver, 8).until(
                EC.visibility_of_element_located((By.ID, "control-detail"))
            )
            assert detail.is_displayed(), "control-detail panel not visible"
            control_rows = driver.find_elements(By.CSS_SELECTOR, "#control-tbody tr")
            assert len(control_rows) >= 1, (
                f"expected ≥1 control row, got {len(control_rows)}. "
                f"tbody html: {driver.find_element(By.ID, 'control-tbody').get_attribute('innerHTML')[:200]}"
            )
            res.ok("control_rows_visible", f"{len(control_rows)} control rows rendered")
        except Exception as exc:
            res.fail("control_rows_visible", exc)

        # ── 8. Screenshot ─────────────────────────────────────────────────
        shot = SCREENSHOT_DIR / "boundary-cato.png"
        driver.save_screenshot(str(shot))
        res.ok("screenshot", shot.name)

        # ── 9. No SEVERE JS errors (filter test-env noise) ───────────────
        _NOISE = ("favicon", "/api/projects/progress", "/api/kanban/tasks")
        try:
            errs = [
                e["message"]
                for e in driver.get_log("browser")
                if e.get("level") == "SEVERE"
                and not any(n in e.get("message", "") for n in _NOISE)
            ]
            assert not errs, f"{len(errs)} SEVERE JS error(s): {errs[0][:150]}"
            res.ok("no_js_errors")
        except Exception as exc:
            res.fail("no_js_errors", exc)

    except Exception as exc:
        res.fail("browser_session", exc)
    finally:
        # ── 10. Cleanup ───────────────────────────────────────────────────
        for d_id in design_ids:
            try:
                _api(driver, "DELETE", f"/boundary/api/designs/{d_id}")
                res.ok(f"cleanup_{d_id[:8]}")
            except Exception as exc:
                res.fail(f"cleanup_{d_id[:8]}", exc)
        try:
            driver.quit()
        except Exception:
            pass

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"PASSED: {len(res.p)}  FAILED: {len(res.f)}  TOTAL: {len(res.p) + len(res.f)}")
    for n, e in res.f:
        print(f"  - {n}: {e}")
    return 0 if not res.f else 1


if __name__ == "__main__":
    sys.exit(main())
