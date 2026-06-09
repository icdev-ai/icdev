#!/usr/bin/env python3
"""Smoke test all /infra* routes on PG-primary dashboard.

Acceptance: Every endpoint returns HTTP 200; no '500', 'UndefinedTable',
or 'UndefinedColumn' errors are logged.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:5050"
TIMEOUT = 15.0

# ── Results tracking ───────────────────────────────────────────────
results: list[dict] = []
failures: list[dict] = []


def _request(
    method: str,
    route: str,
    body: dict | None = None,
    headers: dict | None = None,
    expect_status: int = 200,
) -> dict:
    url = BASE.rstrip("/") + route
    req_headers = {"User-Agent": "ICDEV-InfraSmoke/1.0", "Accept": "application/json, text/html"}
    if headers:
        req_headers.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    t0 = time.monotonic()
    result = {
        "route": route,
        "method": method,
        "url": url,
        "status": None,
        "ok": False,
        "error": None,
        "elapsed_ms": 0,
        "body_snippet": "",
    }
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body_bytes = resp.read(65536)
            result["status"] = resp.status
            result["body_snippet"] = body_bytes.decode("utf-8", errors="replace")[:2000]
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        try:
            result["body_snippet"] = e.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            pass
        result["error"] = f"HTTP {e.code} {e.reason}"
    except Exception as e:
        result["error"] = str(e)
    result["elapsed_ms"] = round((time.monotonic() - t0) * 1000)

    # Check acceptance criteria
    if result["status"] != expect_status:
        result["ok"] = False
        if not result["error"]:
            result["error"] = f"Expected {expect_status}, got {result['status']}"
    else:
        result["ok"] = True

    # Scan for fatal signals even on 200
    fatal_signals = [
        "UndefinedTable", "UndefinedColumn", "Internal Server Error",
        "Traceback (most recent call last)", "no such table", "no such column",
        "relation \"" , "column \"" , "does not exist",
    ]
    lower_body = result["body_snippet"].lower()
    for sig in fatal_signals:
        if sig.lower() in lower_body:
            result["ok"] = False
            result["error"] = f"Body contains fatal signal: '{sig}'"
            break

    results.append(result)
    if not result["ok"]:
        failures.append(result)
    return result


def _get(route: str, expect_status: int = 200) -> dict:
    return _request("GET", route, expect_status=expect_status)


def _post(route: str, body: dict | None = None, expect_status: int = 200) -> dict:
    return _request("POST", route, body=body, expect_status=expect_status)


def _post_any_ok(route: str, body: dict | None = None) -> dict:
    """POST that accepts 200 or 201."""
    r = _request("POST", route, body=body, expect_status=200)
    if r["status"] == 201:
        r["ok"] = True
        r["error"] = None
    return r


def _put(route: str, body: dict | None = None, expect_status: int = 200) -> dict:
    return _request("PUT", route, body=body, expect_status=expect_status)


def _delete(route: str, expect_status: int = 200) -> dict:
    return _request("DELETE", route, expect_status=expect_status)


# ── Phase 1: Static GET pages (no params) ──────────────────────────
print("Phase 1: Static GET pages...")
static_gets = [
    "/infra/",
    "/infra/canvas/new",
    "/infra/templates",
    "/infra/assessments",
    "/infra/twin",
    "/infra/emit",
    "/infra/runbooks",
    "/infra/sops",
    "/infra/ask",
    "/infra/api/templates",
    "/infra/api/snippets",
    "/infra/api/objects",
    "/infra/api/equivalents",
    "/infra/api/ai-trace",
]
for r in static_gets:
    _get(r)

# ── Phase 2: Create a design for parameterized routes ──────────────
print("Phase 2: Creating a design...")
post_design = _post_any_ok("/infra/api/designs", body={
    "name": "Smoke-Test Design",
    "description": "Temporary design for smoke testing",
    "graph_json": '{"nodes":[],"edges":[]}',
    "classification": "CUI",
})
design_id = None
if post_design["ok"]:
    try:
        design_id = json.loads(post_design["body_snippet"]).get("id")
    except Exception:
        pass

if not design_id:
    # Fallback: try to extract from redirect or body
    print(f"  WARN: Could not create design via POST. Status={post_design['status']}, err={post_design['error']}")
    print(f"  Body snippet: {post_design['body_snippet'][:500]}")
    # Try using the /infra/canvas/new redirect approach
    new_canvas_resp = _get("/infra/canvas/new")
    if new_canvas_resp["status"] == 302:
        loc = new_canvas_resp.get("headers", {}).get("Location", "")
        if "/infra/canvas/" in loc:
            design_id = loc.split("/infra/canvas/")[-1]

if not design_id:
    print("ERROR: No design_id available; skipping parameterized routes.")
else:
    print(f"  Using design_id={design_id}")

    # Parameterized GET pages
    print("Phase 3: Parameterized GET pages...")
    parameterized_gets = [
        f"/infra/canvas/{design_id}",
        f"/infra/remediation/{design_id}",
        f"/infra/api/designs/{design_id}",
        f"/infra/api/versions/{design_id}",
        f"/infra/api/collab/{design_id}/participants",
        f"/infra/api/collab/{design_id}/poll",
    ]
    for r in parameterized_gets:
        _get(r)

    # POST endpoints
    print("Phase 4: POST endpoints...")
    _post(f"/infra/api/designs/{design_id}/assess", body={})
    _post(f"/infra/api/designs/{design_id}/auto-fix", body={})
    _post(f"/infra/api/versions/{design_id}", body={
        "change_summary": "smoke-test snapshot",
        "user_id": "smoke",
    })
    _post(f"/infra/api/designs/{design_id}/governance", body={
        "policy_id": "smoke-policy",
        "status": "review",
    })
    _post(f"/infra/api/collab/{design_id}/join", body={"user_id": "smoke"})
    _post(f"/infra/api/collab/{design_id}/leave", body={"user_id": "smoke"})
    _post(f"/infra/api/collab/{design_id}/push", body={"patch": []})
    _post(f"/infra/api/import/cloud", body={
        "provider": "aws",
        "resources": [],
    })

    # Export endpoints (POST but return generated artifacts)
    print("Phase 5: Export endpoints...")
    export_routes = [
        f"/infra/api/export/{design_id}/vsdx",
        f"/infra/api/export/{design_id}/json",
        f"/infra/api/export/{design_id}/markdown",
        f"/infra/api/export/{design_id}/csv",
        f"/infra/api/export/{design_id}/drawio",
        f"/infra/api/export/{design_id}/svg",
        f"/infra/api/export/{design_id}/terraform",
        f"/infra/api/export/{design_id}/cloudformation",
        f"/infra/api/export/{design_id}/pulumi",
        f"/infra/api/export/{design_id}/ansible",
        f"/infra/api/export/{design_id}/helm",
    ]
    for r in export_routes:
        _post(r, body={})

    # PUT endpoints
    print("Phase 6: PUT endpoints...")
    _put(f"/infra/api/designs/{design_id}", body={
        "name": "Smoke-Updated",
        "graph_json": '{"nodes":[],"edges":[]}',
    })

    # IQE query
    print("Phase 7: IQE / API ask...")
    _post("/infra/api/iqe-query", body={"query": "test", "collections": ["idc_designs"]})
    _post("/infra/api/ask", body={"question": "What is this?", "design_id": design_id})

    # Cleanup: delete design
    print("Phase 8: Cleanup...")
    _delete(f"/infra/api/designs/{design_id}")

# ── Phase 9: Bulk delete (empty body ok) ───────────────────────────
print("Phase 9: Bulk operations...")
_post("/infra/api/designs", body={"ids": []}, expect_status=200)  # might return 400 but not 500
_post("/infra/emit/run", body={
    "project": '{"nodes":[],"edges":[]}',
    "target": "terraform",
    "csp": "aws",
})

# ── Report ─────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SMOKE TEST REPORT")
print("=" * 70)
total = len(results)
passed = sum(1 for r in results if r["ok"])
failed = total - passed
print(f"Total routes tested : {total}")
print(f"Passed              : {passed}")
print(f"Failed              : {failed}")

if failures:
    print("\nFAILED ROUTES:")
    for f in failures:
        print(f"  {f['method']:6} {f['route']:50} -> {f['status']} | {f['error']}")
        # Print body snippet if it contains an error signal
        if "fatal signal" in (f.get("error") or "").lower():
            snippet = f["body_snippet"].replace("\n", "\n    ")
            print(f"    Body: {snippet[:500]}")

print("=" * 70)

# Acceptance gate
if failed > 0:
    print("\nRESULT: FAIL -- acceptance criteria not met.")
    sys.exit(1)
else:
    print("\nRESULT: PASS -- all /infra* routes returned HTTP 200 with no fatal errors.")
    sys.exit(0)
