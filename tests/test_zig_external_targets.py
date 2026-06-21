# CUI // SP-CTI
"""Pytest suite — ZIG external-target scoring and backward-compat.

Covers:
  - Schema: zig_activity_completions has target_id column
  - Schema: zig_maturity_scores has target_id column
  - Schema: zig_targets table exists
  - Backward compat: icdev-self default
  - Create + retrieve target
  - Activity completion scoped per target
  - Assessment score isolated per target
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── In-memory schema (matches ZIG external-targets design) ────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS zig_pillars (
    slug            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    pillar_weight   REAL DEFAULT 0.143
);

CREATE TABLE IF NOT EXISTS zig_capabilities (
    id                      TEXT PRIMARY KEY,
    pillar_slug             TEXT NOT NULL,
    title                   TEXT NOT NULL,
    phase                   TEXT NOT NULL,
    maturity_level          TEXT NOT NULL,
    implementation_status   TEXT DEFAULT 'not_started',
    nist_controls           TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS zig_activities (
    id              TEXT PRIMARY KEY,
    capability_id   TEXT NOT NULL,
    phase           TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    nist_control_ref TEXT
);

CREATE TABLE IF NOT EXISTS zig_activity_completions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id     TEXT NOT NULL,
    target_id       TEXT NOT NULL DEFAULT 'icdev-self',
    status          TEXT NOT NULL DEFAULT 'not_started',
    evidence_note   TEXT,
    completed_by    TEXT,
    completed_at    TEXT,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_zig_comp_act
    ON zig_activity_completions(activity_id, target_id);

CREATE TABLE IF NOT EXISTS zig_maturity_scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pillar_slug         TEXT NOT NULL,
    target_id           TEXT NOT NULL DEFAULT 'icdev-self',
    score               REAL NOT NULL DEFAULT 0.0,
    maturity_level      TEXT,
    capability_count    INTEGER DEFAULT 0,
    activity_count      INTEGER DEFAULT 0,
    complete_activities INTEGER DEFAULT 0,
    assessment_run_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zig_targets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    system_type     TEXT DEFAULT 'general',
    classification  TEXT DEFAULT 'CUI',
    status          TEXT DEFAULT 'active',
    pillar_focus    TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# ── Minimal in-memory connection wrapper ─────────────────────────────────────


class _Conn:
    """Duck-type wrapper around sqlite3 that keeps the in-memory DB alive."""

    def __init__(self):
        self._db = sqlite3.connect(":memory:")
        self._db.row_factory = sqlite3.Row
        for stmt in _SCHEMA.strip().split(";"):
            s = stmt.strip()
            if s and not s.startswith("--"):
                self._db.execute(s)
        self._db.commit()

    def execute(self, sql, params=()):
        return self._db.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._db.commit()

    def close(self):
        pass  # keep alive so patched callers that call close() don't destroy the DB

    def cursor(self):
        return self._db.cursor()


@pytest.fixture
def db():
    conn = _Conn()
    # Seed one pillar / capability / two activities for discovery
    conn.execute(
        "INSERT OR IGNORE INTO zig_pillars (slug, name) VALUES (?,?)",
        ("user", "User"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO zig_capabilities "
        "(id, pillar_slug, title, phase, maturity_level) VALUES (?,?,?,?,?)",
        ("cap-user-01", "user", "ICAM Discovery", "discovery", "basic"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO zig_activities (id, capability_id, phase, title) VALUES (?,?,?,?)",
        ("act-u-01", "cap-user-01", "discovery", "Identify user data sources"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO zig_activities (id, capability_id, phase, title) VALUES (?,?,?,?)",
        ("act-u-02", "cap-user-01", "discovery", "Map identity stores"),
    )
    conn.commit()
    yield conn


def _patch(conn):
    """Patch get_connection in all ZIG modules that import it."""
    return patch.multiple(
        "tools.security_canvas.zig_activity_tracker",
        get_connection=lambda: conn,
    )


def _patch_phase(conn):
    return patch.multiple(
        "tools.security_canvas.zig_phase_tracker",
        get_connection=lambda: conn,
    )


# ── 1. Schema: required tables exist ─────────────────────────────────────────


def test_schema_zig_activity_completions_exists(db):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='zig_activity_completions'"
    ).fetchone()
    assert row is not None, "zig_activity_completions table missing"


def test_schema_zig_maturity_scores_exists(db):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='zig_maturity_scores'"
    ).fetchone()
    assert row is not None, "zig_maturity_scores table missing"


def test_schema_zig_targets_exists(db):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='zig_targets'"
    ).fetchone()
    assert row is not None, "zig_targets table missing"


# ── 2. target_id columns exist ───────────────────────────────────────────────


def test_target_id_in_activity_completions(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(zig_activity_completions)").fetchall()]
    assert "target_id" in cols


def test_target_id_default_icdev_self(db):
    rows = db.execute("PRAGMA table_info(zig_activity_completions)").fetchall()
    dflt = next((str(r[4]) for r in rows if r[1] == "target_id"), None)
    assert dflt is not None and "icdev-self" in dflt


def test_target_id_in_maturity_scores(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(zig_maturity_scores)").fetchall()]
    assert "target_id" in cols


def test_maturity_scores_target_id_default_icdev_self(db):
    rows = db.execute("PRAGMA table_info(zig_maturity_scores)").fetchall()
    dflt = next((str(r[4]) for r in rows if r[1] == "target_id"), None)
    assert dflt is not None and "icdev-self" in dflt


# ── 3. Backward compat: icdev-self default ───────────────────────────────────


def test_get_phase_completions_no_arg_uses_icdev_self(db):
    """Calling get_phase_completions without target_id sees icdev-self rows."""
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "icdev-self", "complete"),
    )
    db.commit()

    from tools.security_canvas.zig_activity_tracker import get_phase_completions

    with _patch(db):
        result = get_phase_completions("discovery")

    assert result["complete"] >= 1
    assert result["total"] >= 1


def test_no_arg_equals_explicit_icdev_self(db):
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "icdev-self", "in_progress"),
    )
    db.commit()

    from tools.security_canvas.zig_activity_tracker import get_phase_completions

    with _patch(db):
        r_default = get_phase_completions("discovery")
        r_explicit = get_phase_completions("discovery", target_id="icdev-self")

    assert r_default["complete"] == r_explicit["complete"]
    assert r_default["in_progress"] == r_explicit["in_progress"]
    assert r_default["total"] == r_explicit["total"]


# ── 4. Create + retrieve target ──────────────────────────────────────────────


def test_create_and_retrieve_target(db):
    db.execute(
        "INSERT INTO zig_targets (id, name, system_type, status) VALUES (?,?,?,?)",
        ("tgt-acme-001", "ACME DOD System", "dod_il4", "active"),
    )
    db.commit()

    row = db.execute("SELECT * FROM zig_targets WHERE id=?", ("tgt-acme-001",)).fetchone()
    assert row is not None
    assert row["name"] == "ACME DOD System"
    assert row["status"] == "active"
    assert row["system_type"] == "dod_il4"


def test_target_classification_defaults_cui(db):
    db.execute(
        "INSERT INTO zig_targets (id, name) VALUES (?,?)",
        ("tgt-cls-test", "Test Target"),
    )
    db.commit()

    row = db.execute("SELECT classification FROM zig_targets WHERE id=?", ("tgt-cls-test",)).fetchone()
    assert row["classification"] == "CUI"


def test_target_status_defaults_active(db):
    db.execute(
        "INSERT INTO zig_targets (id, name) VALUES (?,?)",
        ("tgt-status-default", "Default Status Target"),
    )
    db.commit()

    row = db.execute("SELECT status FROM zig_targets WHERE id=?", ("tgt-status-default",)).fetchone()
    assert row["status"] == "active"


def test_multiple_targets_listable(db):
    db.execute("INSERT INTO zig_targets (id, name) VALUES (?,?)", ("tgt-alpha", "Alpha"))
    db.execute("INSERT INTO zig_targets (id, name) VALUES (?,?)", ("tgt-beta", "Beta"))
    db.commit()

    rows = db.execute("SELECT id FROM zig_targets ORDER BY id").fetchall()
    ids = [r["id"] for r in rows]
    assert "tgt-alpha" in ids
    assert "tgt-beta" in ids


# ── 5. Activity completion scoped per target ─────────────────────────────────


def test_completion_for_target_a_not_visible_to_target_b(db):
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "tgt-ext-a", "complete"),
    )
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "tgt-ext-b", "not_started"),
    )
    db.commit()

    from tools.security_canvas.zig_activity_tracker import get_phase_completions

    with _patch(db):
        res_a = get_phase_completions("discovery", target_id="tgt-ext-a")
        res_b = get_phase_completions("discovery", target_id="tgt-ext-b")

    assert res_a["complete"] >= 1
    assert res_b["complete"] == 0


def test_icdev_self_invisible_to_external_target(db):
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "icdev-self", "complete"),
    )
    db.commit()

    from tools.security_canvas.zig_activity_tracker import get_phase_completions

    with _patch(db):
        result = get_phase_completions("discovery", target_id="tgt-no-completions")

    assert result["complete"] == 0


def test_activity_pct_complete_per_target(db):
    """Percentage reflects only the target's own completions."""
    # For tgt-pct: both activities complete
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "tgt-pct", "complete"),
    )
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-02", "tgt-pct", "complete"),
    )
    db.commit()

    from tools.security_canvas.zig_activity_tracker import get_phase_completions

    with _patch(db):
        result = get_phase_completions("discovery", target_id="tgt-pct")

    assert result["pct_complete"] == 100


