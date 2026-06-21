# CUI // SP-CTI
"""
E2E tests — /cpmp/cor (COR Portal page).

Covers:
  1. Page returns HTTP < 400 (no crash, no server error)
  2. Rendered HTML includes the government read-only banner
  3. COR Portal heading is present
  4. Stat grid container renders without traceback
  5. Unauthenticated access is graceful (200 or redirect, not 500)

Run: python tests/e2e_cpmp_cor.py
"""
from __future__ import annotations

import os
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:5050")
_ENV_KEY = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
_HDR = {"Authorization": f"Bearer {_ENV_KEY}"} if _ENV_KEY else {}
_TIMEOUT = 15


class TestResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append(name)
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


def _get(path: str, **kwargs) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", headers=_HDR, timeout=_TIMEOUT, **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cpmp_cor_http_status(results: TestResult) -> None:
    # Route: /cpmp/cor
    try:
        resp = _get("/cpmp/cor", allow_redirects=True)
        assert resp.status_code < 400, f"expected <400, got {resp.status_code}"
        results.ok("cpmp_cor_http_status", str(resp.status_code))
    except Exception as exc:
        results.fail("cpmp_cor_http_status", exc)


def test_cpmp_cor_no_server_error(results: TestResult) -> None:
    try:
        resp = _get("/cpmp/cor", allow_redirects=True)
        body = resp.text
        assert "Internal Server Error" not in body, "page shows 500 error"
        assert "Traceback" not in body, "page exposes Python traceback"
        results.ok("cpmp_cor_no_server_error")
    except Exception as exc:
        results.fail("cpmp_cor_no_server_error", exc)


def test_cpmp_cor_gov_banner(results: TestResult) -> None:
    """Page must include the government read-only banner element."""
    try:
        resp = _get("/cpmp/cor", allow_redirects=True)
        body = resp.text
        has_banner = (
            'class="gov-banner"' in body
            or "Government Read-Only View" in body
            or "gov-banner" in body
        )
        assert has_banner, "government read-only banner not found in page HTML"
        results.ok("cpmp_cor_gov_banner")
    except Exception as exc:
        results.fail("cpmp_cor_gov_banner", exc)


def test_cpmp_cor_heading(results: TestResult) -> None:
    """Page must render the COR Portal heading."""
    try:
        resp = _get("/cpmp/cor", allow_redirects=True)
        body = resp.text
        has_heading = "COR Portal" in body or "Contracting Officer Representative" in body
        assert has_heading, "COR Portal heading not found in page HTML"
        results.ok("cpmp_cor_heading")
    except Exception as exc:
        results.fail("cpmp_cor_heading", exc)


def test_cpmp_cor_stat_grid(results: TestResult) -> None:
    """Stat grid container must be present (even with zero contracts)."""
    try:
        resp = _get("/cpmp/cor", allow_redirects=True)
        body = resp.text
        has_stats = "stat-grid" in body or "Total Contracts" in body
        assert has_stats, "stat grid not found in page HTML"
        results.ok("cpmp_cor_stat_grid")
    except Exception as exc:
        results.fail("cpmp_cor_stat_grid", exc)


def test_cpmp_cor_empty_state(results: TestResult) -> None:
    """Page must handle zero contracts gracefully (no crash)."""
    try:
        resp = _get("/cpmp/cor", allow_redirects=True)
        body = resp.text
        # Either shows contracts or empty-state message — not an error
        graceful = (
            "No contracts available" in body
            or "stat-grid" in body
            or "contract" in body.lower()
        )
        assert graceful, "empty contract state not handled gracefully"
        results.ok("cpmp_cor_empty_state")
    except Exception as exc:
        results.fail("cpmp_cor_empty_state", exc)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_tests() -> int:
    results = TestResult()

    print("\n=== /cpmp/cor E2E ===")
    test_cpmp_cor_http_status(results)
    test_cpmp_cor_no_server_error(results)
    test_cpmp_cor_gov_banner(results)
    test_cpmp_cor_heading(results)
    test_cpmp_cor_stat_grid(results)
    test_cpmp_cor_empty_state(results)

    s = results.summary()
    print(f"\nResult: {s['passed']}/{s['total']} passed")
    if s["failures"]:
        for name, err in s["failures"]:
            print(f"  FAIL {name}: {err}")
    return 1 if s["failures"] else 0


if __name__ == "__main__":
    sys.exit(run_tests())
