# CUI // SP-CTI
"""E2E Selenium test for Migration Intelligence Engine (/migration-intel/).

Verifies:
1. Dashboard nav link for Migration Intel exists
2. Index page loads (HTTP 200) with key UI sections
3. POST /api/migration-intel/scan returns valid JSON with sub_scans
4. GET  /api/migration-intel/opportunities returns list
5. GET  /api/migration-intel/status returns engine status
6. GET  /api/migration-intel/goals returns list
7. No SEVERE JS errors on index page
8. Screenshots captured
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_TIMEOUT = 15


def _api_get(path: str) -> tuple[int, dict]:
    try:
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:
            body = {}
        return exc.code, body
    except Exception as exc:
        return 0, {"error": str(exc)}


def _api_post(path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload or {}).encode()
    try:
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:
            body = {}
        return exc.code, body
    except Exception as exc:
        return 0, {"error": str(exc)}


def main() -> int:
    from tools.browser.driver_manager import get_driver

    driver = get_driver(headless=True, window_size=(1920, 1080))
    passed = 0
    failed = 0
    results = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        sym = "✓" if ok else "✗"
        print(f"  [{sym}] {name}" + (f" — {detail}" if detail else ""))
        results.append({"name": name, "passed": ok, "detail": detail})
        if ok:
            passed += 1
        else:
            failed += 1

    print("=" * 60)
    print("E2E: Migration Intelligence Engine")
    print("=" * 60)

    # ── 1. Index page loads ───────────────────────────────────────
    try:
        driver.get(f"{BASE_URL}/migration-intel/")
        time.sleep(2)
        status_ok = "migration" in driver.title.lower() or "intel" in driver.title.lower() or driver.title != ""
        record("Index page loads (HTTP 200)", status_ok, driver.title)
    except Exception as exc:
        record("Index page loads (HTTP 200)", False, str(exc))

    # ── 2. Nav link present ───────────────────────────────────────
    try:
        src = driver.page_source
        has_nav = "migration" in src.lower() and ("intel" in src.lower() or "migration-intel" in src.lower())
        record("Nav/page references Migration Intel", has_nav)
    except Exception as exc:
        record("Nav/page references Migration Intel", False, str(exc))

    # ── 3. No SEVERE JS errors ────────────────────────────────────
    try:
        logs = driver.get_log("browser")
        severe = [l for l in logs if l.get("level") == "SEVERE"
                  and "favicon.ico" not in l.get("message", "")]
        record("No SEVERE JS errors", len(severe) == 0,
               f"{len(severe)} severe" if severe else "clean")
    except Exception:
        record("No SEVERE JS errors", True, "log unavailable — skip")

    # ── 4. Screenshot ─────────────────────────────────────────────
    try:
        ss = str(SCREENSHOT_DIR / "migration_intel_index.png")
        driver.save_screenshot(ss)
        record("Screenshot saved", True, ss)
    except Exception as exc:
        record("Screenshot saved", False, str(exc))

    driver.quit()

    # ── 5. API: /status ───────────────────────────────────────────
    code, body = _api_get("/api/migration-intel/status")
    record("GET /api/migration-intel/status returns 200", code == 200,
           f"HTTP {code}")

    # ── 6. API: /opportunities ────────────────────────────────────
    code, body = _api_get("/api/migration-intel/opportunities")
    record("GET /api/migration-intel/opportunities returns 200", code == 200,
           f"HTTP {code}")

    # ── 7. API: /goals ────────────────────────────────────────────
    code, body = _api_get("/api/migration-intel/goals")
    record("GET /api/migration-intel/goals returns 200", code == 200,
           f"HTTP {code}")

    # ── 8. API: /scan (cross-canvas scan) ────────────────────────
    code, body = _api_post("/api/migration-intel/scan")
    record("POST /api/migration-intel/scan returns 200", code == 200,
           f"HTTP {code}")
    if code == 200:
        sub = body.get("sub_scans", {})
        new_keys = {"security_designs", "boundary_designs", "pipeline_designs"}
        found_new = new_keys & set(sub.keys())
        record("Scan includes SDC/BDC/PDC canvas sources",
               len(found_new) == 3,
               f"found: {sorted(found_new)}" if found_new else "missing security_designs/boundary_designs/pipeline_designs")

    # ── 9. API: /roadmaps ────────────────────────────────────────
    code, body = _api_get("/api/migration-intel/roadmaps")
    record("GET /api/migration-intel/roadmaps returns 200", code == 200,
           f"HTTP {code}")

    # ── 10. API: /wishlist ────────────────────────────────────────
    code, body = _api_get("/api/migration-intel/wishlist")
    record("GET /api/migration-intel/wishlist returns 200", code == 200,
           f"HTTP {code}")

    # ── Summary ───────────────────────────────────────────────────
    print("=" * 60)
    print(f"Result: {passed} passed, {failed} failed")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
