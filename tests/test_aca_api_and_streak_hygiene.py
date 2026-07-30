# CUI // SP-CTI
"""API contract and streak/hint hygiene (aca-hyg-03, aca-hyg-04).

Four defects found while probing the Academy:

  * GET /api/academy/guild/<id> returned 200 {"members": [], "total_xp": 0} for a
    guild id that does not exist, so a client cannot tell an empty guild from a
    missing one.
  * GET /api/academy/learning-path serialised raw mission rows, leaking
    domain_classes_json, is_active, order_idx, ontology_id and created_at to the
    browser.
  * the login streak never advanced. _touch_streak compares a UTC date against a
    LOCAL one: `today` comes from datetime.now(timezone.utc) while `yesterday` comes
    from date.today(). Whenever the machine's local date differs from the UTC date
    the comparison fails and the streak resets to 1 forever. Live proof: the one
    learner had 41 daily logins across 41 distinct days with 35 consecutive-day
    pairs — including the last six days unbroken — and streak_days stuck at 1, so
    every streak bonus ever awarded was min(1,7)*10 instead of up to 70.
  * hint_allowed was 1 on all 212 steps including watch/reflect steps, where there
    is nothing to hint, and the runner ignored the column entirely.
"""
from __future__ import annotations


from _academy_conn import academy_conn
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def fa_conn(monkeypatch):
    from apps.forge_academy import db as fadb

    conn = academy_conn(":memory:")
    conn.executescript(
        """
        -- aca-int-07: every XP award now writes its provenance row in the same
        -- transaction as the balance change, so a fixture without this table is a
        -- fixture where XP cannot be awarded at all.
        CREATE TABLE fa_xp_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, xp_delta INTEGER, reason TEXT, source_type TEXT,
            source_id INTEGER, is_attendance INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 1, note TEXT, created_at TEXT,
            classification TEXT, tenant_id TEXT);
        CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
          display_name TEXT, role TEXT DEFAULT 'swe', xp INTEGER DEFAULT 0,
          level TEXT DEFAULT 'recruit', streak_days INTEGER DEFAULT 0,
          last_active TEXT, tenant_id TEXT);
        CREATE TABLE fa_guilds (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
          description TEXT, invite_code TEXT UNIQUE, created_by INTEGER);
        CREATE TABLE fa_guild_members (id INTEGER PRIMARY KEY AUTOINCREMENT,
          guild_id INTEGER, user_id INTEGER, role TEXT DEFAULT 'member');
        CREATE TABLE fa_daily_logins (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, login_date TEXT, xp_awarded INTEGER DEFAULT 0,
          UNIQUE(user_id, login_date));
        INSERT INTO fa_users (id, username, display_name, xp) VALUES (1,'l','L',100);
        INSERT INTO fa_guilds (id, name, invite_code) VALUES (7, 'Real Guild', 'CODE7');
        INSERT INTO fa_guild_members (guild_id, user_id) VALUES (7, 1);
        """
    )
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)
    return conn


# ---------------------------------------------------------------------------
# aca-hyg-03 — guild 404 and learning-path projection
# ---------------------------------------------------------------------------

def test_guild_stats_distinguishes_missing_from_empty(fa_conn):
    from apps.forge_academy.db import get_guild_stats

    assert get_guild_stats(7) is not None, "a real guild must resolve"
    assert get_guild_stats(9999) is None, (
        "a nonexistent guild must be None so the route can 404"
    )


def test_an_empty_but_real_guild_is_not_confused_with_a_missing_one(fa_conn):
    from apps.forge_academy.db import get_guild_stats

    fa_conn.execute("DELETE FROM fa_guild_members WHERE guild_id=7")
    fa_conn.commit()
    stats = get_guild_stats(7)
    assert stats is not None, "the guild still exists; it is merely empty"
    assert stats["members"] == []
    assert stats["total_xp"] == 0


def test_guild_route_returns_404_for_an_unknown_guild(fa_conn):
    import inspect

    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_guild_stats)
    assert "404" in src, "the route must translate a missing guild into a 404"


def test_learning_path_projects_instead_of_dumping_rows():
    """Internal columns must not reach the browser."""
    from apps.forge_academy.blueprint import _learner_mission_view

    row = {
        "id": 3, "slug": "m-x", "title": "X", "tagline": "t", "tier": 2,
        "mission_type": "coding", "xp_reward": 300, "difficulty": "advanced",
        "estimated_minutes": 40, "topic": "rag", "is_available": True,
        # everything below is internal and must be dropped
        "domain_classes_json": "[]", "is_active": 1, "order_idx": 8,
        "ontology_id": "", "created_at": "2026-06-12", "role_filter": "swe",
        "prereq_slugs_json": "[]", "source_credit": "derived", "status": "active",
        "updated_at": None, "competency": "",
    }
    out = _learner_mission_view(row)
    for leaked in ("domain_classes_json", "is_active", "order_idx", "ontology_id",
                   "created_at", "updated_at", "status", "source_credit"):
        assert leaked not in out, f"{leaked} leaked to the client"
    for kept in ("slug", "title", "tier", "xp_reward", "difficulty"):
        assert kept in out, f"{kept} is needed by the client"


