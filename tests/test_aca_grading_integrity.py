# CUI // SP-CTI
"""FORGE Academy assessment integrity — the verdict must be the server's.

Every test below fails on the code as it stood. /api/academy/step/submit read
`passed = bool(data.get("passed", True))` — defaulting to True — and then
completed the step and paid XP with no server-side check, so one crafted POST per
mission produced a certificate that /academy/verify/<token> publicly attested to.
The coding grader accepted the *test* from the graded party (mission.html posted
`STEPS[i].test_code` straight back) and passed any script that exits 0, so a step
with no stored test was passed by `print(1)`. A wrong reflect answer submitted
`passed=true` and the key sat in the DOM as data-correct.

These are the assertions of refusal: what the Academy must decline to credit.
"""
from __future__ import annotations

import inspect
import json

from _academy_conn import academy_conn
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "tools" / "dashboard" / "templates" / "forge_academy"


# ---------------------------------------------------------------------------
# Fixtures — a minimal fa_* schema, in-memory, matching apps/forge_academy/db.py
# ---------------------------------------------------------------------------

@pytest.fixture
def fa_conn(monkeypatch):
    """In-memory fa_* schema with one learner, one mission and three steps."""
    from apps.forge_academy import db as fadb

    conn = academy_conn(":memory:")
    conn.executescript(
        """
        CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
          display_name TEXT, email TEXT, role TEXT DEFAULT 'unset',
          role_type TEXT DEFAULT 'guided', tier_unlocked INT DEFAULT 1,
          xp INTEGER NOT NULL DEFAULT 0, level TEXT DEFAULT 'recruit', guild_id INT,
          streak_days INT DEFAULT 0, last_active TEXT, tenant_id TEXT, created_at TEXT);
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
          title TEXT, tagline TEXT, tier INT DEFAULT 1, topic TEXT,
          role_filter TEXT DEFAULT 'all', mission_type TEXT DEFAULT 'coding',
          xp_reward INTEGER DEFAULT 200, prereq_slugs_json TEXT DEFAULT '[]',
          order_idx INT DEFAULT 0, difficulty TEXT, estimated_minutes INT,
          source_credit TEXT, is_active INT DEFAULT 1, status TEXT DEFAULT 'active',
          updated_at TEXT, created_at TEXT);
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
          mission_id INT, step_num INT, title TEXT, step_type TEXT DEFAULT 'coding',
          content_path TEXT, starter_code_path TEXT, test_code_path TEXT,
          config_schema_json TEXT DEFAULT '{}', xp_partial INTEGER DEFAULT 50,
          skill_tag TEXT, hint_allowed INT DEFAULT 1, estimated_seconds INT);
        CREATE TABLE fa_mission_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INT, mission_id INT, status TEXT DEFAULT 'not_started',
          score INT DEFAULT 0, xp_earned INT DEFAULT 0, attempts INT DEFAULT 0,
          started_at TEXT, completed_at TEXT, UNIQUE(user_id, mission_id));
        CREATE TABLE fa_step_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INT, step_id INT, status TEXT DEFAULT 'not_started', submission TEXT,
          score INT DEFAULT 0, hints_used INT DEFAULT 0, started_at TEXT,
          completed_at TEXT, UNIQUE(user_id, step_id));
        CREATE TABLE fa_user_skills (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INT,
          skill_slug TEXT, unlocked_at TEXT, UNIQUE(user_id, skill_slug));
        CREATE TABLE fa_achievements (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
          title TEXT, description TEXT, icon TEXT, xp_bonus INT DEFAULT 0, rarity TEXT,
          criteria_json TEXT);
        CREATE TABLE fa_user_achievements (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INT, achievement_slug TEXT, earned_at TEXT,
          UNIQUE(user_id, achievement_slug));

        INSERT INTO fa_users (id, username, display_name, xp, level)
          VALUES (1, 'learner', 'Learner', 0, 'recruit');
        INSERT INTO fa_missions (id, slug, title, xp_reward) VALUES (1, 'm-test', 'Test', 400);
        -- step 1: coding WITH a stored test
        INSERT INTO fa_mission_steps
          (id, mission_id, step_num, title, step_type, test_code_path, xp_partial)
          VALUES (1, 1, 1, 'Coded', 'coding', 'tier-test/steps/step1_test.py', 50);
        -- step 2: coding with NO stored test (must be ungradeable, not auto-pass)
        INSERT INTO fa_mission_steps
          (id, mission_id, step_num, title, step_type, test_code_path, xp_partial)
          VALUES (2, 1, 2, 'Untested', 'coding', '', 50);
        -- step 3: reflect with a real question
        INSERT INTO fa_mission_steps
          (id, mission_id, step_num, title, step_type, config_schema_json, xp_partial)
          VALUES (3, 1, 3, 'Checked', 'reflect',
                  '{"question": "2+2?", "options": [{"text": "3"}, {"text": "4", "correct": true}],
                    "explanation": "Arithmetic."}', 50);
        """
    )
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)
    return conn


