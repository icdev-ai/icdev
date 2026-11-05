#!/usr/bin/env python3
# CUI // SP-CTI
"""Toolset profiles on the SaaS MCP Streamable HTTP surface (owui-mcp-01).

A chat client (Open WebUI) selects ``chat_readonly`` with ``?toolset=`` or the
``X-ICDEV-Toolset`` header and is offered -- and may call -- only that
profile's read-only tools. The profile NARROWS the role-authorized set and
never widens it; an unknown profile is refused rather than answered with the
full list. Principal set from headers as in
``tests/test_exa_policy_05_saas_mcp_authz.py``; tool execution is stubbed.
"""

import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask, g, request  # noqa: E402

from tools.mcp.toolset_profiles import resolve_toolset  # noqa: E402
from tools.saas import mcp_http  # noqa: E402

MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
CHAT_READONLY = {"project_list", "project_status", "nist_lookup"}


@pytest.fixture(autouse=True)
def no_audit_writes(monkeypatch):
    monkeypatch.setattr(mcp_http, "_write_authz_audit", lambda *a, **k: None)


@pytest.fixture
def dispatched(monkeypatch):
    calls = []

    def _fake(name, arguments, tenant_id, user_role=None, user_id=""):
        calls.append(name)
        return {"tool": name, "ok": True}

    monkeypatch.setattr(mcp_http, "_dispatch_tool", _fake)
    return calls


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _set_principal():
        g.tenant_id = "tenant-1"
        g.user_id = "user-1"
        g.user_role = request.headers.get("X-Test-Role", "admin")

    app.register_blueprint(mcp_http.mcp_bp)
    return app.test_client()


def _post(client, method, params=None, url="/mcp/v1/", headers=None, session=True):
    hdrs = dict(MCP_HEADERS, **(headers or {}))
    if session:
        init = client.post(url, data=json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                                 "params": {}}), headers=hdrs)
        hdrs["Mcp-Session-Id"] = init.headers.get("Mcp-Session-Id", "")
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    resp = client.post(url, data=json.dumps(body), headers=hdrs)
    return resp, json.loads(resp.data)


def _listed(body):
    return {t["name"] for t in body["result"]["tools"]}


def test_chat_readonly_profile_is_exactly_the_read_only_http_tools():
    resolved = resolve_toolset("chat_readonly", registry_names=set(mcp_http._TOOL_MAP))
    assert resolved == CHAT_READONLY, "a profile entry is missing from the HTTP registry"


def test_no_profile_leaves_the_list_unchanged(client):
    _, body = _post(client, "tools/list")
    assert _listed(body) == set(mcp_http._TOOL_MAP)


@pytest.mark.parametrize("how", ["query", "header"])
def test_profile_narrows_tools_list(client, how):
    url = "/mcp/v1/?toolset=chat_readonly" if how == "query" else "/mcp/v1/"
    headers = {"X-ICDEV-Toolset": "chat_readonly"} if how == "header" else None
    _, body = _post(client, "tools/list", url=url, headers=headers)
    assert _listed(body) == CHAT_READONLY


def test_call_outside_profile_is_refused_and_never_dispatched(client, dispatched):
    _, body = _post(client, "tools/call", {"name": "ssp_generate", "arguments": {}},
                    url="/mcp/v1/?toolset=chat_readonly")
    assert body["error"]["code"] == mcp_http.JSONRPC_UNAUTHORIZED
    assert body["error"]["data"]["reason"] == "outside_toolset_profile"
    assert dispatched == []


def test_call_inside_profile_is_dispatched(client, dispatched):
    _, body = _post(client, "tools/call", {"name": "nist_lookup", "arguments": {"control_id": "AC-2"}},
                    url="/mcp/v1/?toolset=chat_readonly")
    assert body["result"]["isError"] is False
    assert dispatched == ["nist_lookup"]


def test_unknown_profile_is_refused_not_answered_with_everything(client, dispatched):
    resp, body = _post(client, "tools/list", url="/mcp/v1/?toolset=everything-please", session=False)
    assert resp.status_code == 400
    assert "Unknown toolset profile" in body["error"]["message"]
    assert dispatched == []


def test_profile_never_widens_what_the_role_may_use(client, monkeypatch):
    # Enforced denial of a profile tool: the profile must not bring it back.
    real = mcp_http.authorize_tool

    def _deny_status(tool_name, user_role):
        if tool_name == "project_status":
            return {"allowed": False, "enforced": True, "role": user_role, "reason": "test deny"}
        return real(tool_name, user_role)

    monkeypatch.setattr(mcp_http, "authorize_tool", _deny_status)
    _, body = _post(client, "tools/list", url="/mcp/v1/?toolset=chat_readonly")
    assert _listed(body) == CHAT_READONLY - {"project_status"}
