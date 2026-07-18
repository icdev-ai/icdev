# CUI // SP-CTI
"""Pytest suite — ZIG SQL placeholder standardization (shx-db-04).

The storage layer (tools/db/storage.py StorageConnection) bidirectionally
normalizes ``?`` <-> ``%s`` for PostgreSQL, so a single SQL statement that
MIXES both placeholder styles only works by accident. Repo convention: author
with ``?``.

This suite has two jobs:

  1. Runtime — exercise the query paths that were touched (zig_activity_tracker
     IN-list + target_id filter, zig_pillar_scorer scoring, zig_assessor
     persistence/reconcile, bus_subscriber handlers) against a strict SQLite
     connection wrapper that does NOT translate ``%s`` -> ``?``. If any residual
     ``%s`` survived the standardization, SQLite raises and the test fails.

  2. Static regression — parse the four standardized source files and assert no
     SQL passed to ``execute()``/``executemany()`` contains a ``%s`` placeholder
     (the four files must author exclusively with ``?``), and that no single SQL
     string literal contains BOTH ``?`` and ``%s``.
"""
from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))


# ── Strict in-memory connection (NO %s -> ? translation) ─────────────────────

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
    description             TEXT,
    implementation_status   TEXT DEFAULT 'not_started'
);

CREATE TABLE IF NOT EXISTS zig_activities (
    id              TEXT PRIMARY KEY,
    capability_id   TEXT NOT NULL,
    phase           TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT
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

CREATE TABLE IF NOT EXISTS sc_assessments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id           TEXT,
    source_entity_id    TEXT
);