@pytest.fixture
def grading(monkeypatch):
    """The grading module with code execution stubbed to a deterministic verdict."""
    from apps.forge_academy import grading as mod
    return mod


# ---------------------------------------------------------------------------
# aca-int-02 — the grader must use the stored test, never the request body
# ---------------------------------------------------------------------------

def test_coding_step_with_no_stored_test_cannot_be_credited(fa_conn, grading, monkeypatch):
    """`print(1)` exits 0. Without a stored test that used to score 100."""
    monkeypatch.setattr(
        grading, "run_code",
        lambda code, test_code="": {"passed": True, "stdout": "", "stderr": "", "exit_code": 0},
    )
    verdict = grading.grade_step(step_id=2, submission="print(1)")
    assert verdict["passed"] is False
    assert verdict["reason"] == "ungraded_no_test"


def test_coding_step_grades_against_the_stored_test(fa_conn, grading, monkeypatch):
    """The test body must come from test_code_path on the step row."""
    seen = {}

    def fake_run(code, test_code=""):
        seen["test_code"] = test_code
        return {"passed": True, "stdout": "ok", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(grading, "run_code", fake_run)
    monkeypatch.setattr(grading, "load_test_code", lambda p: "assert answer() == 42")

    verdict = grading.grade_step(step_id=1, submission="def answer(): return 42")
    assert verdict["passed"] is True
    assert seen["test_code"] == "assert answer() == 42"


def test_grade_step_takes_no_test_code_argument(grading):
    """A caller must not be able to hand the grader a test at all."""
    params = inspect.signature(grading.grade_step).parameters
    assert "test_code" not in params, "the grader must source its own test"


def test_code_run_route_no_longer_reads_test_code_from_the_body(fa_conn):
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_code_run)
    assert 'data.get("test_code"' not in src, "test body must not come from the request"
    assert "step_id" in src, "the route must identify the step and load its own test"


# ---------------------------------------------------------------------------
# aca-int-03 — a wrong reflect answer must fail, and the key must leave the DOM
# ---------------------------------------------------------------------------

def test_reflect_wrong_answer_fails(fa_conn, grading):
    verdict = grading.grade_step(step_id=3, submission="", chosen_option=0)
    assert verdict["passed"] is False
    assert verdict["explanation"] == "Arithmetic."


def test_reflect_correct_answer_passes(fa_conn, grading):
    verdict = grading.grade_step(step_id=3, submission="", chosen_option=1)
    assert verdict["passed"] is True


def test_reflect_template_does_not_emit_the_answer_key():
    html = (TEMPLATES / "partials" / "_step_reflect.html").read_text(encoding="utf-8")
    assert "data-correct" not in html, "the key was readable in page source"
    assert "opt.correct" not in html, "the key must not reach the client at all"


def test_reflect_template_submits_the_chosen_option(fa_conn):
    html = (TEMPLATES / "partials" / "_step_reflect.html").read_text(encoding="utf-8")
    assert "submitStep(idx, 'incorrect', true)" not in html, "wrong answers paid full XP"
    assert "chosen_option" in html or "checkAnswer" in html


# ---------------------------------------------------------------------------
# aca-int-01 — the submit route must not take the verdict or the payout from the client
# ---------------------------------------------------------------------------

def test_submit_does_not_default_passed_to_true(fa_conn):
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_step_submit)
    assert 'data.get("passed", True)' not in src, "the client decided its own verdict"
    assert 'data.get("base_xp"' not in src, "payout must come from the step row"
    assert 'data.get("mission_xp"' not in src, "payout must come from the mission row"
    assert 'data.get("mission_complete")' not in src, "completion must be derived"


