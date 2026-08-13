# CUI // SP-CTI
"""Tests for tools/observability_canvas/mitre_coverage_db.py — 4 CRUD functions.

Uses an in-memory SQLite DB via monkeypatching get_connection so tests are
fully isolated from any real DB file on disk.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

def _migration_path() -> Path:
    """Locate the odc_mitre_coverage migration by SLUG, not by number.

    It shipped as ``028_odc_mitre_coverage`` and was renumbered to
    ``336_odc_mitre_coverage`` to clear a duplicate-028 collision (there are two
    other 028_* directories). A hardcoded number turns any future renumber into
    a FileNotFoundError at fixture setup — which is exactly how this file rotted.
    The slug is the stable identifier, so match on it and fail loudly, with the
    candidates named, if it is ever ambiguous or gone.
    """
    matches = sorted(
        (_ROOT / "tools" / "db" / "migrations").glob("*_odc_mitre_coverage")
    )
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly 1 *_odc_mitre_coverage migration, found {len(matches)}: "
            f"{[m.name for m in matches]}"
        )
    return matches[0] / "up.py"


def _load_migration_up():
    spec = importlib.util.spec_from_file_location(
        "migration_odc_mitre_coverage_up", _migration_path()
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.up


class _NoCloseConn:
    """Wraps an sqlite3.Connection and makes close() a no-op.

    Python 3.14 made Connection.close read-only, so we can no longer
    monkey-patch it directly on the instance.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:  # no-op — fixture keeps the connection alive
        pass

    # Forward row_factory reads/writes so migration DDL works correctly.
    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value


@pytest.fixture()
def mem_db():
    """In-memory SQLite with mitre_coverage schema applied."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    _load_migration_up()(db)
    yield db
    db.close()


@pytest.fixture()
def patched_db(mem_db):
    """Patch get_connection in mitre_coverage_db to return the in-memory DB.

    The module authors its SQL with PostgreSQL ``%s`` placeholders and, in
    production, ``init_db.get_connection`` hands it a translating
    ``StorageConnection`` (``%s`` -> ``?`` on the SQLite fallback). The fixture
    must therefore wrap the in-memory connection in a ``StorageConnection`` too —
    a raw ``sqlite3`` connection raises ``sqlite3.OperationalError: near "%"``.
    The ``_NoCloseConn`` layer keeps each CRUD function's ``conn.close()`` from
    tearing down the shared in-memory DB. It goes on the INSIDE so that the
    object handed to the module under test is the translating
    ``StorageConnection`` itself, with ``StorageConnection.close()`` delegating
    into the no-op. Either nesting translates correctly at runtime; this one just
    puts the wrapper where a reader (and a static checker) expects it.
    """
    from tools.db.storage import StorageConnection

    wrapped = StorageConnection(_NoCloseConn(mem_db), "sqlite")

    import tools.observability_canvas.mitre_coverage_db as mod

    with patch.object(mod, "get_connection", return_value=wrapped):
        yield mod, mem_db


# ── record_coverage ───────────────────────────────────────────────────────────

def test_record_coverage_inserts_row(patched_db):
    mod, db = patched_db
    row_id = mod.record_coverage("T1059", "caldera", "partial", "2026-04-18T10:00:00Z", "proj-1")
    assert row_id
    row = db.execute("SELECT * FROM mitre_coverage WHERE id=?", (row_id,)).fetchone()
    assert row["technique_id"] == "T1059"
    assert row["state"] == "partial"
    assert row["project_id"] == "proj-1"


def test_record_coverage_rejects_invalid_state(patched_db):
    mod, _ = patched_db
    with pytest.raises(ValueError, match="Invalid state"):
        mod.record_coverage("T1059", "caldera", "bad_state", "2026-04-18T10:00:00Z", "proj-1")


def test_record_coverage_all_valid_states(patched_db):
    mod, db = patched_db
    for state in ("none", "partial", "full", "false_positive"):
        row_id = mod.record_coverage("T1059", "caldera", state, "2026-04-18T10:00:00Z", "proj-x")
        assert row_id
    count = db.execute("SELECT COUNT(*) FROM mitre_coverage WHERE project_id='proj-x'").fetchone()[0]
    assert count == 4


# ── get_coverage ──────────────────────────────────────────────────────────────

def test_get_coverage_returns_latest(patched_db):
    mod, _ = patched_db
    mod.record_coverage("T1059", "caldera", "none", "2026-04-18T09:00:00Z", "proj-1")
    mod.record_coverage("T1059", "caldera", "full", "2026-04-18T10:00:00Z", "proj-1")
    result = mod.get_coverage("T1059", "proj-1")
    assert result is not None
    assert result["state"] == "full"


def test_get_coverage_none_when_missing(patched_db):
    mod, _ = patched_db
    assert mod.get_coverage("T9999", "proj-missing") is None


def test_get_coverage_filtered_by_signal_source(patched_db):
    mod, _ = patched_db
    mod.record_coverage("T1059", "caldera", "partial", "2026-04-18T09:00:00Z", "proj-1")
    mod.record_coverage("T1059", "siem", "full", "2026-04-18T10:00:00Z", "proj-1")
    result = mod.get_coverage("T1059", "proj-1", signal_source="caldera")
    assert result["state"] == "partial"
    assert result["signal_source"] == "caldera"


# ── list_coverage ─────────────────────────────────────────────────────────────

def test_list_coverage_returns_project_rows(patched_db):
    mod, _ = patched_db
    mod.record_coverage("T1059", "caldera", "partial", "2026-04-18T10:00:00Z", "proj-1")
    mod.record_coverage("T1078", "siem", "full", "2026-04-18T11:00:00Z", "proj-1")
    mod.record_coverage("T1059", "caldera", "none", "2026-04-18T10:00:00Z", "proj-2")
    rows = mod.list_coverage("proj-1")
    assert len(rows) == 2
    assert all(r["project_id"] == "proj-1" for r in rows)


def test_list_coverage_filtered_by_state(patched_db):
    mod, _ = patched_db
    mod.record_coverage("T1059", "caldera", "partial", "2026-04-18T10:00:00Z", "proj-1")
    mod.record_coverage("T1078", "siem", "full", "2026-04-18T11:00:00Z", "proj-1")
    rows = mod.list_coverage("proj-1", state="full")
    assert len(rows) == 1
    assert rows[0]["state"] == "full"


def test_list_coverage_empty_project(patched_db):
    mod, _ = patched_db
    assert mod.list_coverage("proj-none") == []


# ── coverage_summary ──────────────────────────────────────────────────────────

def test_coverage_summary_counts_latest_state(patched_db):
    mod, _ = patched_db
    # T1059/caldera: transitions none→partial (latest = partial)
    mod.record_coverage("T1059", "caldera", "none", "2026-04-18T09:00:00Z", "proj-1")
    mod.record_coverage("T1059", "caldera", "partial", "2026-04-18T10:00:00Z", "proj-1")
    # T1078/siem: only "full"
    mod.record_coverage("T1078", "siem", "full", "2026-04-18T11:00:00Z", "proj-1")
    summary = mod.coverage_summary("proj-1")
    assert summary["partial"] == 1
    assert summary["full"] == 1
    assert summary["none"] == 0
    assert summary["false_positive"] == 0


def test_coverage_summary_empty(patched_db):
    mod, _ = patched_db
    summary = mod.coverage_summary("proj-empty")
    assert set(summary.keys()) == {"none", "partial", "full", "false_positive"}
    assert all(v == 0 for v in summary.values())
