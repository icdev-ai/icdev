# CUI // SP-CTI
"""Tests for ACF MCP gateway registration (acf-mcp-01).

Covers the two contractual requirements of the task:

  * **Registry lists the tools** — ``foundry_run`` and ``foundry_status`` are
    present in ``TOOL_REGISTRY`` with the right module / handler / category and a
    resolvable handler (the same lazy lookup ``unified_server`` performs).
  * **Handler returns JSON for a fixture run** — an end-to-end call of
    ``handle_foundry_run`` against a hermetic file-backed SQLite DB (the same
    ``init_db``-stubbed + ``get_connection``-repointed seam the engine tests use,
    no Postgres) returns a JSON-serialisable roll-up dict; ``handle_foundry_status``
    then reads the pipeline back.

The engine's substantive behaviour (rate limits, stage wiring) is covered by
``tests/foundry/test_engine.py``; these tests assert the MCP wiring only.
"""

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mcp import gap_handlers, tool_registry  # noqa: E402
from tools.foundry import engine  # noqa: E402
from tools.foundry.db.init_db import _SCHEMA_SQLITE  # noqa: E402

FOUNDRY_TOOLS = ("foundry_run", "foundry_status")

# Minimal kanban_tasks slice — only the columns _active_project_count reads.
_KANBAN_DDL = "CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, status TEXT);"


def _new_conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def db(tmp_path, monkeypatch):
    """File-backed SQLite DB wired into the engine (init_db stubbed, get_connection
    repointed), mirroring tests/foundry/test_engine.py. Yields the db path."""
    path = str(tmp_path / "foundry_mcp_test.db")
    boot = _new_conn(path)
    boot.executescript(_SCHEMA_SQLITE)
    boot.executescript(_KANBAN_DDL)
    boot.commit()
    boot.close()

    # Never touch the platform DB: stub every init_db the engine / harvester call.
    monkeypatch.setattr(engine, "init_db", lambda *a, **k: True)
    from tools.foundry import harvester

    monkeypatch.setattr(harvester, "init_db", lambda *a, **k: True)
    # Force the SQLite lastrowid path in _open_run.
    monkeypatch.setattr(engine, "_is_pg", lambda: False)

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))
    yield path


# --------------------------------------------------------------------------- #
# Registry — the tools are listed and dispatchable
# --------------------------------------------------------------------------- #
def test_registry_lists_foundry_tools():
    for name in FOUNDRY_TOOLS:
        assert name in tool_registry.TOOL_REGISTRY, f"{name} not registered in TOOL_REGISTRY"
        entry = tool_registry.TOOL_REGISTRY[name]
        assert entry["module"] == "tools.mcp.gap_handlers"
        assert entry["category"] == "foundry"
        assert entry["description"]
        assert entry["input_schema"]["type"] == "object"


def test_registry_foundry_handlers_resolvable():
    # Mirror unified_server._resolve_handler: import module, getattr the handler.
    for name in FOUNDRY_TOOLS:
        entry = tool_registry.TOOL_REGISTRY[name]
        mod = importlib.import_module(entry["module"])
        handler = getattr(mod, entry["handler"], None)
        assert callable(handler), f"{entry['handler']} is not callable"


def test_foundry_run_schema_has_dry_run():
    schema = tool_registry.TOOL_REGISTRY["foundry_run"]["input_schema"]
    props = schema["properties"]
    assert props["dry_run"]["type"] == "boolean"
    assert props["dry_run"]["default"] is False
    assert "max_concepts" in props
    # dry_run is optional — no required keys.
    assert not schema.get("required")


def test_foundry_tools_listed_by_list_tools():
    names = tool_registry.list_tools()
    for name in FOUNDRY_TOOLS:
        assert name in names


# --------------------------------------------------------------------------- #
# handle_foundry_run — delegation + guards
# --------------------------------------------------------------------------- #
def test_run_handler_delegates_to_engine(monkeypatch):
    captured = {}

    def fake_run_cycle(*, dry_run=False, max_concepts=None):
        captured.update(dry_run=dry_run, max_concepts=max_concepts)
        return {
            "run_id": "9",
            "harvested": 0,
            "concepts_proposed": 0,
            "concepts_approved": 0,
            "tasks_emitted": 0,
            "active_projects": 0,
            "status": "completed",
            "dry_run": dry_run,
        }

    monkeypatch.setattr(engine, "run_cycle", fake_run_cycle)

    result = gap_handlers.handle_foundry_run({"dry_run": True, "max_concepts": 2})
    assert result["run_id"] == "9"
    assert result["status"] == "completed"
    assert captured == {"dry_run": True, "max_concepts": 2}


def test_run_handler_defaults(monkeypatch):
    captured = {}

    def fake_run_cycle(*, dry_run=False, max_concepts=None):
        captured.update(dry_run=dry_run, max_concepts=max_concepts)
        return {"status": "completed"}

    monkeypatch.setattr(engine, "run_cycle", fake_run_cycle)
    gap_handlers.handle_foundry_run({})
    assert captured == {"dry_run": False, "max_concepts": None}


def test_run_handler_wraps_engine_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("harvest exploded")

    monkeypatch.setattr(engine, "run_cycle", boom)
    result = gap_handlers.handle_foundry_run({})
    assert "error" in result
    assert "harvest exploded" in result["error"]


def test_status_handler_delegates_to_engine(monkeypatch):
    captured = {}

    def fake_status(*, conn=None, limit=10):
        captured.update(limit=limit)
        return {"recent_runs": [], "active_projects": 0, "pipeline": {}, "rate_limits": {}}

    monkeypatch.setattr(engine, "status", fake_status)
    result = gap_handlers.handle_foundry_status({"limit": 3})
    assert "recent_runs" in result
    assert captured == {"limit": 3}


def test_status_handler_bad_limit_falls_back(monkeypatch):
    captured = {}

    def fake_status(*, conn=None, limit=10):
        captured.update(limit=limit)
        return {"recent_runs": []}

    monkeypatch.setattr(engine, "status", fake_status)
    gap_handlers.handle_foundry_status({"limit": "not-an-int"})
    assert captured == {"limit": 10}


# --------------------------------------------------------------------------- #
# End-to-end — handler returns JSON for a fixture run
# --------------------------------------------------------------------------- #
def test_run_handler_returns_rollup_for_fixture(db):
    result = gap_handlers.handle_foundry_run({"dry_run": True})
    assert "error" not in result, result
    assert isinstance(result, dict)
    assert result["status"] == "completed"
    assert result["dry_run"] is True
    assert result["tasks_emitted"] == 0  # dry_run never seeds
    for key in ("run_id", "harvested", "concepts_proposed", "concepts_approved", "active_projects"):
        assert key in result

    # JSON-serialisable (MCP transport contract).
    json.dumps(result, default=str)


def test_status_handler_returns_pipeline_for_fixture(db):
    # A real run first so recent_runs has a row to read back.
    gap_handlers.handle_foundry_run({"dry_run": True})

    snap = gap_handlers.handle_foundry_status({})
    assert "error" not in snap, snap
    assert set(snap.keys()) == {"recent_runs", "active_projects", "pipeline", "rate_limits"}
    assert isinstance(snap["recent_runs"], list)
    assert len(snap["recent_runs"]) >= 1
    assert snap["rate_limits"]["max_active_projects"] >= 1
    json.dumps(snap, default=str)