def test_submit_uses_the_server_grader(fa_conn):
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_step_submit)
    assert "grade_step" in src, "the route must obtain a server-side verdict"


def test_client_step_payload_carries_no_test_code_or_answer_key(fa_conn, monkeypatch):
    """mission.html dumps the step list into JS — it must be sanitised first."""
    from apps.forge_academy import grading

    steps = [
        {
            "id": 1, "step_type": "coding", "test_code": "assert answer() == 42",
            "starter_code": "def answer(): ...", "title": "Coded",
            "config_schema": {"question": "2+2?",
                              "options": [{"text": "3"}, {"text": "4", "correct": True}],
                              "explanation": "Arithmetic."},
        },
    ]
    safe = grading.client_safe_steps(steps)
    blob = json.dumps(safe)
    assert "assert answer() == 42" not in blob, "the test leaked to the browser"
    assert "correct" not in blob, "the answer key leaked to the browser"
    assert "Arithmetic." not in blob, "the explanation pre-empts the question"
    # Still usable by the runner.
    assert safe[0]["starter_code"] == "def answer(): ..."
    assert safe[0]["title"] == "Coded"


def test_mission_template_does_not_post_the_test_code(fa_conn):
    html = (TEMPLATES / "mission.html").read_text(encoding="utf-8")
    assert "test_code: STEPS" not in html, "the graded party supplied the test"
    assert "passed: passed" not in html, "the client sent its own verdict"


# ---------------------------------------------------------------------------
# aca-int-05 — a failed step is not 'completed', and it is not paid
# ---------------------------------------------------------------------------

def test_failed_step_is_not_marked_completed(fa_conn):
    from apps.forge_academy import db as fadb

    fadb.complete_step(1, 1, submission="wrong", passed=False)
    row = fa_conn.execute(
        "SELECT status, score FROM fa_step_progress WHERE user_id=1 AND step_id=1"
    ).fetchone()
    assert row["status"] != "completed", "a failure was recorded as a completion"
    assert row["score"] == 0


def test_passing_step_is_marked_completed(fa_conn):
    from apps.forge_academy import db as fadb

    fadb.complete_step(1, 1, submission="right", passed=True)
    row = fa_conn.execute(
        "SELECT status, score FROM fa_step_progress WHERE user_id=1 AND step_id=1"
    ).fetchone()
    assert row["status"] == "completed"
    assert row["score"] == 100


def test_a_later_failure_does_not_downgrade_a_completed_step(fa_conn):
    """Mastery already demonstrated must survive a subsequent experiment."""
    from apps.forge_academy import db as fadb

    fadb.complete_step(1, 1, submission="right", passed=True)
    fadb.complete_step(1, 1, submission="oops", passed=False)
    row = fa_conn.execute(
        "SELECT status, score FROM fa_step_progress WHERE user_id=1 AND step_id=1"
    ).fetchone()
    assert row["status"] == "completed"
    assert row["score"] == 100


def test_mission_completion_is_derived_from_verified_steps(fa_conn, grading):
    """mission_complete came from the client's isLast; it must come from the data."""
    from apps.forge_academy import db as fadb

    assert grading.mission_is_complete(1, 1) is False
    for sid in (1, 2, 3):
        fadb.complete_step(1, sid, submission="x", passed=True)
    assert grading.mission_is_complete(1, 1) is True


# ---------------------------------------------------------------------------
# Acknowledgement step types are honest about not being assessments
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("step_type", ["watch", "configure", "verify", "deploy"])
def test_acknowledgement_steps_are_marked_unassessed(fa_conn, grading, step_type):
    fa_conn.execute(
        "UPDATE fa_mission_steps SET step_type=? WHERE id=2", (step_type,)
    )
    verdict = grading.grade_step(step_id=2, submission="acknowledged")
    assert verdict["passed"] is True, "reading steps stay completable"
    assert verdict["assessed"] is False, "but they must not claim to be assessments"


def test_assessed_step_types_are_declared(grading):
    assert "coding" in grading.ASSESSED_STEP_TYPES
    assert "reflect" in grading.ASSESSED_STEP_TYPES
    assert "watch" not in grading.ASSESSED_STEP_TYPES


def test_unknown_step_id_is_refused(fa_conn, grading):
    verdict = grading.grade_step(step_id=9999, submission="x")
    assert verdict["passed"] is False
    assert verdict["reason"] == "unknown_step"
