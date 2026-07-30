# CUI // SP-CTI
"""Opening a mission page must not change the learner's progress.

blueprint.mission_runner called start_mission() on every GET, and start_mission
did `SET status='in_progress', attempts=attempts+1` unconditionally. Two defects
fell out of that:

  * `attempts` counted page views, not attempts. Production evidence: 39
    fa_mission_progress rows in_progress with 352 total attempts (m01 alone 26)
    while fa_step_progress was COMPLETELY EMPTY â€” every recorded attempt was a
    page load and nobody had ever submitted a step.
  * re-opening a COMPLETED mission to review it reverted it to in_progress,
    silently withdrawing the completion that certificate gates count
    (db.py check_cert_eligibility counts status='completed').

A write on GET is also wrong on its own terms: it breaks caching and makes link
prefetch mutate state.

Progress is now a consequence of work: record_mission_attempt() is called from
the submit path, and it never moves a mission out of 'completed'.
"""
from __future__ import annotations

import inspect
import sqlite3

import pytest


@pytest.fixture
def fa_conn(monkeypatch):
    """Minimal fa_* schema: one learner, one mission, two steps."""
    from apps.forge_academy import db as fadb

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
          display_name TEXT, xp INTEGER DEFAULT 0, level TEXT DEFAULT 'recruit',
          tenant_id TEXT);
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
          title TEXT, tier INTEGER DEFAULT 1, xp_reward INTEGER DEFAULT 200,
          is_active INTEGER DEFAULT 1);
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
          mission_id INTEGER, step_num INTEGER, title TEXT,
          step_type TEXT DEFAULT 'watch', test_code_path TEXT,
          config_schema_json TEXT DEFAULT '{}', xp_partial INTEGER DEFAULT 50,
          skill_tag TEXT);
        CREATE TABLE fa_mission_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, mission_id INTEGER, status TEXT DEFAULT 'not_started',
          score INTEGER DEFAULT 0, xp_earned INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
          started_at TEXT, completed_at TEXT, UNIQUE(user_id, mission_id));
        CREATE TABLE fa_step_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, step_id INTEGER, status TEXT DEFAULT 'not_started',
          submission TEXT, score INTEGER DEFAULT 0, hints_used INTEGER DEFAULT 0,
          started_at TEXT, completed_at TEXT, UNIQUE(user_id, step_id));

        INSERT INTO fa_users (id, username, display_name) VALUES (1, 'l', 'L');
        INSERT INTO fa_missions (id, slug, title) VALUES (1, 'm-test', 'Test');
        INSERT INTO fa_mission_steps (id, mission_id, step_num, title) VALUES (1, 1, 1, 'A');
        INSERT INTO fa_mission_steps (id, mission_id, step_num, title) VALUES (2, 1, 2, 'B');
        """
    )
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)
    return conn


def _progress(conn):
    return conn.execute(
        "SELECT status, attempts, completed_at FROM fa_mission_progress "
        "WHERE user_id=1 AND mission_id=1"
    ).fetchone()


# ---------------------------------------------------------------------------
# The GET handler must not write
# ---------------------------------------------------------------------------

def test_mission_runner_does_not_start_a_mission():
    """The route body must contain no progress-mutating call."""
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.mission_runner)
    assert "start_mission(" not in src, "GET wrote progress"
    assert "record_mission_attempt(" not in src, "GET must not record an attempt either"


def test_start_mission_is_gone_from_the_db_module():
    """Renamed so no future caller reaches for a name that implies a GET-time write."""
    from apps.forge_academy import db as fadb

    assert not hasattr(fadb, "start_mission"), (
        "start_mission implied 'opening it starts it'; use record_mission_attempt"
    )
    assert hasattr(fadb, "record_mission_attempt")


# ---------------------------------------------------------------------------
# record_mission_attempt semantics
# ---------------------------------------------------------------------------

def test_first_attempt_creates_the_row(fa_conn):
    from apps.forge_academy.db import record_mission_attempt

    assert _progress(fa_conn) is None
    record_mission_attempt(1, 1)
    row = _progress(fa_conn)
    assert row["status"] == "in_progress"
    assert row["attempts"] == 1


def test_further_attempts_increment(fa_conn):
    from apps.forge_academy.db import record_mission_attempt

    for _ in range(3):
        record_mission_attempt(1, 1)
    assert _progress(fa_conn)["attempts"] == 3


def test_a_completed_mission_is_never_reverted(fa_conn):
    """The defect that silently withdrew certificate eligibility."""
    from apps.forge_academy.db import complete_mission, record_mission_attempt

    record_mission_attempt(1, 1)
    complete_mission(1, 1, score=100)
    assert _progress(fa_conn)["status"] == "completed"

    record_mission_attempt(1, 1)  # learner returns to review or tinker
    row = _progress(fa_conn)
    assert row["status"] == "completed", "revisiting withdrew the completion"
    assert row["completed_at"] is not None, "completion timestamp must survive"
    assert row["attempts"] == 2, "the attempt is still counted"


# ---------------------------------------------------------------------------
# The submit path is what records the attempt
# ---------------------------------------------------------------------------

def test_submit_records_the_mission_attempt():
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_step_submit)
    assert "record_mission_attempt(" in src, (
        "progress must be a consequence of submitting work"
    )


# ---------------------------------------------------------------------------
# migration 313 â€” reconcile the phantom rows already in production
# ---------------------------------------------------------------------------

def test_migration_313_exists_and_is_scoped_to_evidence():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    matches = list((root / "tools" / "db" / "migrations").glob("313_*.sql"))
    assert matches, "expected migration 313 reconciling fa_mission_progress"
    sql = matches[0].read_text(encoding="utf-8")
    assert "fa_mission_progress" in sql
    assert "fa_step_progress" in sql, "reconciliation must key on real step evidence"
    # Must not touch completions.
    assert "completed" in sql, "the rule must explicitly protect completed rows"


def test_migration_313_reconciles_phantom_rows(fa_conn):
    """Apply the migration body to the fixture and check the documented rule.

    Rule: a mission_progress row with no step_progress evidence for that mission
    was never really started â€” remove it. Rows with evidence keep their row but
    have `attempts` reset to the number of recorded step submissions.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sql = next((root / "tools" / "db" / "migrations").glob("313_*.sql")).read_text(
        encoding="utf-8"
    )

    # Phantom: 26 attempts, zero step evidence (exactly the m01 shape).
    fa_conn.execute(
        "INSERT INTO fa_mission_progress (user_id, mission_id, status, attempts) "
        "VALUES (1, 1, 'in_progress', 26)"
    )
    # Real: a second mission with one recorded step submission.
    fa_conn.execute("INSERT INTO fa_missions (id, slug, title) VALUES (2, 'm-real', 'R')")
    fa_conn.execute(
        "INSERT INTO fa_mission_steps (id, mission_id, step_num, title) VALUES (9, 2, 1, 'S')"
    )
    fa_conn.execute(
        "INSERT INTO fa_mission_progress (user_id, mission_id, status, attempts) "
        "VALUES (1, 2, 'in_progress', 14)"
    )
    fa_conn.execute(
        "INSERT INTO fa_step_progress (user_id, step_id, status) VALUES (1, 9, 'completed')"
    )
    fa_conn.commit()

    fa_conn.executescript(sql)

    assert _progress(fa_conn) is None, "phantom row should be removed"
    real = fa_conn.execute(
        "SELECT status, attempts FROM fa_mission_progress WHERE user_id=1 AND mission_id=2"
    ).fetchone()
    assert real is not None, "a mission with real evidence must be kept"
    assert real["attempts"] == 1, "attempts must reflect recorded submissions, not views"


def test_migration_313_leaves_completed_missions_alone(fa_conn):
    """A completed mission with no step rows is still a completion â€” do not delete it."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sql = next((root / "tools" / "db" / "migrations").glob("313_*.sql")).read_text(
        encoding="utf-8"
    )

    fa_conn.execute(
        "INSERT INTO fa_mission_progress "
        "(user_id, mission_id, status, attempts, completed_at) "
        "VALUES (1, 1, 'completed', 5, '2026-01-01T00:00:00Z')"
    )
    fa_conn.commit()

    fa_conn.executescript(sql)

    row = _progress(fa_conn)
    assert row is not None, "a completed mission must never be deleted"
    assert row["status"] == "completed"
