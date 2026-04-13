"""E2E test for OPT-59 — Remediation History wired to audit_trail.

Lifecycle:
  1. /api/oracle/proposals/history?limit=5 returns >=1 item with real shape
  2. Item has id, status, title, canvas, created_at, actor, action
  3. /api/oracle/proposals/stats returns counts matching history size bound
  4. canvas filter narrows results to matching rows only
  5. /api/oracle/proposals/unified returns a non-empty proposals list
  6. /  index page loads 200
  7. Remediation History tile populates (not the 'No remediation history' placeholder)
  8. Screenshot captured, no SEVERE JS errors

Run: python tests/dashboard/e2e_oracle_history.py
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
    def fail(self, n, e): self.f.append((n, str(e)[:200])); print(f"[FAIL] {n}: {str(e)[:200]}")


def _get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def main() -> int:
    res = R()

    # 1. History endpoint returns items
    items = []
    try:
        status, data = _get(f"{BASE_URL}/api/oracle/proposals/history?limit=5")
        assert status == 200
        items = data.get("history", [])
        assert len(items) >= 1, f"no history items: {data}"
        assert data.get("total") == len(items)
        res.ok("history_returns_items", f"{len(items)} items")
    except Exception as e:
        res.fail("history_returns_items", e)

    # 2. Item shape matches UI contract
    try:
        assert items
        it = items[0]
        for field in ("id", "status", "title", "canvas", "created_at", "actor", "action"):
            assert field in it, f"missing {field} in {list(it.keys())}"
        assert it["status"] in ("approved", "pending", "failed", "expired", "rejected")
        res.ok("item_shape", f"status={it['status']} canvas={it['canvas']!r}")
    except Exception as e:
        res.fail("item_shape", e)

    # 3. Stats endpoint
    try:
        status, data = _get(f"{BASE_URL}/api/oracle/proposals/stats")
        assert status == 200
        assert data.get("total", 0) >= 1
        assert data.get("approved", 0) + data.get("pending", 0) + \
               data.get("failed", 0) + data.get("rejected", 0) <= data["total"]
        res.ok("stats_endpoint",
               f"total={data['total']} approved={data['approved']} pending={data['pending']}")
    except Exception as e:
        res.fail("stats_endpoint", e)

    # 4. Canvas filter narrows results
    try:
        status, data = _get(f"{BASE_URL}/api/oracle/proposals/history?canvas=security&limit=10")
        filtered = data.get("history", [])
        if filtered:
            assert all(it.get("canvas") == "security" for it in filtered)
            res.ok("canvas_filter", f"{len(filtered)} security rows")
        else:
            res.ok("canvas_filter", "no security rows (acceptable)")
    except Exception as e:
        res.fail("canvas_filter", e)

    # 5. Unified endpoint
    try:
        status, data = _get(f"{BASE_URL}/api/oracle/proposals/unified?limit=5")
        proposals = data.get("proposals", [])
        assert len(proposals) >= 1
        res.ok("unified_endpoint", f"{len(proposals)} proposals")
    except Exception as e:
        res.fail("unified_endpoint", e)

    # 6-8. UI render
    driver = None
    try:
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        driver = webdriver.Chrome(options=opts)
        driver.get(f"{BASE_URL}/")
        time.sleep(3.0)  # allow async history fetch to complete
        res.ok("index_loads", f"title={driver.title[:40]!r}")

        # Tile should have at least one entry, not the placeholder
        try:
            tile = driver.find_element(By.ID, "remediation-history-list")
            html = tile.get_attribute("innerHTML") or ""
            assert "No remediation history" not in html, "placeholder still shown"
            assert len(html) > 100, f"tile too small: {len(html)} chars"
            res.ok("tile_populated", f"{len(html)} chars of history HTML")
        except Exception as e:
            res.fail("tile_populated", e)

        shot = SCREENSHOT_DIR / "oracle-remediation-history-1920.png"
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
        assert not errs, f"{len(errs)} SEVERE: {errs[0][:100] if errs else ''}"
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
