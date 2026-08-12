#!/usr/bin/env python3
# CUI // SP-CTI
"""Per-tool authorization on the SaaS MCP HTTP surface (exa-policy-05).

``tools/saas/mcp_http.py`` is the only MCP surface in the tree with a real
authenticated principal -- the gateway middleware sets ``g.tenant_id``,
``g.user_id`` and ``g.user_role`` before the blueprint runs.  These tests
prove that an authenticated tenant cannot dispatch a tool their role
disallows, and that monitor mode records the would-be denial instead.

The Flask app here registers the blueprint behind a stub ``before_request``
that sets ``g`` from headers.  That is deliberate: the real middleware is
already covered elsewhere, and this file must test the authorization decision
rather than re-test authentication.
"""

import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask, g, request  # noqa: E402

from tools.saas import mcp_http  # noqa: E402

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def audited(monkeypatch):
    """Collect authorization audit rows instead of writing to the platform DB."""
    rows = []
    monkeypatch.setattr(
        mcp_http,
        "_write_authz_audit",
        lambda decision, tenant_id, user_id, action: rows.append(
            {"decision": decision, "tenant_id": tenant_id, "user_id": user_id, "action": action}
        ),
    )
    return rows


@pytest.fixture
def enforce(monkeypatch):
    monkeypatch.setenv(mcp_http.AUTHZ_MODE_ENV, mcp_http.AUTHZ_MODE_ENFORCE)


@pytest.fixture
def monitor(monkeypatch):
    monkeypatch.setenv(mcp_http.AUTHZ_MODE_ENV, mcp_http.AUTHZ_MODE_MONITOR)


