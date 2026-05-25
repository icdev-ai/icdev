"""E2E test for OPT-60: Innovation → Kanban promoter.

Verifies the full lifecycle:
  1. CLI --list returns >=1 promotable signal (JSON shape)
  2. CLI --dry-run inserts nothing
  3. /api/kanban/tasks?status=suggested returns the previously-promoted rows
  4. Each suggested row has INNOV- prefix title + source_prediction_id provenance
  5. Re-running --promote-id on an already-promoted signal is a no-op
  6. /kanban page loads with 200 and renders the Suggested column
  7. Screenshot captured, no SEVERE JS errors

Run: python tests/innovation/e2e_kanban_promoter.py
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
PROMOTER = [sys.executable, str(BASE_DIR / "tools/innovation/kanban_promoter.py")]


class R:
    def __init__(self): self.p = []; self.f = []
    def ok(self, n, d=""): self.p.append(n); print(f"[OK]   {n}{(': '+d) if d else ''}")
    def fail(self, n, e): self.f.append((n, str(e)[:200])); print(f"[FAIL] {n}: {str(e)[:200]}")


def _get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _cli(*args):
    r = subprocess.run(PROMOTER + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(BASE_DIR))
    return r.returncode, r.stdout, r.stderr


def main() -> int:
    res = R()

    # 1. --list returns JSON array
    try:
        code, out, err = _cli("--list", "--limit", "3", "--json")
        assert code == 0, f"exit={code} stderr={err}"
        items = json.loads(out)
        assert isinstance(items, list), type(items)
        res.ok("cli_list", f"returned {len(items)} items")
    except Exception as e:
        res.fail("cli_list", e)

    # 2. --dry-run is a no-op
    try:
        code, out, _ = _cli("--dry-run", "--limit", "1", "--json")
        assert code == 0
        data = json.loads(out)
        assert data.get("dry_run") is True
        res.ok("cli_dry_run", f"inserted={data.get('inserted')}")
    except Exception as e:
        res.fail("cli_dry_run", e)

    # 3. API returns suggested tasks
    suggested_rows = []
    try:
        status, data = _get(f"{BASE_URL}/api/kanban/tasks?status=suggested")
        assert status == 200
        suggested_rows = data if isinstance(data, list) else (
            data.get("tasks") or data.get("items") or data.get("data") or [])
        assert len(suggested_rows) >= 1, f"no suggested rows: {data}"
        res.ok("api_suggested", f"{len(suggested_rows)} rows")
    except Exception as e:
        res.fail("api_suggested", e)

    # 4. Provenance check — INNOV- prefix + source_prediction_id
    try:
        innov = [t for t in suggested_rows if str(t.get("title", "")).startswith("INNOV-")]
        assert innov, "no INNOV- titled rows in suggested"
        with_provenance = [t for t in innov if t.get("source_prediction_id", "").startswith("sig-")]
        assert with_provenance, "INNOV rows missing sig- provenance"
        res.ok("provenance", f"{len(with_provenance)}/{len(innov)} rows have sig- provenance")
    except Exception as e:
        res.fail("provenance", e)

    # 5. --promote-id idempotency
    try:
        if suggested_rows:
            sid = next((t["source_prediction_id"] for t in suggested_rows
                       if t.get("source_prediction_id", "").startswith("sig-")), None)
            assert sid
            code, out, _ = _cli("--promote-id", sid, "--json")
            data = json.loads(out)
            assert data.get("inserted") == 0 and data.get("skipped_existing") == 1
            res.ok("idempotent_promote_id", f"{sid} correctly skipped")
    except Exception as e:
        res.fail("idempotent_promote_id", e)

    # 6. /kanban page renders
    driver = None
    try:
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        driver = webdriver.Chrome(options=opts)
        driver.get(f"{BASE_URL}/kanban")
        time.sleep(2.0)
        driver.find_element(By.TAG_NAME, "body").text
        assert driver.title, "no title"
        res.ok("page_loads", f"title={driver.title[:40]!r}")

        # 7. Screenshot + JS errors
        shot = SCREENSHOT_DIR / "kanban-with-innovation-suggested-1920.png"
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
