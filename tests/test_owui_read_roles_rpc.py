"""owui-idp-03 / owui-idp-04: the read-only SaaS roles on an RPC-over-POST gateway.

Measured on the five-role Open WebUI demo (2026-11-07), real Keycloak tokens: auditor and
viewer listed /v1/models and were 403 on everything else -- tools.saas.auth.rbac admitted
only GET for an 'R' permission, while a chat completion and every MCP Streamable HTTP call
is a POST. Operator decision: read roles chat, and reach read-only MCP tools only while
per-tool authorization enforces (tools.saas.mcp_http.get_authz_mode). REST writes stay refused.

The same refusals left no audit row: tools.saas.auth.middleware._log_auth_event returned
early whenever data/platform.db was absent, i.e. always on PostgreSQL.
"""
import pytest

from icdev.tools.saas import mcp_http
from icdev.tools.saas.auth import middleware, rbac

READ_ROLES = ("auditor", "viewer")
WRITE_ROLES = ("tenant_admin", "developer", "compliance_officer")


@pytest.mark.parametrize("role", READ_ROLES)
def test_read_roles_may_chat(role):
    assert rbac.require_permission(role, "/v1/chat/completions", "POST")


@pytest.mark.parametrize("role", READ_ROLES)
def test_read_roles_reach_mcp_only_under_enforce(role, monkeypatch):
    monkeypatch.setenv(mcp_http.AUTHZ_MODE_ENV, "enforce")
    assert rbac.require_permission(role, "/mcp/v1/", "POST")
    monkeypatch.setenv(mcp_http.AUTHZ_MODE_ENV, "monitor")
    assert not rbac.require_permission(role, "/mcp/v1/", "POST")


@pytest.mark.parametrize("role", READ_ROLES)
@pytest.mark.parametrize("path", ("/api/v1/projects", "/api/v1/projects/p-1/ssp", "/api/v1/projects/p-1/scan"))
@pytest.mark.parametrize("method", ("POST", "PUT", "DELETE"))
def test_read_roles_rest_writes_still_refused(role, path, method, monkeypatch):
    monkeypatch.setenv(mcp_http.AUTHZ_MODE_ENV, "enforce")
    assert not rbac.require_permission(role, path, method)


@pytest.mark.parametrize("role", WRITE_ROLES)
@pytest.mark.parametrize("mode", ("monitor", "enforce"))
def test_write_roles_unchanged(role, mode, monkeypatch):
    monkeypatch.setenv(mcp_http.AUTHZ_MODE_ENV, mode)
    assert rbac.require_permission(role, "/v1/chat/completions", "POST")
    assert rbac.require_permission(role, "/mcp/v1/", "POST")


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params):
        self.rows.append(params)

    def commit(self):
        pass

    def close(self):
        pass


def test_auth_refusal_is_audited_on_postgresql(monkeypatch, tmp_path):
    rows = []
    monkeypatch.setattr(middleware, "get_connection", lambda: _Conn(rows))
    monkeypatch.setenv("PLATFORM_DB_PATH", str(tmp_path / "absent.db"))
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://x@localhost/platform")
    middleware._log_auth_event("t-1", "u-1", "auth.forbidden", {"action": "permission_denied"}, "127.0.0.1")
    assert len(rows) == 1 and rows[0][2] == "auth.forbidden"


def test_no_platform_db_at_all_still_writes_nothing(monkeypatch, tmp_path):
    rows = []
    monkeypatch.setattr(middleware, "get_connection", lambda: _Conn(rows))
    monkeypatch.setenv("PLATFORM_DB_PATH", str(tmp_path / "absent.db"))
    monkeypatch.delenv("PLATFORM_DB_URL", raising=False)
    middleware._log_auth_event("t-1", "u-1", "auth.forbidden", {"action": "permission_denied"}, "127.0.0.1")
    assert rows == []
