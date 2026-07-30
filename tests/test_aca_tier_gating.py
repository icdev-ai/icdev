# CUI // SP-CTI
"""Tier gating: percentage thresholds, locked-but-readable, and no credit while locked.

fa_users.tier_unlocked was set to 1 for every learner and enforced nowhere. All 104
Tier-2 missions were listed and openable (m-cortex-01 had 16 recorded attempts), and
the hub showed a "TIER 1" tile implying a gate that did not exist.

The trap this design had to avoid: a "100% of the previous tier" rule makes the next
tier PERMANENTLY unreachable, because Tier 1 contains m-chat-agent-interview with
zero steps — deliberately "Coming soon" per fga-wire-06, so it can never be
completed. 12 of 13 Tier-1 missions are completable, 95 of 104 in Tier 2.

The same latent bug is in check_cert_eligibility's tier1_complete gate, which counts
ALL tier-1 missions and therefore made the Foundation certificate unobtainable too.
Fixed here as well.

Chosen model: percentage of COMPLETABLE missions in the prior tier; locked missions
stay readable but earn nothing.
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
          level TEXT DEFAULT 'recruit', tier_unlocked INTEGER DEFAULT 1, tenant_id TEXT);
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
          title TEXT, tier INTEGER DEFAULT 1, role_filter TEXT DEFAULT 'all',
          mission_type TEXT DEFAULT 'coding', xp_reward INTEGER DEFAULT 200,
          is_active INTEGER DEFAULT 1);
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
          mission_id INTEGER, step_num INTEGER, title TEXT,
          step_type TEXT DEFAULT 'watch', test_code_path TEXT DEFAULT '',
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
        CREATE TABLE fa_certificates (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, cert_tier TEXT, cert_label TEXT, token TEXT, issued_at TEXT);

        INSERT INTO fa_users (id, username, display_name) VALUES (1, 'l', 'L');
        """
    )
    # Tier 1: 5 missions, 4 completable (one is a zero-step "Coming soon").
    for i in range(1, 6):
        conn.execute(
            "INSERT INTO fa_missions (id, slug, title, tier) VALUES (?,?,?,1)",
            (i, f"t1-{i}", f"T1 {i}"),
        )
        if i != 5:  # mission 5 gets no steps
            conn.execute(
                "INSERT INTO fa_mission_steps (mission_id, step_num, title) VALUES (?,1,'s')",
                (i,),
            )
    # Tier 2: 2 completable missions.
    for i in (10, 11):
        conn.execute(
            "INSERT INTO fa_missions (id, slug, title, tier) VALUES (?,?,?,2)",
            (i, f"t2-{i}", f"T2 {i}"),
        )
        conn.execute(
            "INSERT INTO fa_mission_steps (mission_id, step_num, title) VALUES (?,1,'s')",
            (i,),
        )
    conn.commit()
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)
    return conn