@pytest.fixture
def client():
    """Flask test client with an authenticated principal taken from headers."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _set_principal():
        g.tenant_id = request.headers.get("X-Test-Tenant", "tenant-1")
        g.user_id = request.headers.get("X-Test-User", "user-1")
        g.user_role = request.headers.get("X-Test-Role", "viewer")

    app.register_blueprint(mcp_http.mcp_bp)
    return app.test_client()



def _patch_get_connection(monkeypatch, factory):
    """Patch ``get_connection`` where ``_write_authz_audit`` actually reads it.

    ``tools/`` is a backward-compat shim over ``icdev.tools``, and
    ``import tools.db.storage as st`` does NOT bind the same object as
    ``sys.modules["tools.db.storage"]`` -- patching the former leaves the real
    connection in place and the test silently exercises the live database.
    ``importlib.import_module`` resolves to the sys.modules entry, which is
    what the function-local ``from tools.db.storage import get_connection``
    reads.
    """
    import importlib

    module = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(module, "get_connection", factory)


def _rpc(client, role, method, params=None, rpc_id=1):
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    headers = dict(MCP_HEADERS)
    headers["X-Test-Role"] = role
    resp = client.post("/mcp/v1/", data=json.dumps(body), headers=headers)
    return resp, json.loads(resp.data)


def _session(client, role):
    """Run the initialize handshake and return the Mcp-Session-Id."""
    resp, _ = _rpc(client, role, "initialize", {})
    return resp.headers.get("Mcp-Session-Id", "")


def _call(client, role, tool, arguments=None, session_id=None):
    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    }
    headers = dict(MCP_HEADERS)
    headers["X-Test-Role"] = role
    headers["Mcp-Session-Id"] = session_id or _session(client, role)
    resp = client.post("/mcp/v1/", data=json.dumps(body), headers=headers)
    return resp, json.loads(resp.data)


# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------
def test_default_mode_is_monitor(monkeypatch):
    """Shipped default is monitor -- enforcement is switched on deliberately."""
    monkeypatch.delenv(mcp_http.AUTHZ_MODE_ENV, raising=False)
    assert mcp_http.get_authz_mode() == mcp_http.AUTHZ_MODE_MONITOR
    assert mcp_http.DEFAULT_AUTHZ_MODE == mcp_http.AUTHZ_MODE_MONITOR


def test_unrecognized_mode_falls_back_to_monitor(monkeypatch):
    """A typo in the env var must not silently disable the audit trail."""
    monkeypatch.setenv(mcp_http.AUTHZ_MODE_ENV, "enfroce")
    assert mcp_http.get_authz_mode() == mcp_http.AUTHZ_MODE_MONITOR


# ---------------------------------------------------------------------------
# The decision itself -- one DENY per role
# ---------------------------------------------------------------------------
#: (SaaS role, a registry tool that role must not be able to dispatch).
#: Every SaaS role in tools/saas/models.py::UserRole that can be denied at all
#: appears here.  ``tenant_admin`` is absent because it maps to the D261
#: ``admin`` role, whose allow list is a bare wildcard -- see
#: test_tenant_admin_is_allowed_by_wildcard, which pins that as a deliberate
#: policy outcome rather than a gap in this table.
DENY_CASES = [
    ("developer", "ssp_generate"),  # explicit deny rule
    ("developer", "sbom_generate"),  # no matching rule -> default deny
    ("compliance_officer", "project_create"),  # isso: not on the allow list
    ("isso", "project_create"),
    ("viewer", "project_create"),  # unmapped role -> unknown -> default deny
    ("viewer", "sast_scan"),
    ("auditor", "ssp_generate"),  # unmapped role -> unknown -> default deny
    ("", "project_list"),  # no role at all is never treated as admin
]


@pytest.mark.parametrize("role,tool", DENY_CASES)
def test_authorize_tool_denies(role, tool, enforce):
    decision = mcp_http.authorize_tool(tool, role)
    assert decision["allowed"] is False, decision
    assert decision["enforced"] is True
    assert decision["reason"]


@pytest.mark.parametrize("role,tool", DENY_CASES)
def test_authenticated_tenant_cannot_dispatch_denied_tool(role, tool, client, enforce, audited):
    """The acceptance criterion: authenticated, and still refused."""
    resp, body = _call(client, role, tool)
    assert resp.status_code == 200
    assert "error" in body, body
    assert body["error"]["code"] == mcp_http.JSONRPC_UNAUTHORIZED
    assert tool in body["error"]["message"]
    # A refusal must not be dressed up as a tool result.
    assert "result" not in body
    assert any(r["action"] == "mcp.tool.denied" for r in audited), audited


@pytest.mark.parametrize(
    "role,tool",
    [
        ("tenant_admin", "ssp_generate"),
        ("developer", "run_tests"),
        ("isso", "stig_check"),
        ("compliance_officer", "nist_lookup"),
    ],
)
def test_authorize_tool_allows(role, tool, enforce):
    assert mcp_http.authorize_tool(tool, role)["allowed"] is True


def test_tenant_admin_is_allowed_by_wildcard(enforce):
    """``tenant_admin`` maps to D261 ``admin``, whose allow list is ``["*"]``.

    There is therefore no tool it can be denied, which is why it has no entry
    in DENY_CASES.  Pinned as a test so that if the matrix ever narrows admin,
    this becomes a visible failure rather than a silently missing deny case.
    """
    for entry in mcp_http.TOOL_REGISTRY:
        assert mcp_http.authorize_tool(entry["name"], "tenant_admin")["allowed"] is True


def test_dispatch_raises_before_importing_the_tool(monkeypatch, enforce, audited):
    """Denial happens before the tool module is imported, not after."""
    loaded = []
    monkeypatch.setattr(mcp_http, "_load_tool_func", lambda entry: loaded.append(entry))
    with pytest.raises(mcp_http.MCPAuthorizationError):
        mcp_http._dispatch_tool(
            "ssp_generate", {}, "tenant-1", user_role="developer", user_id="user-1"
        )
    assert loaded == []


def test_unknown_tool_still_raises_value_error(enforce):
    with pytest.raises(ValueError):
        mcp_http._dispatch_tool(
            "no_such_tool", {}, "tenant-1", user_role="tenant_admin", user_id="user-1"
        )


# ---------------------------------------------------------------------------
# tools/list -- do not advertise what the caller may not call
# ---------------------------------------------------------------------------
def test_tools_list_is_filtered_by_role(client, enforce, audited):
    session_id = _session(client, "developer")
    headers = dict(MCP_HEADERS)
    headers["X-Test-Role"] = "developer"
    headers["Mcp-Session-Id"] = session_id
    resp = client.post(
        "/mcp/v1/",
        data=json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        headers=headers,
    )
    names = [t["name"] for t in json.loads(resp.data)["result"]["tools"]]
    assert "ssp_generate" not in names
    assert len(names) < len(mcp_http.TOOL_REGISTRY)


def test_tools_list_unfiltered_for_admin(client, enforce):
    session_id = _session(client, "tenant_admin")
    headers = dict(MCP_HEADERS)
    headers["X-Test-Role"] = "tenant_admin"
    headers["Mcp-Session-Id"] = session_id
    resp = client.post(
        "/mcp/v1/",
        data=json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        headers=headers,
    )
    names = [t["name"] for t in json.loads(resp.data)["result"]["tools"]]
    assert len(names) == len(mcp_http.TOOL_REGISTRY)


def test_convenience_tools_endpoint_is_filtered_too(client, enforce, audited):
    """GET /mcp/v1/tools must not route around the tools/list filter."""
    resp = client.get("/mcp/v1/tools", headers={"X-Test-Role": "developer"})
    names = [t["name"] for t in json.loads(resp.data)["tools"]]
    assert "ssp_generate" not in names
    # Audited under its own surface label, so the two discovery paths stay
    # distinguishable in the evidence used to decide when to flip to enforce.
    assert [r["decision"]["surface"] for r in audited] == ["GET /mcp/v1/tools"]

    resp = client.get("/mcp/v1/tools", headers={"X-Test-Role": "tenant_admin"})
    names = [t["name"] for t in json.loads(resp.data)["tools"]]
    assert len(names) == len(mcp_http.TOOL_REGISTRY)


def test_list_filter_is_audited_as_one_row_not_one_per_tool(client, enforce, audited):
    """A deny-all role must not emit an audit row per tool on every list call.

    ``viewer`` is denied all 19 registry tools.  One row per withheld tool
    would be 19 DB round-trips on a read, and would bury the ``tools/call``
    denials that actually matter.
    """
    session_id = _session(client, "viewer")
    headers = dict(MCP_HEADERS)
    headers["X-Test-Role"] = "viewer"
    headers["Mcp-Session-Id"] = session_id
    resp = client.post(
        "/mcp/v1/",
        data=json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        headers=headers,
    )
    assert json.loads(resp.data)["result"]["tools"] == []
    assert len(audited) == 1, audited
    row = audited[0]
    assert row["action"] == "mcp.tools_list.filtered"
    assert row["decision"]["withheld_count"] == len(mcp_http.TOOL_REGISTRY)
    assert len(row["decision"]["withheld"]) == len(mcp_http.TOOL_REGISTRY)


def test_admin_list_writes_no_audit_row(client, enforce, audited):
    """Nothing withheld means nothing to record."""
    session_id = _session(client, "tenant_admin")
    headers = dict(MCP_HEADERS)
    headers["X-Test-Role"] = "tenant_admin"
    headers["Mcp-Session-Id"] = session_id
    client.post(
        "/mcp/v1/",
        data=json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        headers=headers,
    )
    assert audited == []


# ---------------------------------------------------------------------------
# Monitor mode -- log the would-be denial, let the call through
# ---------------------------------------------------------------------------
def test_monitor_mode_logs_would_be_denial_without_blocking(monitor, audited):
    decision = mcp_http.authorize_tool("ssp_generate", "developer")
    assert decision["allowed"] is False
    assert decision["enforced"] is False

    mcp_http.log_authz_decision(decision, "tenant-1", "user-1", surface="tools/call")
    assert len(audited) == 1
    assert audited[0]["action"] == "mcp.tool.would_deny"
    assert audited[0]["decision"]["tool"] == "ssp_generate"
    assert audited[0]["decision"]["mode"] == mcp_http.AUTHZ_MODE_MONITOR


def test_monitor_mode_does_not_refuse_the_dispatch(monkeypatch, monitor, audited):
    """Same call that is refused under enforce runs under monitor."""
    monkeypatch.setattr(mcp_http, "_load_tool_func", lambda entry: (lambda **kw: {"ok": True}))
    monkeypatch.setattr(
        "tools.saas.tenant_db_adapter.call_tool_with_tenant_db",
        lambda func, tenant_id, **kw: func(**kw),
    )
    result = mcp_http._dispatch_tool(
        "ssp_generate", {}, "tenant-1", user_role="developer", user_id="user-1"
    )
    assert result == {"ok": True}
    assert [r["action"] for r in audited] == ["mcp.tool.would_deny"]


def test_monitor_mode_does_not_hide_tools(client, monitor, audited):
    """Shrinking a tenant's tool list is itself a change worth measuring first."""
    session_id = _session(client, "developer")
    headers = dict(MCP_HEADERS)
    headers["X-Test-Role"] = "developer"
    headers["Mcp-Session-Id"] = session_id
    resp = client.post(
        "/mcp/v1/",
        data=json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        headers=headers,
    )
    names = [t["name"] for t in json.loads(resp.data)["result"]["tools"]]
    assert len(names) == len(mcp_http.TOOL_REGISTRY)
    # ...but every omission it *would* have made is on the record, as ONE row
    # naming them all rather than a row (and a DB round-trip) per tool.
    assert len(audited) == 1
    assert audited[0]["action"] == "mcp.tools_list.would_filter"
    assert "ssp_generate" in audited[0]["decision"]["withheld"]
    assert audited[0]["decision"]["surface"] == "tools/list"


