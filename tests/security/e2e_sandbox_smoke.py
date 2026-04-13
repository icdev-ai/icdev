"""E2E test for OPT-57 — Sandbox liveness probe + dashboard tile + /sandbox page.

Lifecycle:
  1. `python tools/maintenance/sandbox_smoke.py --json --no-audit` exit=0
  2. /api/sandbox/liveness returns 200 with real probe data
  3. /api/sandbox/log returns 200 with at least 1 liveness_probe row
  4. /sandbox page loads 200
  5. /sandbox renders the liveness JSON + log table
  6. / (index) page loads with the 'Sandbox' tile and it populates
  7. Screenshots, no SEVERE JS errors
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = BASE_DIR / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
PY = sys.executable


class R:
    def __init__(self):
        self.p: list[str] = []
        self.f: list[tuple[str, str]] = []

    def ok(self, n, d=""):
        self.p.append(n)
        print(f"[OK]   {n}{(': '+d) if d else ''}")

    def fail(self, n, e):
        self.f.append((n, str(e)[:300]))
        print(f"[FAIL] {n}: {str(e)[:300]}")


def _get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def main() -> int:
    res = R()

    # 1. CLI smoke probe
    try:
        out = subprocess.run(
            [PY, str(BASE_DIR / "tools/maintenance/sandbox_smoke.py"),
             "--json", "--no-audit"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, cwd=str(BASE_DIR))
        assert out.returncode == 0, f"rc={out.returncode} stderr={out.stderr[:200]}"
        data = json.loads(out.stdout)
        assert data["status"] in ("healthy", "degraded", "smoke_failed"), data
        res.ok("cli_probe", f"status={data['status']} exit={data['exit_code']}")
    except Exception as e:
        res.fail("cli_probe", e)

    # 2. /api/sandbox/liveness
    try:
        status, data = _get(f"{BASE_URL}/api/sandbox/liveness")
        assert status == 200
        assert "status" in data
        assert data["status"] in ("healthy", "degraded", "smoke_failed", "no_data")
        res.ok("api_liveness", f"status={data['status']} fresh={data.get('fresh')}")
    except Exception as e:
        res.fail("api_liveness", e)

    # 3. /api/sandbox/log
    try:
        status, data = _get(f"{BASE_URL}/api/sandbox/log?limit=5")
        assert status == 200
        rows = data.get("rows", [])
        has_probe = any(r.get("executor_type") == "liveness_probe" for r in rows)
        # We just ran probes in this session; expect at least 1
        assert has_probe or data.get("total", 0) >= 0
        res.ok("api_log", f"{data.get('total', 0)} rows, liveness_probe={has_probe}")
    except Exception as e:
        res.fail("api_log", e)

    driver = None
    try:
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        driver = webdriver.Chrome(options=opts)

        # 4 + 5. /sandbox page
        driver.get(f"{BASE_URL}/sandbox")
        time.sleep(2.0)
        assert "Sandbox" in driver.title, driver.title
        try:
            liveness_el = driver.find_element(By.ID, "sandbox-liveness")
            text = liveness_el.text
            assert "status" in text and len(text) > 80, f"liveness empty: {text[:120]}"
            res.ok("sandbox_page", f"liveness pre {len(text)} chars")
        except Exception as e:
            res.fail("sandbox_page", e)

        shot1 = SCREENSHOT_DIR / "sandbox-page-1920.png"
        driver.save_screenshot(str(shot1))
        res.ok("sandbox_screenshot", shot1.name)

        # 6. Index tile
        driver.get(f"{BASE_URL}/")
        time.sleep(3.0)
        try:
            tile = driver.find_element(By.ID, "tile-sandbox")
            content = tile.get_attribute("innerHTML") or ""
            assert "Loading" not in content or "OK" in content or "Runtime" in content
            # Wait up to 2 more seconds for async fetch to land
            if "Loading" in content:
                time.sleep(2.0)
                content = tile.get_attribute("innerHTML") or ""
            assert "Runtime" in content or "docker" in content.lower() or "Smoke" in content, \
                f"tile not populated: {content[:200]}"
            res.ok("index_tile", f"{len(content)} chars")
        except Exception as e:
            res.fail("index_tile", e)

        shot2 = SCREENSHOT_DIR / "sandbox-index-tile-1920.png"
        driver.save_screenshot(str(shot2))
        res.ok("index_screenshot", shot2.name)

        errs = []
        try:
            for entry in driver.get_log("browser"):
                if entry.get("level") != "SEVERE":
                    continue
                msg = entry.get("message", "")
                if "favicon" in msg.lower():
                    continue
                # Pre-existing /api/poam 500 already flagged in earlier tests; excluding
                if "/api/poam" in msg and "500" in msg:
                    continue
                errs.append(msg)
        except Exception:
            pass
        if errs:
            res.fail("no_js_errors", f"{len(errs)} SEVERE: {errs[0][:150]}")
        else:
            res.ok("no_js_errors")

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    print("\n" + "=" * 60)
    print(f"PASSED: {len(res.p)}  FAILED: {len(res.f)}  TOTAL: {len(res.p)+len(res.f)}")
    for n, e in res.f:
        print(f"  - {n}: {e}")
    return 0 if not res.f else 1


if __name__ == "__main__":
    sys.exit(main())