def _complete(conn, mission_ids):
    for mid in mission_ids:
        conn.execute(
            "INSERT OR REPLACE INTO fa_mission_progress "
            "(user_id, mission_id, status, completed_at) VALUES (1,?, 'completed','now')",
            (mid,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Threshold model
# ---------------------------------------------------------------------------

def test_thresholds_are_declared_as_percentages():
    from apps.forge_academy.constants import TIER_UNLOCK_PCT

    assert isinstance(TIER_UNLOCK_PCT, dict)
    assert 1 not in TIER_UNLOCK_PCT, "tier 1 is the entry point and must never gate"
    assert 0 < TIER_UNLOCK_PCT[2] <= 100
    assert 0 < TIER_UNLOCK_PCT[3] <= 100


def test_tier_one_is_always_unlocked(fa_conn):
    from apps.forge_academy.db import is_tier_unlocked

    assert is_tier_unlocked(1, 1) is True


def test_percentage_counts_only_completable_missions(fa_conn):
    """The zero-step mission must not sit in the denominator forever."""
    from apps.forge_academy.db import tier_progress

    prog = tier_progress(1)
    assert prog[1]["completable"] == 4, "the zero-step mission must be excluded"
    assert prog[1]["total"] == 5, "but still reported, so the UI can explain it"


def test_tier_two_locks_below_the_threshold(fa_conn):
    from apps.forge_academy.db import is_tier_unlocked, tier_progress

    _complete(fa_conn, [1])  # 1 of 4 = 25%
    assert tier_progress(1)[1]["pct"] == 25
    assert is_tier_unlocked(1, 2) is False


def test_tier_two_unlocks_at_the_threshold(fa_conn):
    """4 completable, 80% required -> 4/4 = 100% clears it; 3/4 = 75% does not."""
    from apps.forge_academy.db import is_tier_unlocked

    _complete(fa_conn, [1, 2, 3])  # 75%
    assert is_tier_unlocked(1, 2) is False
    _complete(fa_conn, [1, 2, 3, 4])  # 100%
    assert is_tier_unlocked(1, 2) is True


def test_completing_the_uncompletable_mission_is_not_required(fa_conn):
    """The whole point: 100% of *completable* is reachable."""
    from apps.forge_academy.db import is_tier_unlocked

    _complete(fa_conn, [1, 2, 3, 4])
    assert is_tier_unlocked(1, 2) is True, (
        "gate must not require the zero-step mission, which can never be completed"
    )


def test_a_tier_with_no_completable_missions_does_not_deadlock(fa_conn):
    """Guard against dividing by zero into a permanent lock."""
    from apps.forge_academy.db import is_tier_unlocked

    fa_conn.execute("DELETE FROM fa_mission_steps")
    fa_conn.commit()
    # No tier-1 mission is completable; tier 2 must not be permanently unreachable.
    assert is_tier_unlocked(1, 2) is True


def test_progress_reports_what_the_ui_needs_to_explain_the_lock(fa_conn):
    from apps.forge_academy.db import tier_progress

    _complete(fa_conn, [1])
    t2 = tier_progress(1)[2]
    for key in ("unlocked", "required_pct", "gating_tier"):
        assert key in t2, f"missing {key} — the UI cannot state the requirement"
    assert t2["unlocked"] is False
    assert t2["gating_tier"] == 1


# ---------------------------------------------------------------------------
# Locked-but-readable
# ---------------------------------------------------------------------------

def test_mission_page_still_renders_a_locked_mission(fa_conn):
    """Locked means 'earns nothing', not 'you may not read it'."""
    import inspect

    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.mission_runner)
    assert "tier_locked" in src, "the runner must tell the template the tier is locked"
    assert "abort(403" not in src and "redirect(url_for(\"forge_academy.missions" not in src, \
        "a locked mission must remain readable"


def test_the_runner_template_shows_a_locked_notice():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    html = (root / "tools" / "dashboard" / "templates" / "forge_academy"
            / "mission.html").read_text(encoding="utf-8")
    assert "tier_locked" in html
    assert "locked" in html.lower()


def test_the_browser_marks_locked_cards():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    html = (root / "tools" / "dashboard" / "templates" / "forge_academy"
            / "missions.html").read_text(encoding="utf-8")
    assert "is_locked" in html, "cards must show the locked state before the click"


# ---------------------------------------------------------------------------
# No credit while locked — enforced server-side
# ---------------------------------------------------------------------------

def test_submit_refuses_credit_on_a_locked_mission(fa_conn):
    import inspect

    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_step_submit)
    assert "is_tier_unlocked" in src, (
        "the gate must be enforced where credit is granted, not only in the UI"
    )


# ---------------------------------------------------------------------------
# The certificate gate had the same unreachable-denominator bug
# ---------------------------------------------------------------------------

def test_tier1_certificate_gate_excludes_uncompletable_missions(fa_conn, monkeypatch):
    """It counted all 13 tier-1 missions, so the Foundation cert was unobtainable."""
    from apps.forge_academy import constants, db as fadb

    monkeypatch.setattr(
        constants, "CERT_BY_KEY",
        {"foundation": {"label": "Foundation", "requirements": {"tier1_complete": True}}},
        raising=False,
    )
    _complete(fa_conn, [1, 2, 3, 4])  # every completable tier-1 mission
    res = fadb.check_cert_eligibility(1, "foundation")
    gate = next(g for g in res["gates"] if "Tier 1" in g["name"])
    assert gate["met"] is True, (
        "completing every completable Tier-1 mission must satisfy the gate"
    )
    assert "4" in gate["detail"], "detail should reflect the completable denominator"
