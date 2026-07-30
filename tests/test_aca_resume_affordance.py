# CUI // SP-CTI
"""aca-ux-03 — the hub must offer to resume real work, and show real progress.

The hub's "Active Missions" panel listed the first six missions by order_idx — not
the learner's own work — so a learner with missions in flight got no "continue where
you left off" anywhere in the product.

The card deliberately sequenced this AFTER aca-int-04. Before that fix, `in_progress`
was an artefact of page views (39 rows, 352 attempts, zero submissions), so a resume
control built then would have pointed at missions the learner had merely glanced at.
Migration 313 cleared those, so in_progress now means submitted work — and this
enforces that: a mission with no recorded step submissions is never offered as a
resume target, whatever its progress row says.

Separately, page.html reads `progress_map` and the hub route never passed it, so the
`{% if progress_map is defined %}` guard was always false and every card on the hub
showed "○ Start" regardless of state.
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def fa_conn(monkeypatch):
    from apps.forge_academy import db as fadb

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
          display_name TEXT, role TEXT DEFAULT 'swe', xp INTEGER DEFAULT 0,
          level TEXT DEFAULT 'recruit', tenant_id TEXT);
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
          title TEXT, tier INTEGER DEFAULT 1, is_active INTEGER DEFAULT 1,
          mission_type TEXT DEFAULT 'coding', xp_reward INTEGER DEFAULT 200);
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
          mission_id INTEGER, step_num INTEGER, title TEXT, step_type TEXT DEFAULT 'watch');
        CREATE TABLE fa_mission_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, mission_id INTEGER, status TEXT DEFAULT 'not_started',
          score INTEGER DEFAULT 0, xp_earned INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
          started_at TEXT, completed_at TEXT, UNIQUE(user_id, mission_id));
        CREATE TABLE fa_step_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, step_id INTEGER, status TEXT DEFAULT 'not_started',
          submission TEXT, score INTEGER DEFAULT 0, hints_used INTEGER DEFAULT 0,
          started_at TEXT, completed_at TEXT, UNIQUE(user_id, step_id));
        INSERT INTO fa_users (id, username, display_name) VALUES (1, 'l', 'L');
        """
    )
    # Three missions, three steps each.
    for mid, slug in ((1, "m-alpha"), (2, "m-beta"), (3, "m-gamma")):
        conn.execute(
            "INSERT INTO fa_missions (id, slug, title, tier) VALUES (?,?,?,1)",
            (mid, slug, slug.upper()),
        )
        for n in range(1, 4):
            conn.execute(
                "INSERT INTO fa_mission_steps (id, mission_id, step_num, title) "
                "VALUES (?,?,?,?)",
                (mid * 10 + n, mid, n, f"step {n}"),
            )
    conn.commit()
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)
    return conn


def _progress(conn, mission_id, status="in_progress", started="2026-07-01T00:00:00Z"):
    conn.execute(
        "INSERT OR REPLACE INTO fa_mission_progress "
        "(user_id, mission_id, status, started_at) VALUES (1,?,?,?)",
        (mission_id, status, started),
    )
    conn.commit()


def _step_done(conn, step_id, when):
    conn.execute(
        "INSERT OR REPLACE INTO fa_step_progress "
        "(user_id, step_id, status, completed_at) VALUES (1,?, 'completed', ?)",
        (step_id, when),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# resume_target
# ---------------------------------------------------------------------------

def test_no_resume_target_when_nothing_is_in_flight(fa_conn):
    from apps.forge_academy.db import resume_target

    assert resume_target(1) is None


def test_a_mission_with_no_submissions_is_not_offered(fa_conn):
    """The aca-int-04 lesson: a progress row alone is not evidence of work."""
    from apps.forge_academy.db import resume_target

    _progress(fa_conn, 1)  # in_progress, but zero step submissions
    assert resume_target(1) is None, (
        "a mission the learner only opened must not be offered as 'resume'"
    )


def test_the_most_recently_worked_mission_wins(fa_conn):
    from apps.forge_academy.db import resume_target

    _progress(fa_conn, 1)
    _progress(fa_conn, 2)
    _step_done(fa_conn, 11, "2026-07-01T10:00:00Z")   # alpha, older
    _step_done(fa_conn, 21, "2026-07-05T10:00:00Z")   # beta, newer
    target = resume_target(1)
    assert target is not None
    assert target["slug"] == "m-beta"


def test_a_completed_mission_is_never_offered(fa_conn):
    from apps.forge_academy.db import resume_target

    _progress(fa_conn, 1, status="completed")
    _step_done(fa_conn, 11, "2026-07-09T10:00:00Z")
    assert resume_target(1) is None


def test_resume_target_reports_step_counts(fa_conn):
    """'Continue' is only useful with a sense of how far in they are."""
    from apps.forge_academy.db import resume_target

    _progress(fa_conn, 1)
    _step_done(fa_conn, 11, "2026-07-01T10:00:00Z")
    _step_done(fa_conn, 12, "2026-07-02T10:00:00Z")
    t = resume_target(1)
    assert t["steps_done"] == 2
    assert t["steps_total"] == 3
    for key in ("slug", "title", "tier"):
        assert key in t


def test_an_attempted_but_unpassed_step_still_counts_as_activity(fa_conn):
    """A learner who tried and failed is mid-mission — that is exactly who to resume."""
    from apps.forge_academy.db import resume_target

    _progress(fa_conn, 1)
    fa_conn.execute(
        "INSERT INTO fa_step_progress (user_id, step_id, status, completed_at) "
        "VALUES (1, 11, 'attempted', NULL)"
    )
    fa_conn.commit()
    t = resume_target(1)
    assert t is not None and t["slug"] == "m-alpha"
    assert t["steps_done"] == 0, "an attempt is activity, not a completed step"


# ---------------------------------------------------------------------------
# Per-mission step progress for the cards
# ---------------------------------------------------------------------------

def test_step_progress_map_reports_done_and_total(fa_conn):
    from apps.forge_academy.db import mission_step_progress

    _step_done(fa_conn, 11, "2026-07-01T10:00:00Z")
    m = mission_step_progress(1, [1, 2, 3])
    assert m[1] == {"done": 1, "total": 3}
    assert m[2] == {"done": 0, "total": 3}


def test_step_progress_map_handles_an_unknown_mission(fa_conn):
    from apps.forge_academy.db import mission_step_progress

    m = mission_step_progress(1, [999])
    assert m.get(999, {"done": 0, "total": 0})["total"] == 0


def test_step_progress_map_is_empty_for_no_ids(fa_conn):
    from apps.forge_academy.db import mission_step_progress

    assert mission_step_progress(1, []) == {}


# ---------------------------------------------------------------------------
# The hub actually uses them
# ---------------------------------------------------------------------------

def test_hub_passes_resume_and_progress_to_the_template():
    import inspect

    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.hub)
    assert "resume_target" in src, "the hub computes no resume target"
    assert "progress_map" in src, (
        "page.html reads progress_map; the hub never passed it, so every card "
        "showed 'Start'"
    )


def test_the_hub_template_renders_a_resume_control():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    html = (root / "tools" / "dashboard" / "templates" / "forge_academy"
            / "page.html").read_text(encoding="utf-8")
    assert "resume" in html.lower()
    assert "steps_done" in html or "steps_total" in html, (
        "the resume control should say how far in the learner is"
    )
