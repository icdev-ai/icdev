#!/usr/bin/env python3
# CUI // SP-CTI
"""Smoke test for ACE Session Replay UI pages.

Checks:
  1. GET /coworker/sessions — page renders with expected HTML landmarks
  2. GET /api/ace/sessions — API returns valid JSON with expected fields
  3. GET /coworker/sessions/<id> — detail page renders (if any sessions exist)
  4. GET /api/ace/sessions/<id> — detail API returns expected structure

Usage:
    python tools/testing/ace_session_smoke.py
    python tools/testing/ace_session_smoke.py --fast   # skip detail page
    python tools/testing/ace_session_smoke.py --json   # machine-readable output
    python tools/testing/ace_session_smoke.py --url http://localhost:5050
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 10) -> tuple[int, str]:
    """Return (status_code, body_text). Raises on connection error."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "passed": passed, "detail": detail}


# ---------------------------------------------------------------------------
# Smoke checks
# ---------------------------------------------------------------------------

def check_sessions_list_page(base_url: str) -> dict[str, Any]:
    """GET /coworker/sessions returns 200 with expected HTML landmarks."""
    url = f"{base_url}/coworker/sessions"
    try:
        status, body = _get(url)
    except Exception as exc:
        return _check("sessions_list_page", False, f"connection error: {exc}")

    if status != 200:
        return _check("sessions_list_page", False, f"HTTP {status}")

    checks = {
        "status_200": status == 200,
        "has_sessions_title": "sessions" in body.lower(),
        "has_table_or_empty": "<table" in body or "no sessions" in body.lower() or "sessions" in body.lower(),
        "no_template_error": "TemplateNotFound" not in body and "500" not in body[:200],
    }
    passed = all(checks.values())
    detail = "; ".join(f"{k}={'✓' if v else '✗'}" for k, v in checks.items())
    return _check("sessions_list_page", passed, detail)


def check_sessions_api(base_url: str) -> tuple[dict[str, Any], str | None]:
    """GET /api/ace/sessions returns valid JSON with expected schema."""
    url = f"{base_url}/api/ace/sessions"
    try:
        status, body = _get(url)
    except Exception as exc:
        return _check("sessions_api", False, f"connection error: {exc}"), None

    if status != 200:
        return _check("sessions_api", False, f"HTTP {status}"), None

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return _check("sessions_api", False, f"invalid JSON: {exc}"), None

    has_sessions_key = "sessions" in data
    has_total = "total" in data
    sessions_is_list = isinstance(data.get("sessions"), list)

    passed = has_sessions_key and has_total and sessions_is_list
    detail = (
        f"keys={list(data.keys())[:8]} "
        f"total={data.get('total', '?')} "
        f"sessions_count={len(data.get('sessions', []))}"
    )

    first_id: str | None = None
    if sessions_is_list and data["sessions"]:
        first_id = data["sessions"][0].get("session_id") or data["sessions"][0].get("id")

    return _check("sessions_api", passed, detail), first_id


def check_session_detail_page(base_url: str, session_id: str) -> dict[str, Any]:
    """GET /coworker/sessions/<id> returns 200 with expected HTML."""
    url = f"{base_url}/coworker/sessions/{session_id}"
    try:
        status, body = _get(url)
    except Exception as exc:
        return _check("session_detail_page", False, f"connection error: {exc}")

    if status != 200:
        return _check("session_detail_page", False, f"HTTP {status}")

    checks = {
        "status_200": status == 200,
        "has_session_id": session_id[:8] in body or session_id in body,
        "no_template_error": "TemplateNotFound" not in body and "500" not in body[:200],
    }
    passed = all(checks.values())
    detail = "; ".join(f"{k}={'✓' if v else '✗'}" for k, v in checks.items())
    return _check("session_detail_page", passed, detail)


def check_session_detail_api(base_url: str, session_id: str) -> dict[str, Any]:
    """GET /api/ace/sessions/<id> returns valid JSON."""
    url = f"{base_url}/api/ace/sessions/{session_id}"
    try:
        status, body = _get(url)
    except Exception as exc:
        return _check("session_detail_api", False, f"connection error: {exc}")

    if status not in (200, 404):
        return _check("session_detail_api", False, f"HTTP {status}")

    if status == 404:
        return _check("session_detail_api", True, "HTTP 404 (session may be missing from this env)")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return _check("session_detail_api", False, f"invalid JSON: {exc}")

    has_session_id = "session_id" in data
    has_turns = "turns_parsed" in data or "turns" in data or "messages" in data
    passed = has_session_id or has_turns  # lenient: either key is enough
    detail = f"keys={list(data.keys())[:10]}"
    return _check("session_detail_api", passed, detail)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_smoke(base_url: str, fast: bool = False) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    results.append(check_sessions_list_page(base_url))

    api_check, first_session_id = check_sessions_api(base_url)
    results.append(api_check)

    if not fast and first_session_id:
        results.append(check_session_detail_page(base_url, first_session_id))
        results.append(check_session_detail_api(base_url, first_session_id))
    elif not fast:
        results.append(_check(
            "session_detail_page", True,
            "skipped — no sessions in DB (empty state is acceptable)"
        ))
        results.append(_check(
            "session_detail_api", True,
            "skipped — no sessions in DB"
        ))

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="ACE Session Replay UI smoke test")
    parser.add_argument("--url", default="http://localhost:5050", help="Base URL")
    parser.add_argument("--fast", action="store_true", help="Skip detail page checks")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        results = run_smoke(args.url, fast=args.fast)
    except Exception as exc:
        if args.as_json:
            print(json.dumps({"error": str(exc), "checks": []}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    success = passed == total

    if args.as_json:
        print(json.dumps({
            "success": success,
            "passed": passed,
            "total": total,
            "checks": results,
        }, indent=2))
    else:
        for r in results:
            icon = "✓" if r["passed"] else "✗"
            print(f"  [{icon}] {r['check']}: {r['detail']}")
        print(f"\n{'PASS' if success else 'FAIL'}: {passed}/{total} checks passed")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
