#!/usr/bin/env python3
# CUI // SP-CTI
"""E2E tests for MCP Streamable HTTP transport (mcp_http.py)."""

import hashlib
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

os.environ["PYTHONIOENCODING"] = "utf-8"


def main():
    # ---- Setup ----
    from tools.saas.platform_db import init_platform_db, SQLITE_PATH

    data_dir = BASE_DIR / "data"
    data_dir.mkdir(exist_ok=True)

    # Clean start
    for f in [str(SQLITE_PATH), str(data_dir / "tenants" / "acme-defense.db")]:
        if os.path.exists(f):
            os.remove(f)

    init_platform_db()
    print("1. Platform DB initialized")

    # Create tenant
    from tools.saas.tenant_manager import create_tenant, provision_tenant

    result = create_tenant(
        "ACME Defense", "IL4", "professional", admin_email="admin@acme.gov"
    )
    tenant_id = result["tenant"]["id"]
    # For IL2-IL4 + professional, auto-provision happens in create_tenant
    # but call provision_tenant if status is still pending/provisioning
    if result["tenant"].get("status") not in ("active",):
        provision_tenant(tenant_id)
    print("2. Tenant created + provisioned: %s" % tenant_id)

    # Create API key
    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    key_raw = "icdev_test_e2e_key_1234567890"
    key_hash = hashlib.sha256(key_raw.encode()).hexdigest()
    key_id = str(uuid.uuid4())
    user_row = conn.execute(
        "SELECT id FROM users WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    user_id = user_row["id"]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO api_keys (id, tenant_id, user_id, key_hash, key_prefix, "
        "name, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (key_id, tenant_id, user_id, key_hash, "icdev_te", "test-key", "active", now),
    )
    conn.commit()
    conn.close()
    print("3. API key created")

    # Create Flask test client
    from tools.saas.api_gateway import create_app

    app = create_app({"TESTING": True})
    client = app.test_client()

    HEADERS = {
        "Authorization": "Bearer " + key_raw,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    passed = 0
    failed = 0

    def test(name, condition, details=""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print("  PASS: %s" % name)
        else:
            failed += 1
            print("  FAIL: %s -- %s" % (name, details))

    # ---- Test Suite ----
    print()
    print("=== MCP Streamable HTTP E2E Tests ===")
    print()

    # T1: Health check
    r = client.get("/health")
    test("Health check returns 200", r.status_code == 200)

    # T2: POST without Accept header => 406
    r = client.post(
        "/mcp/v1/",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
        headers={
            "Authorization": "Bearer " + key_raw,
            "Content-Type": "application/json",
        },
    )
    test("Missing Accept header => 406", r.status_code == 406, "got %d" % r.status_code)

    # T3: Initialize (no session yet)
    r = client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        },
        headers=HEADERS,
    )
    test("Initialize returns 200", r.status_code == 200, "got %d" % r.status_code)
    resp = r.get_json()
    test(
        "Initialize has protocolVersion",
        resp.get("result", {}).get("protocolVersion") == "2025-03-26",
        str(resp),
    )
    session_id = r.headers.get("Mcp-Session-Id", "")
    test(
        "Initialize returns Mcp-Session-Id",
        len(session_id) == 64,
        "got len=%d" % len(session_id),
    )

    # T4: Send notification (initialized) => 202
    sess_headers = {**HEADERS, "Mcp-Session-Id": session_id}
    r = client.post(
        "/mcp/v1/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=sess_headers,
    )
    test("Notification returns 202", r.status_code == 202, "got %d" % r.status_code)

    # T5: Ping with session
    r = client.post(
        "/mcp/v1/",
        json={"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
        headers=sess_headers,
    )
    test("Ping returns 200", r.status_code == 200)
    resp = r.get_json()
    test(
        "Ping result is pong",
        resp.get("result", {}).get("status") == "pong",
        str(resp),
    )

    # T6: tools/list
    r = client.post(
        "/mcp/v1/",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
        headers=sess_headers,
    )
    test("tools/list returns 200", r.status_code == 200)
    resp = r.get_json()
    tools = resp.get("result", {}).get("tools", [])
    test("tools/list has 12 tools", len(tools) == 12, "got %d" % len(tools))

    # T7: tools/call (project_list)
    r = client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "project_list", "arguments": {}},
        },
        headers=sess_headers,
    )
    test("tools/call returns 200", r.status_code == 200)
    resp = r.get_json()
    test(
        "tools/call isError=False",
        resp.get("result", {}).get("isError") is False,
        str(resp),
    )

    # T8: tools/call with unknown tool
    r = client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        },
        headers=sess_headers,
    )
    resp = r.get_json()
    test(
        "Unknown tool returns isError=True",
        resp.get("result", {}).get("isError") is True,
        str(resp),
    )

    # T9: Unknown method
    r = client.post(
        "/mcp/v1/",
        json={"jsonrpc": "2.0", "id": 6, "method": "nonexistent/method", "params": {}},
        headers=sess_headers,
    )
    resp = r.get_json()
    test("Unknown method returns error", "error" in resp, str(resp))
    test(
        "Error code is -32601",
        resp.get("error", {}).get("code") == -32601,
        str(resp),
    )

    # T10: Batch request
    r = client.post(
        "/mcp/v1/",
        json=[
            {"jsonrpc": "2.0", "id": 10, "method": "ping", "params": {}},
            {"jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {}},
        ],
        headers=sess_headers,
    )
    test("Batch returns 200", r.status_code == 200)
    resp = r.get_json()
    test(
        "Batch returns array of 2",
        isinstance(resp, list) and len(resp) == 2,
        "got %s" % type(resp),
    )

    # T11: Invalid JSON-RPC (missing jsonrpc field)
    r = client.post(
        "/mcp/v1/",
        json={"id": 1, "method": "ping"},
        headers=sess_headers,
    )
    test("Invalid JSON-RPC returns 400", r.status_code == 400, "got %d" % r.status_code)

    # T12: POST without auth => 401
    r = client.post(
        "/mcp/v1/",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    test("No auth returns 401", r.status_code == 401, "got %d" % r.status_code)

    # T13: GET notification stream without session => 400
    r = client.get("/mcp/v1/", headers=HEADERS)
    test(
        "GET without session returns 400",
        r.status_code == 400,
        "got %d" % r.status_code,
    )

    # T14: DELETE session
    r = client.delete(
        "/mcp/v1/", headers={**HEADERS, "Mcp-Session-Id": session_id}
    )
    test("DELETE returns 204", r.status_code == 204, "got %d" % r.status_code)

    # T15: POST with destroyed session => 400
    r = client.post(
        "/mcp/v1/",
        json={"jsonrpc": "2.0", "id": 99, "method": "ping", "params": {}},
        headers={**HEADERS, "Mcp-Session-Id": session_id},
    )
    test(
        "Destroyed session returns 400",
        r.status_code == 400,
        "got %d" % r.status_code,
    )

    # T16: DELETE already-destroyed session => 204 (idempotent)
    r = client.delete(
        "/mcp/v1/", headers={**HEADERS, "Mcp-Session-Id": session_id}
    )
    test(
        "Delete idempotent returns 204",
        r.status_code == 204,
        "got %d" % r.status_code,
    )

    # T17: Convenience tools endpoint still works
    r = client.get("/mcp/v1/tools", headers=HEADERS)
    test("GET /mcp/v1/tools returns 200", r.status_code == 200)
    resp = r.get_json()
    test("Tools endpoint has 12 tools", resp.get("total") == 12, "got %s" % resp.get("total"))

    # T18: CUI headers present (value depends on CLASSIFICATION env var)
    x_class = r.headers.get("X-Classification", "")
    test(
        "X-Classification header present",
        x_class.startswith("CUI"),
        "got %s" % x_class,
    )

    # T19: Wildcard Accept header works
    r2 = client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test2", "version": "1.0"},
            },
        },
        headers={
            "Authorization": "Bearer " + key_raw,
            "Content-Type": "application/json",
            "Accept": "*/*",
        },
    )
    test("Wildcard Accept works", r2.status_code == 200, "got %d" % r2.status_code)
    session2 = r2.headers.get("Mcp-Session-Id", "")
    # cleanup
    if session2:
        client.delete("/mcp/v1/", headers={**HEADERS, "Mcp-Session-Id": session2})

    # T20: DELETE without Mcp-Session-Id => 400
    r = client.delete("/mcp/v1/", headers=HEADERS)
    test("DELETE without session => 400", r.status_code == 400, "got %d" % r.status_code)

    print()
    print("=== Results: %d passed, %d failed ===" % (passed, failed))
    if failed:
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
