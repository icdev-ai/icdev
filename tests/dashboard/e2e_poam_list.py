"""E2E test for /api/poam — canvas findings list.

Lifecycle:
  1. /api/poam returns 200 with findings list (no 500)
  2. decision=pending filter works (all returned rows have decision=pending)
  3. canvas=security filter narrows rows to security only
  4. severity=CAT1 filter narrows rows
  5. /api/poam/summary returns 200 with counts
  6. /poam page loads 200
  7. #findings-tbody populates with real rows (not error / loading placeholder)
  8. Screenshot captured, no SEVERE JS errors (excluding unrelated favicon)

Run: python tests/dashboard/e2e_poam_list.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class R:
    def __init__(self): self.p = []; self.f = []
    def ok(self, n, d=""): self.p.append(n); print(f"[OK]   {n}{(': '+d) if d else ''}")
    def fail(self, n, e): self.f.append((n, str(e)[:300])); print(f"[FAIL] {n}: {str(e)[:300]}")


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def main() -> int:
    res = R()

    # 1. Base endpoint 200
    try:
        status, data = _get(f"{BASE_URL}/api/poam?include_remediated=true")
        assert status == 200
        assert "findings" in data and "total" in data
        res.ok("api_poam_base_200", f"total={data['total']}")
    except Exception as e:
        res.fail("api_poam_base_200", e)

    # 2. decision=pending filter
    try:
        status, data = _get(f"{BASE_URL}/api/poam?decision=pending&include_remediated=true")
        assert status == 200
        rows = data.get("findings", [])
        assert all(r.get("decision") == "pending" for r in rows), \
            f"non-pending row: {[r.get('decision') for r in rows[:3]]}"
        res.ok("api_poam_decision_filter", f"{len(rows)} pending rows")
    except Exception as e:
        res.fail("api_poam_decision_filter", e)

    # 3. canvas=security filter
    try:
        status, data = _get(f"{BASE_URL}/api/poam?canvas=security")
        rows = data.get("findings", [])
        if rows:
            assert all(r.get("canvas_source") == "security" for r in rows)
            res.ok("api_poam_canvas_filter", f"{len(rows)} security rows")
        else:
            res.ok("api_poam_canvas_filter", "no security rows (acceptable)")
    except Exception as e:
        res.fail("api_poam_canvas_filter", e)

    # 4. severity filter
    try:
        status, data = _get(f"{BASE_URL}/api/poam?severity=CAT1")
        rows = data.get("findings", [])
        assert all(r.get("severity") == "CAT1" for r in rows)
        res.ok("api_poam_severity_filter", f"{len(rows)} CAT1 rows")
    except Exception as e:
        res.fail("api_poam_severity_filter", e)

    # 5. summary
    try:
        status, data = _get(f"{BASE_URL}/api/poam/summary")
        assert status == 200
        res.ok("api_poam_summary",
               f"keys={list(data.keys())[:5] if isinstance(data, dict) else type(data)}")
    except Exception as e:
        res.fail("api_poam_summary", e)

    # UI
    driver = None
    try:
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        driver = webdriver.Chrome(options=opts)
        driver.get(f"{BASE_URL}/poam")
        time.sleep(3.0)
        res.ok("poam_page_loads", f"title={driver.title[:40]!r}")

        try:
            tbody = driver.find_element(By.ID, "findings-tbody")
            html = tbody.get_attribute("innerHTML") or ""
            assert "Loading findings" not in html, "still showing loading placeholder"
            # Either "no findings match" (valid if filters exclude all) or real rows
            has_rows = tbody.find_elements(By.CSS_SELECTOR, "tr:not(.empty-row)")
            assert has_rows or "No findings match" in html, f"tbody html: {html[:200]}"
            res.ok("tbody_populated", f"{len(has_rows)} rows or empty state")
        except Exception as e:
            res.fail("tbody_populated", e)

        shot = SCREENSHOT_DIR / "poam-findings-list-1920.png"
        driver.save_screenshot(str(shot))
        res.ok("screenshot", shot.name)

        errs = []
        try:
            for entry in driver.get_log("browser"):
                if entry.get("level") != "SEVERE":
                    continue
                msg = entry.get("message", "")
                if "favicon" in msg.lower():
                    continue
                errs.append(msg)
        except Exception:
            pass
        assert not errs, f"{len(errs)} SEVERE: {errs[0][:150] if errs else ''}"
        res.ok("no_js_errors")
    except Exception as e:
        res.fail("ui_check", e)
    finally:
        if driver:
            try: driver.quit()
            except Exception: pass

    print("\n" + "=" * 60)
    print(f"PASSED: {len(res.p)}  FAILED: {len(res.f)}  TOTAL: {len(res.p)+len(res.f)}")
    for n, e in res.f:
        print(f"  - {n}: {e}")
    return 0 if not res.f else 1


if __name__ == "__main__":
    sys.exit(main())
