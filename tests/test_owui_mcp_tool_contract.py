"""owui-mcp-05: the MCP tools a chat client sees must actually execute.

Measured on the governed Open WebUI demo (2026-11-07): authorization passed, then
every chat_readonly tool failed behind it -- nist_lookup named a function its module
does not define, and project_list reported an ACTIVE tenant as "not found or not
active" because the tenant lookup demanded a SQLite platform.db on a PostgreSQL
deployment.  The model then answered from memory, wrongly.

Covers tools.saas.mcp_http and tools.saas.mcp_sse (TOOL_REGISTRY contract) and
tools.saas.tenant_db_adapter (platform lookup on PostgreSQL).
"""
import importlib
import inspect

import pytest

from icdev.tools.saas import mcp_http, mcp_sse, tenant_db_adapter

_INJECTED = {"db_path", "db", "database"}


def _entries():
    for mod in (mcp_http, mcp_sse):
        for entry in mod.TOOL_REGISTRY:
            yield pytest.param(entry, id=f"{mod.__name__.rsplit('.', 1)[-1]}:{entry['name']}")


@pytest.mark.parametrize("entry", list(_entries()))
def test_registry_entry_matches_its_function(entry):
    """Every advertised argument is accepted, and every required one is advertised."""
    func = getattr(importlib.import_module(entry["module"]), entry["function"])
    sig = inspect.signature(func)
    params = sig.parameters
    takes_kwargs = any(p.kind is p.VAR_KEYWORD for p in params.values())
    props = set(entry["inputSchema"].get("properties", {}))
    if not takes_kwargs:
        assert props <= set(params), f"schema advertises {sorted(props - set(params))}"
    needed = {
        n for n, p in params.items()
        if p.default is p.empty and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    } - _INJECTED
    assert needed <= props, f"function requires {sorted(needed - props)}"


def test_nist_lookup_dispatches_to_the_control(monkeypatch):
    monkeypatch.setattr(mcp_http, "record_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(tenant_db_adapter, "_get_tenant_config",
                        lambda tid: {"slug": "t", "db_name": None})
    result = mcp_http._dispatch_tool("nist_lookup", {"control_id": "AC-2"}, "t", "developer")
    assert result and result["id"].upper() == "AC-2"


class _Row(dict):
    pass


class _Conn:
    def __init__(self, row):
        self.row = row

    def execute(self, sql, params=()):
        assert "FROM tenants" in sql
        return self

    def fetchone(self):
        return self.row

    def close(self):
        pass


def test_tenant_lookup_works_on_postgres_without_sqlite_file(monkeypatch, tmp_path):
    """A PG platform DB (PLATFORM_DB_URL) must not require data/platform.db to exist."""
    row = _Row(id="t1", slug="demo", db_host=None, db_name=None, db_port=None,
               status="active", impact_level="IL4", tier="professional")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setattr(tenant_db_adapter, "PLATFORM_DB_PATH", tmp_path / "absent.db")
    monkeypatch.setattr(tenant_db_adapter, "get_connection", lambda *a, **k: _Conn(row))
    tenant_db_adapter._cache_clear()
    try:
        assert tenant_db_adapter._get_tenant_config("t1")["slug"] == "demo"
    finally:
        tenant_db_adapter._cache_clear()


def test_tenant_lookup_still_requires_sqlite_file_without_pg(monkeypatch, tmp_path):
    monkeypatch.delenv("PLATFORM_DB_URL", raising=False)
    monkeypatch.setattr(tenant_db_adapter, "PLATFORM_DB_PATH", tmp_path / "absent.db")
    with pytest.raises(FileNotFoundError):
        tenant_db_adapter._get_platform_conn()
