# CUI // SP-CTI
"""Tests for SIPA MCP gateway registration (sipa-mcp-01).

Covers the two contractual requirements of the task:

  * **Registry lists the tools** — ``integrity_assess`` and
    ``integrity_list_assessments`` are present in ``TOOL_REGISTRY`` with the right
    module / handler / category and a resolvable handler (the same lazy lookup
    ``unified_server`` performs).
  * **Handler returns a JSON assessment for a fixture path** — an end-to-end run
    of ``handle_integrity_assess`` against a benign source tree, using the same
    deterministic-offline-scanners + shared in-memory DB seam as the engine tests
    (no real bandit / pip-audit / Semgrep, no Postgres), returns a JSON-serialisable
    assessment dict; ``handle_integrity_list_assessments`` then reads it back.

The engine's substantive behaviour is covered by ``test_integrity_engine.py``;
these tests assert the MCP wiring only.
"""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mcp import gap_handlers, tool_registry  # noqa: E402
from tools.integrity import scanners  # noqa: E402
from tools.integrity.db import init_db as init_db_mod  # noqa: E402

INTEGRITY_TOOLS = ("integrity_assess", "integrity_list_assessments")


# --------------------------------------------------------------------------- #
# Fixtures — benign source tree + shared in-memory DB with offline scanners
# --------------------------------------------------------------------------- #
_BENIGN_README = """\
# Tinylog

A tiny utility that reads and writes log files on disk. It saves entries to a
file and reads them back. Nothing else — no network, no subprocesses.
"""

_BENIGN_PY = '''\
"""Tinylog — read and write log entries to a file on disk."""
from pathlib import Path


def write_entry(path, text):
    Path(path).write_text(text, encoding="utf-8")


def read_entries(path):
    return Path(path).read_text(encoding="utf-8")
'''


class _SharedConn(sqlite3.Connection):
    """In-memory connection whose ``.close()`` is a no-op.

    ``engine.assess`` opens and closes its own connection when ``conn=None``;
    keeping the connection alive lets the subsequent list handler read the row
    back from the same in-memory database.
    """

    def close(self):  # noqa: D401 - intentional no-op
        pass


@pytest.fixture
def benign_source(tmp_path):
    base = tmp_path / "benign_src"
    base.mkdir(parents=True, exist_ok=True)
    (base / "README.md").write_text(_BENIGN_README, encoding="utf-8")
    (base / "tinylog.py").write_text(_BENIGN_PY, encoding="utf-8")
    return base


@pytest.fixture
def shared_db(monkeypatch, tmp_path):
    """Route every ``get_connection()`` to one persistent in-memory DB.

    Patches BOTH seams: ``engine.assess`` imports ``get_connection`` lazily from
    ``tools.db.storage``, while ``gap_handlers`` bound it at import time — so the
    list handler needs its module-level name patched too. Scanners are stubbed to
    the same deterministic-offline values the engine tests use.
    """
    conn = sqlite3.connect(":memory:", factory=_SharedConn)
    conn.row_factory = sqlite3.Row
    init_db_mod.init_db(conn)

    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    # Hand out the StorageConnection wrapper, not the raw sqlite3 connection.
    # The engine authors its SQL PostgreSQL-first (%s placeholders); a bare
    # sqlite3 connection rejects them, the persist step fails, and the engine
    # fails CLOSED — surfacing as status/verdict "quarantine" on a benign tree
    # rather than as a visible error. Production always sees the wrapper, whose
    # translate_sql rewrites %s -> ? for SQLite.
    #
    # A fresh wrapper per call is fine: _SharedConn.close() is a no-op, so every
    # wrapper shares the one live in-memory database.
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(
        storage, "get_connection", lambda *a, **k: storage.StorageConnection(conn, "sqlite")
    )
    monkeypatch.setattr(
        gap_handlers, "get_connection", lambda *a, **k: storage.StorageConnection(conn, "sqlite")
    )

    monkeypatch.setattr(scanners, "_invoke_scanner", lambda cmd, timeout: (0, "{}", ""))
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: None)

    yield conn
    sqlite3.Connection.close(conn)


# --------------------------------------------------------------------------- #
# Registry — the tools are listed and dispatchable
# --------------------------------------------------------------------------- #
def test_registry_lists_integrity_tools():
    for name in INTEGRITY_TOOLS:
        assert name in tool_registry.TOOL_REGISTRY, f"{name} not registered in TOOL_REGISTRY"
        entry = tool_registry.TOOL_REGISTRY[name]
        assert entry["module"] == "tools.mcp.gap_handlers"
        assert entry["category"] == "integrity"
        assert entry["description"]
        assert entry["input_schema"]["type"] == "object"


