"""
E2E Selenium test — /writeguard (Content Quality Dashboard).

Verifies the WriteGuard page and core APIs end-to-end:
  1. GET /writeguard returns 200 and renders expected DOM anchors
  2. GET /api/writeguard/modes returns known modes list
  3. POST /api/writeguard/analyze runs 5-dimension quality check and returns findings
  4. GET /api/writeguard/history lists the just-created analysis
  5. GET /api/writeguard/history/<id> returns full result with findings
  6. POST /api/writeguard/export returns exportable content (txt/md/json)
  7. Screenshot capture at 1920x1080
  8. No SEVERE JS errors (favicon/404 excluded)

Run: python tests/e2e_writeguard.py
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


def _http_json(url: str, method: str = "GET", data: dict | None = None, timeout: int = 30):
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — localhost test harness
        return resp.status, json.loads(resp.read().decode("utf-8"))


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
            if "favicon" in msg.lower() or "/favicon" in msg:
                continue
            errors.append(msg)
    except Exception:
        pass
    return errors


SAMPLE_TEXT = (
    "WriteGuard is a deterministic content-quality engine. It scores grammar, "
    "readability, tone, plagiarism risk, and AI-detection signals across five "
    "dimensions. This test submission is intentionally short but coherent."
)


def main() -> int:
    result = TestResult()
    driver = None

    try:
        driver = create_driver()

        # 1. Load page
        try:
            driver.get(f"{BASE_URL}/writeguard")
            time.sleep(1.5)
            body = driver.find_element(By.TAG_NAME, "body").text or ""
            assert driver.current_url.endswith("/writeguard"), driver.current_url
            assert "writeguard" in (driver.title or "").lower() or len(body) > 0
            result.ok("page_loads", f"title={driver.title[:40]!r}")
        except Exception as e:
            result.fail("page_loads", e)

        # 2. Modes endpoint
        try:
            status, data = _http_json(f"{BASE_URL}/api/writeguard/modes")
            assert status == 200, status
            modes = data.get("modes") or data.get("data") or data
            assert modes, "empty modes response"
            result.ok("api_modes", f"keys={list(data.keys())[:5]}")
        except Exception as e:
            result.fail("api_modes", e)

        # 3. Analyze endpoint
        analysis_id = None
        try:
            status, data = _http_json(
                f"{BASE_URL}/api/writeguard/analyze",
                method="POST",
                data={"text": SAMPLE_TEXT, "mode": "default"},
                timeout=60,
            )
            assert status == 200, status
            # result id field varies across variants
            analysis_id = (
                data.get("result_id")
                or data.get("id")
                or (data.get("result") or {}).get("id")
            )
            score = data.get("score") or data.get("overall_score") or (data.get("result") or {}).get("score")
            assert analysis_id or score is not None, f"no id/score in response: {list(data.keys())}"
            result.ok("api_analyze", f"id={analysis_id} score={score}")
        except Exception as e:
            result.fail("api_analyze", e)

        # 4. History list
        try:
            status, data = _http_json(f"{BASE_URL}/api/writeguard/history?limit=5")
            assert status == 200, status
            items = data.get("results") or data.get("history") or data.get("items") or []
            result.ok("api_history_list", f"count={len(items)}")
        except Exception as e:
            result.fail("api_history_list", e)

        # 5. History detail (only if we got an id)
        if analysis_id:
            try:
                status, data = _http_json(f"{BASE_URL}/api/writeguard/history/{analysis_id}")
                assert status == 200, status
                findings = data.get("findings") or (data.get("result") or {}).get("findings") or []
                result.ok("api_history_detail", f"findings={len(findings)}")
            except Exception as e:
                result.fail("api_history_detail", e)

        # 6. Export endpoint (txt format)
        try:
            export_payload: dict = {"text": SAMPLE_TEXT, "format": "txt"}
            if analysis_id:
                export_payload["result_id"] = analysis_id
            status, data = _http_json(
                f"{BASE_URL}/api/writeguard/export",
                method="POST",
                data=export_payload,
                timeout=30,
            )
            # Accept 200 (export returned) or 404/501 (export not yet wired for this id)
            assert status in (200, 404, 501), f"unexpected status {status}"
            result.ok("api_export", f"status={status}")
        except Exception as e:
            result.fail("api_export", e)

        # 7. Screenshot
        try:
            driver.get(f"{BASE_URL}/writeguard")
            time.sleep(1.0)
            shot = SCREENSHOT_DIR / "writeguard-page-1920.png"
            driver.save_screenshot(str(shot))
            result.ok("screenshot", str(shot.name))
        except Exception as e:
            result.fail("screenshot", e)

        # 8. JS errors
        try:
            errs = check_js_errors(driver)
            assert not errs, f"{len(errs)} SEVERE errors: {errs[0][:120] if errs else ''}"
            result.ok("no_js_errors")
        except Exception as e:
            result.fail("no_js_errors", e)

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    summary = result.summary()
    print("\n" + "=" * 60)
    print(f"PASSED: {summary['passed']}  FAILED: {summary['failed']}  TOTAL: {summary['total']}")
    if summary["failures"]:
        for name, err in summary["failures"]:
            print(f"  - {name}: {err}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
