#!/usr/bin/env python3
# CUI // SP-CTI
"""Per-call MCP audit rows tagged by client, and the enforce survey (owui-mcp-02).

Arming ``MCPToolAuthorizer`` for Open WebUI needs a measured would-deny RATE,
which needs a row for every call -- not only the denials. These tests pin the
row, the caller tag, and the survey's refusal to call an empty history safe.
"""

import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask, g, request  # noqa: E402

from tools.saas import mcp_authz_survey as survey  # noqa: E402
from tools.saas import mcp_http  # noqa: E402

MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


@pytest.fixture
def rows(monkeypatch):
    out = []
    monkeypatch.setattr(mcp_http, "_write_authz_audit",
                        lambda decision, tenant_id, user_id, action: out.append((action, decision)))
    monkeypatch.setattr(mcp_http, "_load_tool_func", lambda entry: (lambda **kw: {"ok": True}))
    import importlib

    adapter = importlib.import_module("tools.saas.tenant_db_adapter")
    monkeypatch.setattr(adapter, "call_tool_with_tenant_db", lambda func, tenant_id, **kw: func(**kw))
    return out


@pytest.fixture
def client():
    app = Flask(__name__)

    @app.before_request
    def _p():
        g.tenant_id, g.user_id = "tenant-1", "user-1"
        g.user_role = request.headers.get("X-Test-Role", "admin")

    app.register_blueprint(mcp_http.mcp_bp)
    return app.test_client()


def _call(client, tool, headers=None):
    hdrs = dict(MCP_HEADERS, **(headers or {}))
    init = client.post("/mcp/v1/", data=json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                                    "params": {}}), headers=hdrs)
    hdrs["Mcp-Session-Id"] = init.headers.get("Mcp-Session-Id", "")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": {}}}
    return client.post("/mcp/v1/", data=json.dumps(body), headers=hdrs)


def test_every_tools_call_writes_one_tagged_row(client, rows):
    _call(client, "nist_lookup", {"X-ICDEV-Client": "OpenWebUI"})
    calls = [d for a, d in rows if a == "mcp.tool.call"]
    assert len(calls) == 1
    assert calls[0]["caller_source"] == "openwebui"
    assert calls[0]["tool"] == "nist_lookup"


def test_untagged_caller_is_unknown_and_header_is_sanitised(client, rows):
    _call(client, "nist_lookup")
    _call(client, "nist_lookup", {"X-ICDEV-Client": "evil\"; DROP TABLE x;--"})
    sources = [d["caller_source"] for a, d in rows if a == "mcp.tool.call"]
    assert sources == ["unknown", "evildroptablex--"]


def _row(caller, allowed, enforced, tool="t"):
    return {"details": json.dumps({"caller_source": caller, "allowed": allowed,
                                   "enforced": enforced, "tool": tool})}


def test_survey_counts_one_caller_only():
    report = survey.summarize([
        _row("openwebui", True, False), _row("openwebui", False, False, "ssp_generate"),
        _row("openwebui", False, True), _row("goose", False, False),
    ], "openwebui")
    assert (report["calls"], report["allowed"], report["would_deny"], report["denied"]) == (3, 1, 1, 1)
    assert report["would_deny_rate"] == round(1 / 3, 4)
    assert report["would_deny_by_tool"] == {"ssp_generate": 1}


def test_empty_history_is_unmeasurable_not_safe(capsys):
    class Conn:
        def execute(self, *a):
            return self

        def fetchall(self):
            return []

        def close(self):
            pass

    assert survey.main(["--caller", "openwebui", "--json"], get_conn=Conn) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["measurable"] is False
    assert report["would_deny_rate"] is None


def test_unreadable_table_is_unmeasurable(capsys):
    def broken():
        raise RuntimeError("no such table: audit_platform")

    assert survey.main(["--json"], get_conn=broken) == 2