def test_registry_integrity_handlers_resolvable():
    # Mirror unified_server._resolve_handler: import module, getattr the handler.
    for name in INTEGRITY_TOOLS:
        entry = tool_registry.TOOL_REGISTRY[name]
        mod = importlib.import_module(entry["module"])
        handler = getattr(mod, entry["handler"], None)
        assert callable(handler), f"{entry['handler']} is not callable"


def test_assess_schema_requires_source():
    schema = tool_registry.TOOL_REGISTRY["integrity_assess"]["input_schema"]
    assert "source" in schema["required"]
    assert set(schema["properties"]["mode"]["enum"]) == {
        "auto",
        "provenance_aware",
        "provenance_blind",
    }


# --------------------------------------------------------------------------- #
# handle_integrity_assess — delegation + guards
# --------------------------------------------------------------------------- #
def test_assess_handler_delegates_to_engine(monkeypatch):
    captured = {}

    def fake_assess(source, mode="auto", project_id=None, session_id=None, declared_purpose=None):
        captured.update(
            source=source,
            mode=mode,
            project_id=project_id,
            session_id=session_id,
            declared_purpose=declared_purpose,
        )
        return {
            "assessment_id": 7,
            "verdict": "allow",
            "risk_score": 3,
            "findings_count": 0,
            "capabilities_count": 1,
            "status": "assessed",
        }

    eng = importlib.import_module("tools.integrity.engine")
    monkeypatch.setattr(eng, "assess", fake_assess)

    result = gap_handlers.handle_integrity_assess(
        {"source": "/srv/pkg", "mode": "provenance_blind", "project_id": "p1", "session_id": "s1"}
    )
    assert result["assessment_id"] == 7
    assert result["verdict"] == "allow"
    assert captured == {
        "source": "/srv/pkg",
        "mode": "provenance_blind",
        "project_id": "p1",
        "session_id": "s1",
        "declared_purpose": None,
    }


def test_assess_handler_requires_source():
    result = gap_handlers.handle_integrity_assess({})
    assert "error" in result
    assert "source" in result["error"]


def test_assess_handler_wraps_engine_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ingest refused")

    eng = importlib.import_module("tools.integrity.engine")
    monkeypatch.setattr(eng, "assess", boom)

    result = gap_handlers.handle_integrity_assess({"source": "/x"})
    assert "error" in result
    assert "ingest refused" in result["error"]


# --------------------------------------------------------------------------- #
# End-to-end — handler returns a JSON assessment for a fixture path
# --------------------------------------------------------------------------- #
def test_assess_handler_returns_assessment_for_fixture(shared_db, benign_source):
    result = gap_handlers.handle_integrity_assess(
        {"source": str(benign_source), "mode": "provenance_blind"}
    )
    assert "error" not in result, result
    assert isinstance(result, dict)
    assert isinstance(result["assessment_id"], int)
    assert result["verdict"] in ("allow", "review", "quarantine")
    assert result["status"] == "assessed"
    assert result["verdict"] == "allow"  # benign tree => ALLOW

    # JSON-serialisable (MCP transport contract).
    import json

    json.dumps(result)


def test_list_handler_returns_assessments(shared_db, benign_source):
    gap_handlers.handle_integrity_assess({"source": str(benign_source), "mode": "provenance_blind"})

    listed = gap_handlers.handle_integrity_list_assessments({})
    assert "error" not in listed, listed
    assert listed["count"] >= 1
    row = listed["assessments"][0]
    for col in ("id", "source_type", "mode", "status", "verdict", "risk_score"):
        assert col in row
    assert row["status"] == "assessed"


def test_list_handler_status_filter_excludes(shared_db, benign_source):
    gap_handlers.handle_integrity_assess({"source": str(benign_source), "mode": "provenance_blind"})

    # No row is in 'rejected' state, so the filter yields an empty (but valid) list.
    listed = gap_handlers.handle_integrity_list_assessments({"status": "rejected"})
    assert listed["count"] == 0
    assert listed["assessments"] == []


def test_list_handler_graceful_without_table(monkeypatch):
    bad = sqlite3.connect(":memory:")  # no integrity_assessments table
    bad.row_factory = sqlite3.Row
    monkeypatch.setattr(gap_handlers, "get_connection", lambda *a, **k: bad)
    result = gap_handlers.handle_integrity_list_assessments({})
    assert "error" in result
    assert result["assessments"] == []
    bad.close()