def test_unique_index_rejects_duplicate_activity_target_pair(db):
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "tgt-dup", "not_started"),
    )
    db.commit()

    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
            ("act-u-01", "tgt-dup", "in_progress"),
        )


def test_same_activity_different_targets_allowed(db):
    """The same activity_id CAN appear for two different targets."""
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "tgt-x", "complete"),
    )
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "tgt-y", "not_started"),
    )
    db.commit()  # must not raise

    count = db.execute(
        "SELECT COUNT(*) FROM zig_activity_completions WHERE activity_id=?", ("act-u-01",)
    ).fetchone()[0]
    assert count == 2


# ── 6. Assessment score isolated per target ──────────────────────────────────


def test_maturity_score_row_tagged_by_target(db):
    db.execute(
        "INSERT INTO zig_maturity_scores (pillar_slug, target_id, score) VALUES (?,?,?)",
        ("user", "tgt-score-a", 0.75),
    )
    db.execute(
        "INSERT INTO zig_maturity_scores (pillar_slug, target_id, score) VALUES (?,?,?)",
        ("user", "tgt-score-b", 0.25),
    )
    db.commit()

    row_a = db.execute(
        "SELECT score FROM zig_maturity_scores WHERE pillar_slug=? AND target_id=?",
        ("user", "tgt-score-a"),
    ).fetchone()
    row_b = db.execute(
        "SELECT score FROM zig_maturity_scores WHERE pillar_slug=? AND target_id=?",
        ("user", "tgt-score-b"),
    ).fetchone()

    assert row_a["score"] == 0.75
    assert row_b["score"] == 0.25
    assert row_a["score"] != row_b["score"]


