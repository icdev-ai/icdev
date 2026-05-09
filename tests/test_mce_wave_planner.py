# CUI // SP-CTI
"""Unit tests for tools.migration_canvas.wave_planner (Phase 4)."""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────

WAVE_DEP_SCHEMA = """
CREATE TABLE IF NOT EXISTS mc_migration_waves (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    wave_number      INTEGER NOT NULL,
    name             TEXT NOT NULL,
    cutover_date     TEXT,
    status           TEXT DEFAULT 'planned',
    server_ids_json  TEXT DEFAULT '[]',
    notes            TEXT,
    created_at       TEXT NOT NULL,
    classification   TEXT DEFAULT 'CUI'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_waves_num ON mc_migration_waves(session_id, wave_number);

CREATE TABLE IF NOT EXISTS mc_server_dependencies (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    source_server_id TEXT NOT NULL,
    target_server_id TEXT NOT NULL,
    dep_type         TEXT DEFAULT 'network',
    direction        TEXT DEFAULT 'bidirectional',
    notes            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mc_deps_session ON mc_server_dependencies(session_id);

CREATE TABLE IF NOT EXISTS mc_srv_sessions (
    id              TEXT PRIMARY KEY,
    src_hostname    TEXT DEFAULT '',
    readiness_score REAL DEFAULT 0
);
"""


@pytest.fixture()
def mc_conn(tmp_path):
    """In-memory SQLite connection patched into wave_planner._conn."""
    db_path = tmp_path / "test_waves.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(WAVE_DEP_SCHEMA)
    conn.commit()

    import tools.migration_canvas.wave_planner as wp

    def _fake_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    with patch.object(wp, "_conn", _fake_conn):
        yield _fake_conn


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_get_waves_empty(mc_conn):
    """Empty session returns empty list, not an error."""
    from tools.migration_canvas.wave_planner import get_waves
    waves = get_waves("no-such-session")
    assert waves == []


def test_upsert_wave_create(mc_conn):
    """upsert_wave creates a new row when no id is provided."""
    from tools.migration_canvas.wave_planner import upsert_wave, get_waves
    wave = upsert_wave("sess-1", {
        "wave_number": 1,
        "name": "Wave 1 — Rehost",
        "server_ids": ["srv-a", "srv-b"],
    })
    assert wave["id"].startswith("wave-")
    rows = get_waves("sess-1")
    assert len(rows) == 1
    assert rows[0]["name"] == "Wave 1 — Rehost"
    assert "srv-a" in rows[0]["server_ids"]


def test_upsert_wave_update(mc_conn):
    """upsert_wave with existing id updates instead of inserting duplicate."""
    from tools.migration_canvas.wave_planner import upsert_wave, get_waves
    wave = upsert_wave("sess-2", {"wave_number": 1, "name": "Draft Wave"})
    wave_id = wave["id"]
    upsert_wave("sess-2", {"id": wave_id, "wave_number": 1, "name": "Updated Wave"})
    rows = get_waves("sess-2")
    assert len(rows) == 1
    assert rows[0]["name"] == "Updated Wave"


def test_delete_wave(mc_conn):
    """delete_wave removes the row and returns True; second call returns False."""
    from tools.migration_canvas.wave_planner import upsert_wave, delete_wave, get_waves
    w = upsert_wave("sess-3", {"wave_number": 1, "name": "To Delete"})
    removed = delete_wave(w["id"], "sess-3")
    assert removed is True
    assert get_waves("sess-3") == []
    removed_again = delete_wave(w["id"], "sess-3")
    assert removed_again is False


def test_auto_assign_no_servers(mc_conn):
    """auto_assign_waves with no servers creates 3 default waves."""
    from tools.migration_canvas.wave_planner import auto_assign_waves, get_waves
    result = auto_assign_waves("sess-auto")
    assert result["ok"] is True
    assert result["total_servers"] == 0
    waves = get_waves("sess-auto")
    assert len(waves) == 3
    assert waves[0]["wave_number"] == 1
    assert waves[1]["wave_number"] == 2
    assert waves[2]["wave_number"] == 3


def test_wave_number_unique_per_session(mc_conn):
    """Inserting a duplicate (session_id, wave_number) raises IntegrityError."""
    from tools.migration_canvas.wave_planner import upsert_wave
    upsert_wave("sess-4", {"wave_number": 1, "name": "Wave A"})
    with pytest.raises(Exception):
        # Force a direct duplicate insert to hit the UNIQUE constraint
        c = mc_conn()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        c.execute(
            "INSERT INTO mc_migration_waves (id, session_id, wave_number, name, created_at) "
            "VALUES ('dup-id', 'sess-4', 1, 'Wave B', ?)",
            (now,),
        )
        c.commit()
        c.close()
