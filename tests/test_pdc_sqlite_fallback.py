# CUI // SP-CTI
"""pdx-data-03: real-SQL proof that the PDC SQLite fallback translates %s.

The entire Pipeline Design Canvas twin/SOP surface authors runtime SQL with
psycopg2-native ``%s`` placeholders (twin.py, sops.py). Before pdx-data-03 the
SQLite fallback in tools/pipeline/db/init_db.py returned a *bare*
sqlite3.Connection, so every one of those queries raised
``sqlite3.OperationalError: near "%": syntax error`` — the whole surface was
dead in SQLite mode (air-gap deployments; tests/conftest.py forces
ICDEV_STORAGE_BACKEND=sqlite). This went unnoticed because the existing route
tests use MagicMock connections.

These tests use NO mocks. They point init_db at a temporary SQLite DB, build
the real schema, and drive twin + sops through their public functions, proving
%s SQL executes end-to-end against the wrapped connection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def sqlite_pdc(tmp_path, monkeypatch):
    """Point the PDC canvas DB at a temp SQLite file on the fallback backend.

    Returns the imported init_db module (schema already created). twin.py and
    sops.py re-import get_connection from this module on every call, so patching
    the module globals here redirects them too.
    """
    from tools.pipeline.db import init_db

    db_file = tmp_path / "pipeline_canvas.db"
    # Force the SQLite fallback branch and redirect the DB path. get_connection
    # reads both as module globals at call time, so setattr takes effect live.
    monkeypatch.setattr(init_db, "_PC_BACKEND", "sqlite", raising=True)
    monkeypatch.setattr(init_db, "DB_PATH", db_file, raising=True)

    # Sanity: the fallback must hand back the translating wrapper, not a bare
    # sqlite3.Connection — otherwise %s SQL would never work.
    conn = init_db.get_connection()
    assert type(conn).__name__ == "StorageConnection", (
        "SQLite fallback must return the translating StorageConnection wrapper"
    )
    conn.close()

    init_db.init_db()  # create schema + seed (also exercises the %s seed inserts)
    return init_db


def _make_pipeline(init_db, pipe_id: str = "pipe-pdx-1") -> str:
    """Insert a real pipeline row (FK target for pdc_snapshots) using %s SQL."""
    graph = {
        "nodes": [{"id": "n1", "type": "build"}, {"id": "n2", "type": "deploy"}],
        "edges": [{"id": "e1", "from": "n1", "to": "n2"}],
    }
    conn = init_db.get_connection()
    conn.execute(
        "INSERT INTO pipelines (id, name, graph_json) VALUES (%s, %s, %s)",
        (pipe_id, "PDX Fallback Pipeline", json.dumps(graph)),
    )
    conn.commit()
    conn.close()
    return pipe_id


# ── init/seed proves %s seed inserts translate ────────────────────────────────

def test_init_db_seeds_via_percent_s(sqlite_pdc):
    """init_db() must create schema and seed templates/snippets with %s SQL."""
    init_db = sqlite_pdc
    conn = init_db.get_connection()
    tpl_count = conn.execute("SELECT COUNT(*) FROM pc_templates").fetchone()[0]
    snip_count = conn.execute("SELECT COUNT(*) FROM pc_snippets").fetchone()[0]
    conn.close()
    assert tpl_count > 0, "templates should have been seeded"
    assert snip_count > 0, "snippets should have been seeded"


# ── twin.take_snapshot + list_snapshots on real SQLite ────────────────────────

def test_twin_snapshot_roundtrip(sqlite_pdc):
    """take_snapshot + list_snapshots must execute %s SQL end-to-end."""
    from tools.pipeline import twin

    init_db = sqlite_pdc
    pipe_id = _make_pipeline(init_db)

    snap = twin.take_snapshot(pipe_id, label="pdx-test-snap", user_id="tester")
    assert snap["pipeline_id"] == pipe_id
    assert snap["node_count"] == 2
    assert snap["edge_count"] == 1
    assert isinstance(snap["id"], str) and snap["id"]

    snaps = twin.list_snapshots(pipe_id)
    assert len(snaps) == 1
    assert snaps[0]["id"] == snap["id"]
    assert snaps[0]["label"] == "pdx-test-snap"
    assert snaps[0]["pipeline_id"] == pipe_id


def test_twin_take_snapshot_missing_pipeline_raises(sqlite_pdc):
    """A %s SELECT that finds no row must surface as ValueError, not an SQL error."""
    from tools.pipeline import twin

    with pytest.raises(ValueError):
        twin.take_snapshot("does-not-exist", user_id="tester")


# ── sops create/read on real SQLite ───────────────────────────────────────────

def test_sops_create_and_read(sqlite_pdc):
    """create_sop (INSERT %s) + get_sop_by_id (SELECT %s) must round-trip."""
    from tools.pipeline import sops

    created = sops.create_sop(
        {
            "title": "PDX Fallback SOP",
            "sop_type": "custom",
            "description": "proves %s INSERT works on the SQLite fallback",
            "steps": [{"n": 1, "text": "do the thing"}],
            "nist_controls": ["AU-2", "CM-3"],
            "owner": "tester",
        }
    )
    assert created is not None
    sop_id = created["id"]
    assert created["title"] == "PDX Fallback SOP"
    # JSON fields must survive the round-trip as parsed Python objects.
    assert created["steps"] == [{"n": 1, "text": "do the thing"}]
    assert created["nist_controls"] == ["AU-2", "CM-3"]

    fetched = sops.get_sop_by_id(sop_id)
    assert fetched is not None
    assert fetched["id"] == sop_id
    assert fetched["title"] == "PDX Fallback SOP"
    assert fetched["nist_controls"] == ["AU-2", "CM-3"]


def test_sops_filtered_read(sqlite_pdc):
    """get_all_sops with a filter exercises a multi-%s WHERE clause."""
    from tools.pipeline import sops

    sops.create_sop({"title": "Alpha", "sop_type": "incident", "owner": "a"})
    sops.create_sop({"title": "Beta", "sop_type": "custom", "owner": "b"})

    incident = sops.get_all_sops(sop_type="incident")
    assert len(incident) == 1
    assert incident[0]["title"] == "Alpha"
