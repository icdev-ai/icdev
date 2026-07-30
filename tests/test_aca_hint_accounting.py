# CUI // SP-CTI
"""Hints must be counted by the server, charged once, and priced honestly.

Three defects, found together because they are the same economy:

  * hintsUsed lived only in the browser and goStep() reset it to 0 on every
    sidebar click (mission.html), so taking three hints then navigating away and
    back erased the penalty — and restored both the 1.5x "perfect" mission
    multiplier and the no_hints_needed achievement (aca-int-06).
  * api_coach_hint called update_user_xp(user_id, -10) immediately AND the submit
    path separately applied XP_MULT_WITH_HINTS (0.75 instead of 1.5) minus
    XP_HINT_PENALTY per hint. The hint was charged TWICE. On a 50 XP step one hint
    cost 10 up front and then paid 27 instead of 75 — 58 XP total against a UI
    label of "-10 XP".
  * that label. The real first-hint cost was ~5x what the button said (aca-ux-02).

The submit-time multiplier is now the single pricing mechanism, because it is what
constants.py documents and it does not charge a learner who never submits.
"""
from __future__ import annotations

import inspect

from _academy_conn import academy_conn

import pytest


@pytest.fixture
def fa_conn(monkeypatch):
    from apps.forge_academy import db as fadb

    conn = academy_conn(":memory:")
    conn.executescript(
        """
        CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
          xp INTEGER DEFAULT 0, level TEXT DEFAULT 'recruit');
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
          mission_id INTEGER, step_num INTEGER, title TEXT,
          step_type TEXT DEFAULT 'coding', test_code_path TEXT DEFAULT '',
          config_schema_json TEXT DEFAULT '{}', xp_partial INTEGER DEFAULT 50,
          skill_tag TEXT, hint_allowed INTEGER DEFAULT 1);
        CREATE TABLE fa_step_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, step_id INTEGER, status TEXT DEFAULT 'not_started',
          submission TEXT, score INTEGER DEFAULT 0, hints_used INTEGER DEFAULT 0,
          started_at TEXT, completed_at TEXT, UNIQUE(user_id, step_id));

        INSERT INTO fa_users (id, username, xp) VALUES (1, 'l', 1000);
        INSERT INTO fa_mission_steps (id, mission_id, step_num, title, xp_partial)
          VALUES (1, 1, 1, 'A', 50);
        """
    )
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)
    return conn


def _hints(conn, step_id=1):
    row = conn.execute(
        "SELECT hints_used FROM fa_step_progress WHERE user_id=1 AND step_id=?",
        (step_id,),
    ).fetchone()
    return row["hints_used"] if row else None


# ---------------------------------------------------------------------------
# aca-int-06 — the server counts hints
# ---------------------------------------------------------------------------

def test_recording_a_hint_creates_progress_and_counts_it(fa_conn):
    from apps.forge_academy.db import record_hint

    assert _hints(fa_conn) is None
    assert record_hint(1, 1) == 1
    assert _hints(fa_conn) == 1


def test_hints_accumulate_and_survive_navigation(fa_conn):
    """The browser used to zero its counter; the database cannot be navigated away."""
    from apps.forge_academy.db import record_hint

    assert record_hint(1, 1) == 1
    assert record_hint(1, 1) == 2
    assert record_hint(1, 1) == 3
    assert _hints(fa_conn) == 3


def test_recording_a_hint_does_not_disturb_a_completed_step(fa_conn):
    from apps.forge_academy.db import complete_step, record_hint

    complete_step(1, 1, submission="ok", passed=True)
    record_hint(1, 1)
    row = fa_conn.execute(
        "SELECT status, score, hints_used FROM fa_step_progress "
        "WHERE user_id=1 AND step_id=1"
    ).fetchone()
    assert row["status"] == "completed"
    assert row["score"] == 100
    assert row["hints_used"] == 1


def test_submit_reads_hints_from_the_database_not_the_request(fa_conn):
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_step_submit)
    assert 'data.get("hints_used"' not in src, "client-reported hint count was trusted"
    assert "stored_hints_used" in src or "get_step_progress" in src


def test_hint_route_requires_a_step_id(fa_conn):
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_coach_hint)
    assert "step_id" in src, "the server cannot count a hint it cannot attribute"
    assert "record_hint" in src


# ---------------------------------------------------------------------------
# The double charge
# ---------------------------------------------------------------------------

def test_hint_route_no_longer_deducts_xp_directly(fa_conn):
    """One pricing mechanism, not two."""
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_coach_hint)
    assert "update_user_xp" not in src, (
        "hint was charged here AND again by the submit multiplier"
    )


def test_taking_a_hint_leaves_xp_untouched_until_submission(fa_conn):
    """A learner who reads a hint and never submits should not lose XP."""
    from apps.forge_academy.db import get_user, record_hint

    before = get_user(1)["xp"]
    record_hint(1, 1)
    assert get_user(1)["xp"] == before


# ---------------------------------------------------------------------------
# aca-ux-02 — the quoted price must be the real price
# ---------------------------------------------------------------------------

def test_projected_hint_cost_matches_the_award_formula(fa_conn):
    """Whatever we tell the learner must be what award_step_xp actually does."""
    from apps.forge_academy.gamification import award_step_xp, projected_step_xp

    base = 50
    no_hint = projected_step_xp(base, hints_used=0)
    one_hint = projected_step_xp(base, hints_used=1)

    # Cross-check against the real award path (XP actually credited).
    before = fa_conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"]
    award_step_xp(1, base, hints_used=0, step_type="coding")
    awarded_no_hint = (
        fa_conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"] - before
    )
    assert awarded_no_hint == no_hint

    before = fa_conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"]
    award_step_xp(1, base, hints_used=1, step_type="coding")
    awarded_one_hint = (
        fa_conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"] - before
    )
    assert awarded_one_hint == one_hint


def test_the_real_first_hint_cost_is_not_ten(fa_conn):
    """Documents the magnitude the old label hid: 75 -> 27 on a 50 XP step."""
    from apps.forge_academy.gamification import projected_step_xp

    assert projected_step_xp(50, hints_used=0) == 75
    assert projected_step_xp(50, hints_used=1) == 27
    assert projected_step_xp(50, hints_used=0) - projected_step_xp(50, hints_used=1) == 48


def test_projected_xp_never_goes_negative(fa_conn):
    from apps.forge_academy.gamification import projected_step_xp

    assert projected_step_xp(10, hints_used=99) == 0


def test_hint_response_reports_the_projected_cost(fa_conn):
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_coach_hint)
    assert "projected" in src, "the route must tell the client the real numbers"


def test_templates_no_longer_hardcode_a_ten_xp_label():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    tmpl = root / "tools" / "dashboard" / "templates" / "forge_academy"
    coding = (tmpl / "partials" / "_step_coding.html").read_text(encoding="utf-8")
    mission = (tmpl / "mission.html").read_text(encoding="utf-8")
    assert "-10 XP" not in coding and "−10 XP" not in coding
    assert "-10 XP" not in mission and "−10 XP" not in mission
