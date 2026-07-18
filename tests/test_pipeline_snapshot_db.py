# CUI // SP-CTI
"""Tests for tools.pipeline.snapshot_db — 4-function CRUD layer.

NOTE (pdx-data-01): snapshot_db targets the shared-icdev ``pipeline_snapshots``
table and is DEPRECATED in favor of the ``pdc_snapshots`` canvas store (the
delta view and IQE pipeline adapters now read pdc_snapshots via the canvas
connection). The module is retained but has no remaining runtime callers, so
this suite continues to cover its CRUD contract unchanged.
"""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "tools" / "db" / "migrations" / "027_pipeline_snapshots" / "up.py"
)


def _apply_migration(conn: sqlite3.Connection) -> None:
    spec = importlib.util.spec_from_file_location("migration_027_up", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.up(conn)


@pytest.fixture()
def mem_conn():
    """In-memory SQLite connection with pipeline_snapshots schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_migration(conn)
    return conn


@pytest.fixture(autouse=True)
def patch_get_connection(mem_conn):
    """Redirect get_connection() to the in-memory fixture.

    Wrapped in StorageConnection so snapshot_db's PG-native %s placeholders
    translate to ? on SQLite — mirroring production (pipeline get_connection()
    returns a StorageConnection). A raw sqlite3 connection bypasses that
    translator and %s becomes a syntax error.
    """
    from tools.db.storage import StorageConnection

    sconn = StorageConnection(mem_conn, "sqlite")
    mock = MagicMock()
    mock.__enter__ = lambda s: sconn
    mock.__exit__ = MagicMock(return_value=False)
    mock.execute = sconn.execute
    mock.commit = sconn.commit
    mock.close = MagicMock()

    with patch("tools.pipeline.snapshot_db.get_connection", return_value=mock):
        yield mock


from tools.pipeline.snapshot_db import (  # noqa: E402 — after sys.path insert
    create_snapshot,
    get_latest_snapshot,
    get_snapshot,
    list_snapshots,
)


# ── create_snapshot ──────────────────────────────────────────────────────────

def test_create_snapshot_returns_id():
    snap_id = create_snapshot(
        "pipe-1",
        nodes_json='[{"id":"n1"}]',
        edges_json='[{"source":"n1","target":"n2"}]',
    )
    assert isinstance(snap_id, str) and len(snap_id) == 36  # UUID


def test_create_snapshot_accepts_python_objects():
    snap_id = create_snapshot(
        "pipe-2",
        nodes_json=[{"id": "n1", "type": "build-runner"}],
        edges_json=[{"source": "n1", "target": "n2"}],
        meta_json={"slsa": "L2", "classification": "CUI"},
    )
    assert snap_id


def test_create_snapshot_stores_all_fields(mem_conn):
    snap_id = create_snapshot(
        "pipe-3",
        nodes_json='[{"id":"a"}]',
        edges_json='[]',
        label="pre-merge baseline",
        snapshot_type="baseline",
        meta_json='{"note":"test"}',
        created_by="test-user",
    )
    row = dict(mem_conn.execute(
        "SELECT * FROM pipeline_snapshots WHERE id=?", (snap_id,)
    ).fetchone())
    assert row["pipeline_id"] == "pipe-3"
    assert row["label"] == "pre-merge baseline"
    assert row["snapshot_type"] == "baseline"
    assert row["created_by"] == "test-user"
    assert json.loads(row["nodes_json"]) == [{"id": "a"}]


# ── get_snapshot ─────────────────────────────────────────────────────────────

def test_get_snapshot_returns_dict():
    snap_id = create_snapshot("pipe-4", nodes_json="[]", edges_json="[]")
    row = get_snapshot(snap_id)
    assert isinstance(row, dict)
    assert row["id"] == snap_id


def test_get_snapshot_missing_returns_none():
    assert get_snapshot("nonexistent-id") is None


# ── list_snapshots ────────────────────────────────────────────────────────────

def test_list_snapshots_returns_newest_first():
    for i in range(3):
        create_snapshot("pipe-5", nodes_json=f'[{{"id":"n{i}"}}]', edges_json="[]")
    rows = list_snapshots("pipe-5")
    assert len(rows) == 3
    # created_at strings are ISO; newest should be last inserted (highest value)
    assert rows[0]["created_at"] >= rows[1]["created_at"]


def test_list_snapshots_limit():
    for _ in range(5):
        create_snapshot("pipe-6", nodes_json="[]", edges_json="[]")
    rows = list_snapshots("pipe-6", limit=2)
    assert len(rows) == 2


def test_list_snapshots_empty_for_unknown_pipeline():
    assert list_snapshots("no-such-pipeline") == []


# ── get_latest_snapshot ───────────────────────────────────────────────────────

def test_get_latest_snapshot_returns_most_recent():
    create_snapshot("pipe-7", nodes_json='[{"id":"old"}]', edges_json="[]", snapshot_type="baseline")
    create_snapshot("pipe-7", nodes_json='[{"id":"new"}]', edges_json="[]", snapshot_type="baseline")
    latest = get_latest_snapshot("pipe-7")
    assert latest is not None
    assert json.loads(latest["nodes_json"])[0]["id"] == "new"


def test_get_latest_snapshot_filters_by_type():
    create_snapshot("pipe-8", nodes_json="[]", edges_json="[]", snapshot_type="baseline")
    create_snapshot("pipe-8", nodes_json='[{"id":"delta"}]', edges_json="[]", snapshot_type="delta")
    delta = get_latest_snapshot("pipe-8", snapshot_type="delta")
    assert delta is not None
    assert json.loads(delta["nodes_json"])[0]["id"] == "delta"


def test_get_latest_snapshot_none_when_no_match():
    assert get_latest_snapshot("pipe-no-delta", snapshot_type="delta") is None