def test_learning_path_route_uses_the_projection():
    import inspect

    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_learning_path)
    assert "_learner_mission_view" in src


# ---------------------------------------------------------------------------
# aca-hyg-04 — the streak that never advanced
# ---------------------------------------------------------------------------

def test_a_consecutive_day_login_advances_the_streak(fa_conn):
    """The live failure: 35 consecutive-day pairs and streak_days stuck at 1."""
    from apps.forge_academy import db as fadb

    yesterday_utc = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    fa_conn.execute(
        "UPDATE fa_users SET streak_days=4, last_active=? WHERE id=1", (yesterday_utc,)
    )
    fa_conn.commit()
    fadb._touch_streak(fa_conn, dict(fa_conn.execute(
        "SELECT * FROM fa_users WHERE id=1").fetchone()))
    assert fa_conn.execute(
        "SELECT streak_days FROM fa_users WHERE id=1").fetchone()[0] == 5


def test_a_skipped_day_resets_the_streak(fa_conn):
    from apps.forge_academy import db as fadb

    three_days = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    fa_conn.execute(
        "UPDATE fa_users SET streak_days=9, last_active=? WHERE id=1", (three_days,)
    )
    fa_conn.commit()
    fadb._touch_streak(fa_conn, dict(fa_conn.execute(
        "SELECT * FROM fa_users WHERE id=1").fetchone()))
    assert fa_conn.execute(
        "SELECT streak_days FROM fa_users WHERE id=1").fetchone()[0] == 1


def test_two_visits_on_the_same_day_do_not_double_count(fa_conn):
    from apps.forge_academy import db as fadb

    for _ in range(3):
        fadb._touch_streak(fa_conn, dict(fa_conn.execute(
            "SELECT * FROM fa_users WHERE id=1").fetchone()))
    assert fa_conn.execute(
        "SELECT streak_days FROM fa_users WHERE id=1").fetchone()[0] == 1


def test_the_streak_comparison_uses_one_clock():
    """Mixing datetime.now(utc) with date.today() is the whole bug."""
    import inspect

    from apps.forge_academy import db as fadb

    src = inspect.getsource(fadb._touch_streak)
    # Strip the docstring: it explains the bug and necessarily names date.today().
    body = src.split('"""')[-1]
    assert "date.today()" not in body, (
        "date.today() is local; last_active is UTC — the comparison must use one clock"
    )
    assert "now_utc" in body, "both sides of the comparison must come from one UTC clock"


def test_streak_bonus_reflects_the_stored_streak(fa_conn):
    from apps.forge_academy.constants import XP_DAILY_LOGIN_BASE, XP_STREAK_BONUS_PER_DAY
    from apps.forge_academy.gamification import award_daily_login

    fa_conn.execute("UPDATE fa_users SET streak_days=5 WHERE id=1")
    fa_conn.commit()
    res = award_daily_login(1)
    assert res is not None
    assert res["streak"] == 5
    assert res["bonus"] == 5 * XP_STREAK_BONUS_PER_DAY
    assert res["xp"] == XP_DAILY_LOGIN_BASE + res["bonus"]


def test_a_zero_streak_still_counts_the_day_as_one(fa_conn):
    """streak_days defaults to 0, and logging in is day one — not day zero."""
    from apps.forge_academy.gamification import award_daily_login

    fa_conn.execute("UPDATE fa_users SET streak_days=0 WHERE id=1")
    fa_conn.commit()
    res = award_daily_login(1)
    assert res["streak"] >= 1
    assert res["bonus"] > 0, "a zero-streak learner used to get no bonus at all"


def test_the_streak_bonus_is_capped(fa_conn):
    from apps.forge_academy.constants import XP_STREAK_BONUS_PER_DAY
    from apps.forge_academy.gamification import award_daily_login

    fa_conn.execute("UPDATE fa_users SET streak_days=400 WHERE id=1")
    fa_conn.commit()
    assert award_daily_login(1)["bonus"] == 7 * XP_STREAK_BONUS_PER_DAY


def test_a_second_login_the_same_day_awards_nothing(fa_conn):
    from apps.forge_academy.gamification import award_daily_login

    assert award_daily_login(1) is not None
    assert award_daily_login(1) is None


# ---------------------------------------------------------------------------
# aca-hyg-04 — hint_allowed must mean something
# ---------------------------------------------------------------------------

def test_hint_allowed_is_derived_per_step_type():
    from apps.forge_academy.content_loader import hint_allowed_for

    assert hint_allowed_for("coding") is True
    assert hint_allowed_for("design") is True
    assert hint_allowed_for("watch") is False, "nothing to hint on a reading step"
    assert hint_allowed_for("reflect") is False, "a hint would be the answer"


def test_the_runner_honours_hint_allowed():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    html = (root / "tools" / "dashboard" / "templates" / "forge_academy" / "partials"
            / "_step_coding.html").read_text(encoding="utf-8")
    assert "hint_allowed" in html, "the column was set and then ignored by the UI"


def test_the_hint_route_refuses_a_step_that_disallows_hints():
    import inspect

    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_coach_hint)
    assert "hint_allowed" in src, (
        "enforced server-side, or the flag is decoration like tier_unlocked was"
    )
