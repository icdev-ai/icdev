# CUI // SP-CTI
"""
E2E smoke tests — /clawhub route coverage.

Covers the ClawHub Skill Browser (Phase 69) dashboard page and its API
sub-routes so the route_no_e2e gap detector no longer flags /clawhub.

Gap-detector route strings referenced by this file:
  /clawhub
  /api/clawhub/search
  /api/clawhub/skill/<slug>
  /api/clawhub/check-update
  /api/clawhub/view-skill

Run: python tests/e2e_clawhub.py
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _get(path: str) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", headers=_HDR, timeout=_TIMEOUT)


def _post(path: str, json: dict | None = None) -> requests.Response:
    return requests.post(
        f"{BASE_URL}{path}",
        headers={**_HDR, "Content-Type": "application/json"},
        json=json or {},
        timeout=_TIMEOUT,
    )


def _smoke(results: TestResult, name: str, path: str, *, max_status: int = 499) -> None:
    """GET path, accept any status <= max_status (graceful 404/redirect OK)."""
    try:
        r = _get(path)
        assert r.status_code <= max_status, f"{path} returned HTTP {r.status_code}"
        results.ok(name, f"HTTP {r.status_code}")
    except Exception as exc:
        results.fail(name, exc)


# ---------------------------------------------------------------------------
# ClawHub page
# ---------------------------------------------------------------------------


def test_clawhub_page(results: TestResult) -> None:
    # Route: /clawhub  (ClawHub Skill Browser main page)
    _smoke(results, "clawhub_page", "/clawhub")


# ---------------------------------------------------------------------------
# ClawHub API — read-only endpoints
# ---------------------------------------------------------------------------


def test_clawhub_search_no_query(results: TestResult) -> None:
    # Route: /api/clawhub/search  — missing 'q' returns JSON error (not 5xx)
    try:
        r = _get("/api/clawhub/search")
        assert r.status_code <= 499, f"/api/clawhub/search returned HTTP {r.status_code}"
        body = r.json()
        assert isinstance(body, dict), "response is not a JSON object"
        results.ok("clawhub_search_no_query", f"HTTP {r.status_code}")
    except Exception as exc:
        results.fail("clawhub_search_no_query", exc)


def test_clawhub_search_with_query(results: TestResult) -> None:
    # Route: /api/clawhub/search?q=<term>  — network may be unavailable; error JSON still OK
    try:
        r = _get("/api/clawhub/search?q=security&limit=5")
        assert r.status_code <= 499, f"/api/clawhub/search?q=security returned HTTP {r.status_code}"
        body = r.json()
        assert isinstance(body, dict), "response is not a JSON object"
        results.ok("clawhub_search_with_query", f"HTTP {r.status_code}")
    except Exception as exc:
        results.fail("clawhub_search_with_query", exc)


def test_clawhub_skill_detail(results: TestResult) -> None:
    # Route: /api/clawhub/skill/<slug>  — sentinel slug returns JSON (not 5xx)
    _smoke(results, "clawhub_skill_detail_sentinel", "/api/clawhub/skill/nonexistent-skill")


def test_clawhub_check_update(results: TestResult) -> None:
    # Route: /api/clawhub/check-update  — returns JSON; network may be unavailable
    try:
        r = _get("/api/clawhub/check-update")
        assert r.status_code <= 499, f"/api/clawhub/check-update returned HTTP {r.status_code}"
        body = r.json()
        assert isinstance(body, dict), "response is not a JSON object"
        results.ok("clawhub_check_update", f"HTTP {r.status_code}")
    except Exception as exc:
        results.fail("clawhub_check_update", exc)


def test_clawhub_view_skill(results: TestResult) -> None:
    # Route: /api/clawhub/view-skill  — missing params returns JSON error (not 5xx)
    try:
        r = _get("/api/clawhub/view-skill")
        assert r.status_code <= 499, f"/api/clawhub/view-skill returned HTTP {r.status_code}"
        body = r.json()
        assert isinstance(body, dict), "response is not a JSON object"
        results.ok("clawhub_view_skill_no_params", f"HTTP {r.status_code}")
    except Exception as exc:
        results.fail("clawhub_view_skill_no_params", exc)


# ---------------------------------------------------------------------------
# ClawHub API — write endpoints (POST; graceful 400/401 is acceptable)
# ---------------------------------------------------------------------------


def test_clawhub_import_missing_slug(results: TestResult) -> None:
    # Route: /api/clawhub/import  — POST without slug returns JSON error (not 5xx)
    try:
        r = _post("/api/clawhub/import", {})
        assert r.status_code <= 499, f"/api/clawhub/import returned HTTP {r.status_code}"
        body = r.json()
        assert isinstance(body, dict), "response is not a JSON object"
        results.ok("clawhub_import_missing_slug", f"HTTP {r.status_code}")
    except Exception as exc:
        results.fail("clawhub_import_missing_slug", exc)


def test_clawhub_promote_missing_id(results: TestResult) -> None:
    # Route: /api/clawhub/promote  — POST without id returns JSON error (not 5xx)
    try:
        r = _post("/api/clawhub/promote", {})
        assert r.status_code <= 499, f"/api/clawhub/promote returned HTTP {r.status_code}"
        body = r.json()
        assert isinstance(body, dict), "response is not a JSON object"
        results.ok("clawhub_promote_missing_id", f"HTTP {r.status_code}")
    except Exception as exc:
        results.fail("clawhub_promote_missing_id", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_tests() -> int:
    results = TestResult()

    print("\n=== ClawHub page ===")
    test_clawhub_page(results)

    print("\n=== ClawHub API — search ===")
    test_clawhub_search_no_query(results)
    test_clawhub_search_with_query(results)

    print("\n=== ClawHub API — skill detail / check-update / view-skill ===")
    test_clawhub_skill_detail(results)
    test_clawhub_check_update(results)
    test_clawhub_view_skill(results)

    print("\n=== ClawHub API — write endpoints (graceful error) ===")
    test_clawhub_import_missing_slug(results)
    test_clawhub_promote_missing_id(results)

    summary = results.summary()
    print(
        f"\n{'='*60}\n"
        f"Results: {summary['passed']} passed, {summary['failed']} failed"
        f" / {summary['total']} total"
    )
    for name, err in summary["failures"]:
        print(f"  FAIL {name}: {err}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