def test_maturity_score_defaults_to_icdev_self(db):
    db.execute(
        "INSERT INTO zig_maturity_scores (pillar_slug, score) VALUES (?,?)",
        ("device", 0.5),
    )
    db.commit()

    row = db.execute(
        "SELECT target_id FROM zig_maturity_scores WHERE pillar_slug=?", ("device",)
    ).fetchone()
    assert row["target_id"] == "icdev-self"


def test_assessment_scores_for_two_targets_isolated(db):
    """Different targets can have different completion rates stored."""
    # tgt-flow-a: complete both activities
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "tgt-flow-a", "complete"),
    )
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-02", "tgt-flow-a", "complete"),
    )
    # tgt-flow-b: zero completions
    db.commit()

    from tools.security_canvas.zig_activity_tracker import get_phase_completions

    with _patch(db):
        flow_a = get_phase_completions("discovery", target_id="tgt-flow-a")
        flow_b = get_phase_completions("discovery", target_id="tgt-flow-b")

    assert flow_a["pct_complete"] > flow_b["pct_complete"]
    assert flow_b["pct_complete"] == 0


def test_maturity_scores_query_only_own_target(db):
    """Querying maturity scores for a target only returns that target's rows."""
    db.execute(
        "INSERT INTO zig_maturity_scores (pillar_slug, target_id, score) VALUES (?,?,?)",
        ("user", "tgt-only-mine", 0.9),
    )
    db.execute(
        "INSERT INTO zig_maturity_scores (pillar_slug, target_id, score) VALUES (?,?,?)",
        ("user", "tgt-someone-else", 0.1),
    )
    db.commit()

    rows = db.execute(
        "SELECT * FROM zig_maturity_scores WHERE target_id=?", ("tgt-only-mine",)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["score"] == 0.9