def test_authz_audit_insert_matches_the_live_schema(monitor):
    """Exercise the REAL audit write, not the stub the other tests install.

    ``_write_authz_audit`` swallows its own exceptions so an audit outage
    cannot 500 a call the policy allowed -- which means a column that does not
    exist in the live schema would make every monitor-mode row vanish
    silently, and monitor mode would report a clean board while recording
    nothing.  This is the test that would catch that.
    """
    import uuid as _uuid

    from tools.db.storage import get_connection

    try:
        conn = get_connection()
    except Exception as exc:  # pragma: no cover - no DB configured
        pytest.skip("no database available: {}".format(exc))

    try:
        conn.execute("SELECT 1 FROM audit_platform LIMIT 1")
    except Exception:
        conn.close()
        pytest.skip("audit_platform not present in this database")

    tenant_id = str(_uuid.uuid4())
    user_id = str(_uuid.uuid4())
    conn.close()

    # The audit trail is append-only, so this row cannot be cleaned up after
    # the test.  Name the surface after the test so a later reader grepping
    # audit_platform for real denials can tell test residue from a finding.
    surface = "tests/exa-policy-05-schema-parity"
    decision = mcp_http.authorize_tool("ssp_generate", "developer")
    mcp_http.log_authz_decision(decision, tenant_id, user_id, surface=surface)

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT event_type, action, details FROM audit_platform WHERE tenant_id = %s",
            (tenant_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "monitor-mode denial was not persisted"
    assert row[0] == "mcp.authz"
    assert row[1] == "mcp.tool.would_deny"
    details = row[2] if isinstance(row[2], dict) else json.loads(row[2])
    assert details["tool"] == "ssp_generate"
    assert details["surface"] == surface


def test_failed_audit_write_rolls_back_and_returns_the_connection(monkeypatch, monitor):
    """A swallowed audit failure must not poison the connection pool.

    ``get_connection`` hands out pooled PostgreSQL connections and ``close()``
    is ``putconn()``.  Swallowing the exception without rolling back would
    either leak the connection until the pool is exhausted or hand it back
    mid-abort, so the next borrower fails on unrelated queries.
    """
    calls = []

    class _BrokenConn:
        def execute(self, *a, **kw):
            raise RuntimeError("audit table is gone")

        def commit(self):
            calls.append("commit")

        def rollback(self):
            calls.append("rollback")

        def close(self):
            calls.append("close")

    _patch_get_connection(monkeypatch, lambda *a, **kw: _BrokenConn())

    # Must not raise -- an audit outage cannot 500 a call the policy allowed.
    mcp_http._write_authz_audit({"tool": "ssp_generate"}, "t", "u", "mcp.tool.would_deny")

    assert calls == ["rollback", "close"]


def test_audit_write_closes_the_connection_on_success(monkeypatch, monitor):
    calls = []

    class _OkConn:
        def execute(self, *a, **kw):
            calls.append("execute")

        def commit(self):
            calls.append("commit")

        def close(self):
            calls.append("close")

    _patch_get_connection(monkeypatch, lambda *a, **kw: _OkConn())
    mcp_http._write_authz_audit({"tool": "ssp_generate"}, "t", "u", "mcp.tool.would_deny")
    assert calls == ["execute", "commit", "close"]


def test_allowed_calls_are_not_audited_as_denials(monitor, audited):
    mcp_http.log_authz_decision(
        mcp_http.authorize_tool("ssp_generate", "tenant_admin"), "tenant-1", "user-1", "tools/call"
    )
    assert audited == []


# ---------------------------------------------------------------------------
# The stdio surface is deliberately unenforced -- keep the reason on the record
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "module_path",
    [
        "tools/mcp/unified_server.py",
        "tools/mcp/base_server.py",
        # The icdev/ mirror is what a pip-installed deployment actually reads,
        # so the reasoning has to survive the copy, not just live in tools/.
        "icdev/tools/mcp/unified_server.py",
        "icdev/tools/mcp/base_server.py",
    ],
)
def test_stdio_non_enforcement_is_documented(module_path):
    """A future session must find the reasoning, not re-derive it."""
    text = (BASE_DIR / module_path).read_text(encoding="utf-8")
    docstring = text.split('"""')[1]
    assert "exa-policy-05" in docstring
    assert "mcp_http" in docstring
    lowered = docstring.lower()
    assert "no principal" in lowered or "no caller identity" in lowered


def test_no_second_gate_beside_agent_tool_gate():
    """Studio keeps exactly one authorization gate (AGENT-WF-001)."""
    executors = BASE_DIR / "tools" / "studio" / "executors"
    gates = sorted(p.name for p in executors.glob("*tool_gate*.py"))
    assert gates == ["agent_tool_gate.py"], gates


def test_mcp_http_does_not_fork_the_role_matrix():
    """The decision stays in MCPToolAuthorizer; this module only maps roles."""
    text = (BASE_DIR / "tools" / "saas" / "mcp_http.py").read_text(encoding="utf-8")
    assert "MCPToolAuthorizer" in text
    assert "role_tool_matrix" not in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
