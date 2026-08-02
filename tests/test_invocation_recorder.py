# CUI // SP-CTI
"""Tests for runtime invocation telemetry across MCP / agent / persona / role.

The load-bearing assertions are the negative ones: that wrapping a call never
changes its behaviour, and that argument VALUES never reach the database.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tools.observability import invocation_recorder as R

_MIGRATION = (Path(__file__).resolve().parent.parent
              / "tools/db/migrations/341_runtime_invocations/up.py")


@pytest.fixture()
def obs_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with migration 341 applied."""
    db = tmp_path / "obs.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.delenv("ICDEV_OBS_INVOCATIONS", raising=False)
    monkeypatch.setattr(R, "_table_missing", False, raising=False)

    import tools.db.storage as storage

    monkeypatch.setattr(storage, "DB_PATH", str(db), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)

    spec = importlib.util.spec_from_file_location("m341", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.up()
    return db


def _rows():
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT surface, name, status, duration_ms, arg_keys, error_class, "
            "       parent_id FROM runtime_invocations ORDER BY started_at"
        ).fetchall()]
    finally:
        conn.close()


# --------------------------------------------------------------- core contract

def test_success_is_recorded_with_duration(obs_db):
    with R.record(R.SURFACE_MCP, "rag_search", arg_keys={"query": "q", "top_k": 5}):
        pass
    row = _rows()[-1]
    assert row["surface"] == "mcp" and row["name"] == "rag_search"
    assert row["status"] == R.STATUS_OK
    assert row["duration_ms"] is not None and row["duration_ms"] >= 0


def test_argument_values_never_reach_the_database(obs_db):
    """The whole CUI posture rests on this."""
    secret = "TS-SCI-PAYLOAD-DO-NOT-STORE"
    with R.record(R.SURFACE_MCP, "t", arg_keys={"query": secret, "token": secret}):
        pass
    row = _rows()[-1]
    assert secret not in json.dumps(row), "an argument VALUE was persisted"
    assert json.loads(row["arg_keys"]) == ["query", "token"], "keys only"


def test_non_mapping_args_record_nothing_rather_than_guessing(obs_db):
    with R.record(R.SURFACE_MCP, "t", arg_keys="a bare string"):
        pass
    assert _rows()[-1]["arg_keys"] is None


def test_exception_is_recorded_and_re_raised_unchanged(obs_db):
    sentinel = ValueError("boom")
    with pytest.raises(ValueError) as caught:
        with R.record(R.SURFACE_ROLE, "role:step-1"):
            raise sentinel
    assert caught.value is sentinel, "the original exception object must propagate"
    row = _rows()[-1]
    assert row["status"] == R.STATUS_ERROR and row["error_class"] == "ValueError"


def test_telemetry_failure_does_not_break_the_wrapped_call(obs_db, monkeypatch):
    """A bug in the recorder must never take down the thing it observes."""
    def explode(*_a, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(R, "_open", explode)
    monkeypatch.setattr(R, "_close", explode)

    marker = {}
    with R.record(R.SURFACE_AGENT, "agent-x"):
        marker["ran"] = True
    assert marker["ran"], "the wrapped body did not run"


def test_disabled_by_env_writes_nothing(obs_db, monkeypatch):
    monkeypatch.setenv("ICDEV_OBS_INVOCATIONS", "0")
    before = len(_rows())
    with R.record(R.SURFACE_MCP, "should_not_record"):
        pass
    assert len(_rows()) == before


def test_missing_table_degrades_once_not_per_call(tmp_path, monkeypatch):
    """An un-migrated DB costs one failed statement per process, not per call."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "empty.db"))
    monkeypatch.setattr(R, "_table_missing", False, raising=False)
    import tools.db.storage as storage
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "empty.db"), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)

    for _ in range(3):
        with R.record(R.SURFACE_MCP, "t"):
            pass
    assert R._table_missing is True, "should have latched after the first failure"


def test_parent_id_links_a_role_step_to_its_run(obs_db):
    with R.record(R.SURFACE_ROLE, "role:step", parent_id="ace-123"):
        pass
    assert _rows()[-1]["parent_id"] == "ace-123"


def test_summary_rolls_up_calls_and_errors(obs_db):
    with R.record(R.SURFACE_MCP, "dup"):
        pass
    with pytest.raises(ValueError):
        with R.record(R.SURFACE_MCP, "dup"):
            raise ValueError("x")
    entry = next(s for s in R.summary(R.SURFACE_MCP) if s["name"] == "dup")
    assert entry["calls"] == 2 and entry["errors"] == 1


# --------------------------------------------------------- real dispatch paths

def test_mcp_dispatch_is_actually_instrumented(obs_db):
    """Drives the real _register_lazy_tool closure, not the helper.

    This is the assertion that matters: it is the difference between "the
    recorder works" and "the 512 registered tools are observed".
    """
    from tools.mcp.unified_server import UnifiedMCPServer

    srv = UnifiedMCPServer.__new__(UnifiedMCPServer)
    srv._handler_cache = {}
    captured = {}
    srv.register_tool = (  # type: ignore[method-assign]
        lambda name, description, input_schema, handler: captured.__setitem__(name, handler)
    )
    srv._resolve_handler = lambda n, e: (lambda args: {"ok": True})  # type: ignore

    srv._register_lazy_tool("probe_tool", {"description": "d", "input_schema": {}})
    before = len(_rows())
    assert captured["probe_tool"]({"alpha": 1, "beta": "hunter2"}) == {"ok": True}

    rows = _rows()
    assert len(rows) == before + 1, "MCP dispatch recorded no invocation"
    row = rows[-1]
    assert row["surface"] == "mcp" and row["name"] == "probe_tool"
    assert "hunter2" not in json.dumps(row), "MCP argument VALUE leaked"
    assert json.loads(row["arg_keys"]) == ["alpha", "beta"]


def test_mcp_dispatch_records_a_failing_tool(obs_db):
    from tools.mcp.unified_server import UnifiedMCPServer

    srv = UnifiedMCPServer.__new__(UnifiedMCPServer)
    srv._handler_cache = {}
    captured = {}
    srv.register_tool = (  # type: ignore[method-assign]
        lambda name, description, input_schema, handler: captured.__setitem__(name, handler)
    )

    def boom(_args):
        raise KeyError("missing")

    srv._resolve_handler = lambda n, e: boom  # type: ignore
    srv._register_lazy_tool("bad_tool", {"description": "d", "input_schema": {}})

    with pytest.raises(KeyError):
        captured["bad_tool"]({"x": 1})
    row = _rows()[-1]
    assert row["name"] == "bad_tool" and row["status"] == R.STATUS_ERROR
    assert row["error_class"] == "KeyError"


def test_all_four_surfaces_are_declared():
    assert set(R.SURFACES) == {"mcp", "agent", "persona", "role"}