CREATE TABLE IF NOT EXISTS sc_threats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id   TEXT,
    is_stale    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS genesis_reflex_state (
    reflex_name  TEXT PRIMARY KEY,
    enabled      INTEGER DEFAULT 1,
    next_run_at  TEXT,
    updated_at   TEXT
);
"""


class _StrictConn:
    """sqlite3 wrapper that executes SQL VERBATIM.

    Unlike the sibling test's wrapper, this deliberately does NOT rewrite
    ``%s`` -> ``?``. SQLite only understands ``?`` — so any leftover ``%s`` in a
    touched code path raises ``sqlite3.OperationalError`` here, turning this into
    a live regression guard for the placeholder standardization.
    """

    def __init__(self):
        self._db = sqlite3.connect(":memory:")
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def execute(self, sql, params=()):
        return self._db.execute(sql, params)

    def commit(self):
        self._db.commit()

    def close(self):
        # No-op: keep the in-memory DB alive across the module's open/close cycles.
        pass

    def cursor(self):
        return self._db.cursor()


@pytest.fixture
def db():
    conn = _StrictConn()
    conn.execute("INSERT INTO zig_pillars (slug, name) VALUES (?,?)", ("user", "User"))
    conn.execute(
        "INSERT INTO zig_capabilities (id, pillar_slug, title, phase, maturity_level, "
        "implementation_status) VALUES (?,?,?,?,?,?)",
        ("cap-user-01", "user", "ICAM Discovery", "discovery", "basic", "not_started"),
    )
    for aid in ("act-u-01", "act-u-02", "act-u-03"):
        conn.execute(
            "INSERT INTO zig_activities (id, capability_id, phase, title) VALUES (?,?,?,?)",
            (aid, "cap-user-01", "discovery", f"Activity {aid}"),
        )
    conn.commit()
    yield conn


# ── 1. zig_activity_tracker — bulk IN-list lookup with target_id filter ──────


def test_activity_tracker_in_list_with_target_filter(db):
    """get_phase_completions builds an IN (?,?,...) list AND target_id=? — the
    exact statement that previously mixed ? with a trailing %s."""
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "icdev-self", "complete"),
    )
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-02", "icdev-self", "in_progress"),
    )
    # A different target's completion must not leak into the icdev-self result.
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-03", "tgt-other", "complete"),
    )
    db.commit()

    from tools.security_canvas.zig_activity_tracker import get_phase_completions

    with patch("tools.security_canvas.zig_activity_tracker.get_connection", lambda: db):
        result = get_phase_completions("discovery", target_id="icdev-self")

    assert result["total"] == 3
    assert result["complete"] == 1
    assert result["in_progress"] == 1
    # act-u-03 belongs to tgt-other, so it is not_started for icdev-self.
    assert result["not_started"] == 1


def test_activity_tracker_set_and_get_roundtrip(db):
    """set_activity_status (INSERT then UPDATE) and get_activity_completion use
    plain ``activity_id=?`` statements."""
    from tools.security_canvas.zig_activity_tracker import (
        get_activity_completion,
        set_activity_status,
    )

    with patch("tools.security_canvas.zig_activity_tracker.get_connection", lambda: db):
        set_activity_status("act-u-01", "in_progress", evidence_note="wip")
        set_activity_status("act-u-01", "complete", evidence_note="done")
        rec = get_activity_completion("act-u-01")

    assert rec["status"] == "complete"


# ── 2. zig_pillar_scorer — scoring query (nested IN-lists) ───────────────────


def test_pillar_scorer_scoring_query(db):
    """score_pillar chains pillar_slug=? -> activities IN (?...) ->
    completions IN (?...). All must be ``?``-only."""
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "icdev-self", "complete"),
    )
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-02", "icdev-self", "complete"),
    )
    db.commit()

    from tools.security_canvas.zig_pillar_scorer import score_pillar

    with patch("tools.security_canvas.zig_pillar_scorer.get_connection", lambda: db):
        result = score_pillar("user")

    assert result["slug"] == "user"
    assert result["capability_count"] == 1
    assert result["activity_count"] == 3
    assert result["complete_activities"] == 2
    assert result["score"] > 0.0


def test_pillar_scorer_empty_pillar(db):
    """A pillar with no capabilities returns a zero score without SQL error."""
    from tools.security_canvas.zig_pillar_scorer import score_pillar

    with patch("tools.security_canvas.zig_pillar_scorer.get_connection", lambda: db):
        result = score_pillar("device")

    assert result["score"] == 0.0
    assert result["capability_count"] == 0


# ── 3. zig_assessor — persistence + reconcile (IN-list, INSERT, UPDATE) ──────


def test_assessor_persist_scores(db):
    """_persist_scores INSERTs into zig_maturity_scores with a ``?`` tuple."""
    from tools.security_canvas.zig_assessor import _persist_scores

    with patch("tools.security_canvas.zig_assessor.get_connection", lambda: db):
        _persist_scores([
            {
                "slug": "user",
                "score": 0.5,
                "maturity_level": "basic",
                "capability_count": 1,
                "activity_count": 3,
                "complete_activities": 2,
            }
        ])

    row = db.execute(
        "SELECT pillar_slug, score FROM zig_maturity_scores WHERE pillar_slug=?", ("user",)
    ).fetchone()
    assert row is not None
    assert row["score"] == 0.5


def test_assessor_reconcile_capability_status(db):
    """reconcile_capability_status uses activities IN (?...) + UPDATE ...=? WHERE id=?."""
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-01", "icdev-self", "complete"),
    )
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-02", "icdev-self", "complete"),
    )
    db.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        ("act-u-03", "icdev-self", "complete"),
    )
    db.commit()

    from tools.security_canvas.zig_assessor import reconcile_capability_status

    with patch("tools.security_canvas.zig_assessor.get_connection", lambda: db):
        changed = reconcile_capability_status()

    assert changed == 1
    row = db.execute(
        "SELECT implementation_status FROM zig_capabilities WHERE id=?", ("cap-user-01",)
    ).fetchone()
    assert row["implementation_status"] == "implemented"


def test_assessor_identify_gaps(db):
    """_identify_gaps runs a pillar_slug=? + IN (...) filter query."""
    from tools.security_canvas.zig_assessor import _identify_gaps

    with patch("tools.security_canvas.zig_assessor.get_connection", lambda: db):
        gaps = _identify_gaps([{"slug": "user", "name": "User"}])

    assert len(gaps) == 1
    assert gaps[0]["capability_id"] == "cap-user-01"


# ── 4. bus_subscriber — pipeline_deployed handler (two connections) ──────────


def test_bus_subscriber_pipeline_deployed(db):
    """_handle_pipeline_deployed: sc_assessments SELECT (=?), sc_threats UPDATE
    IN (?...), and genesis_reflex_state upsert (VALUES ...,?,?)."""
    db.execute(
        "INSERT INTO sc_assessments (design_id, source_entity_id) VALUES (?,?)",
        ("design-1", "pipe-42"),
    )
    db.execute(
        "INSERT INTO sc_threats (design_id, is_stale) VALUES (?,?)",
        ("design-1", 0),
    )
    db.commit()

    from tools.security_canvas.bus_subscriber import _handle_pipeline_deployed

    with patch("tools.security_canvas.db.init_db.get_connection", lambda: db), \
            patch("tools.db.storage.get_connection", lambda: db):
        _handle_pipeline_deployed(
            "evt-1", "pdc", "pipeline_deployed", {"pipeline_id": "pipe-42"}
        )

    # Threat for design-1 must be marked stale.
    threat = db.execute(
        "SELECT is_stale FROM sc_threats WHERE design_id=?", ("design-1",)
    ).fetchone()
    assert threat["is_stale"] == 1

    # Genesis audit reflex must be queued (row upserted).
    reflex = db.execute(
        "SELECT reflex_name FROM genesis_reflex_state WHERE reflex_name=?", ("audit",)
    ).fetchone()
    assert reflex is not None


def test_bus_subscriber_no_pipeline_id_noops(db):
    """Empty pipeline_id short-circuits without touching the DB."""
    from tools.security_canvas.bus_subscriber import _handle_pipeline_deployed

    with patch("tools.security_canvas.db.init_db.get_connection", lambda: db), \
            patch("tools.db.storage.get_connection", lambda: db):
        _handle_pipeline_deployed("evt-2", "pdc", "pipeline_deployed", {})

    count = db.execute("SELECT COUNT(*) FROM genesis_reflex_state").fetchone()[0]
    assert count == 0


# ── 5. Static regression — no ? / %s mixing in the standardized files ────────

_STANDARDIZED_FILES = [
    "tools/security_canvas/zig_activity_tracker.py",
    "tools/security_canvas/bus_subscriber.py",
    "tools/security_canvas/zig_assessor.py",
    "tools/security_canvas/zig_pillar_scorer.py",
]


def _sql_literal(node: ast.AST) -> str | None:
    """Reconstruct the literal text of a SQL argument.

    Handles plain string constants, ``a + b`` concatenation, and f-strings
    (substitution sites are dropped to a sentinel; the surrounding literal text
    is what we inspect for placeholder style).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("\x00")  # substitution sentinel (an IN-list of ? params)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_literal(node.left)
        right = _sql_literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _iter_execute_sql(source: str):
    """Yield (lineno, sql_literal) for every execute()/executemany() call."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("execute", "executemany")
            and node.args
        ):
            sql = _sql_literal(node.args[0])
            if sql is not None:
                yield node.lineno, sql


@pytest.mark.parametrize("rel_path", _STANDARDIZED_FILES)
def test_no_pct_s_placeholder_in_standardized_files(rel_path):
    """Every execute() SQL in the four standardized files authors with ``?`` —
    a bare ``%s`` placeholder is the regression we are guarding against.

    (Python logging ``%s``/``%d`` format strings are NOT passed to execute() and
    are therefore out of scope for this scan.)
    """
    source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    offenders = [
        (lineno, sql) for lineno, sql in _iter_execute_sql(source) if "%s" in sql
    ]
    assert not offenders, (
        f"{rel_path}: execute() SQL still contains %s placeholders: "
        + "; ".join(f"line {ln}: {sql!r}" for ln, sql in offenders)
    )


@pytest.mark.parametrize("rel_path", _STANDARDIZED_FILES)
def test_no_mixed_placeholders_in_single_statement(rel_path):
    """No single SQL string literal contains BOTH ``?`` and ``%s``."""
    source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    mixed = [
        (lineno, sql)
        for lineno, sql in _iter_execute_sql(source)
        if "?" in sql and "%s" in sql
    ]
    assert not mixed, (
        f"{rel_path}: statement mixes ? and %s: "
        + "; ".join(f"line {ln}: {sql!r}" for ln, sql in mixed)
    )
