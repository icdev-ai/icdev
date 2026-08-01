# CUI // SP-CTI
"""aca-ux-07 — the total and the rank basis are two numbers, so name both.

aca-int-07 moved rank onto earned XP and deliberately left the displayed total alone.
That leaves 1815 XP printed beside "250 XP to Operative" with neither figure labelled,
which reads as broken arithmetic to every learner who ever collects a daily login —
not just to the one account that was demoted when migration 315 landed.

The decision not to ship a one-time demotion notice, and to label the standing split
instead, is recorded in docs/features/forge-academy-aca-ux-07-rank-xp-split.md.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest

from _academy_conn import academy_conn

SCHEMA = """
CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
                       xp INTEGER DEFAULT 0, level TEXT DEFAULT 'recruit');
CREATE TABLE fa_xp_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                           xp_delta INTEGER, reason TEXT, source_type TEXT,
                           source_id INTEGER, is_attendance INTEGER DEFAULT 0,
                           verified INTEGER DEFAULT 1, note TEXT, created_at TEXT,
                           classification TEXT, tenant_id TEXT);
"""

TEMPLATES = (pathlib.Path(__file__).resolve().parent.parent / "tools" / "dashboard"
             / "templates" / "forge_academy")


@pytest.fixture()
def wired(monkeypatch):
    """The live shape on 2026-08-01: 1815 total, 1565 of it 43 daily logins."""
    conn = academy_conn()
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO fa_users (id, username, xp) VALUES (1, 'learner', 1815)")
    for _ in range(43):
        conn.execute(
            "INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, is_attendance) "
            "VALUES (1, %s, 'daily_login', 1)", (1565 // 43,))
    # Make attendance land exactly on 1565 despite the integer division above.
    conn.execute("INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, is_attendance) "
                 "VALUES (1, %s, 'daily_login', 1)", (1565 - (1565 // 43) * 43,))
    conn.execute(
        "INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, source_type, "
        "source_id, is_attendance) VALUES (1, 250, 'step_pass', 'step', 93, 0)")
    conn.commit()
    fadb = importlib.import_module("apps.forge_academy.db")
    monkeypatch.setattr(fadb, "get_connection", lambda *a, **k: conn)
    return importlib.import_module("apps.forge_academy.blueprint"), conn


def test_the_context_carries_both_numbers_not_just_the_rank_basis(wired):
    bp, _ = wired
    ctx = bp._level_ctx({"id": 1, "xp": 1815})
    assert ctx["total_xp"] == 1815
    assert ctx["earned_xp"] == 250
    assert ctx["attendance_xp"] == 1565


def test_the_split_always_reconciles_to_the_printed_total(wired):
    """Derived by subtraction on purpose: a second SUM could disagree with the total
    the page prints next to it, which is the confusion this card exists to end."""
    bp, _ = wired
    ctx = bp._level_ctx({"id": 1, "xp": 1815})
    assert ctx["earned_xp"] + ctx["attendance_xp"] == ctx["total_xp"]


def test_attendance_never_renders_negative(wired):
    """A ledger that over-counts fa_users.xp must not make the UI assert nonsense."""
    bp, conn = wired
    conn.execute("UPDATE fa_users SET xp = 100 WHERE id=1")
    conn.commit()
    assert bp._level_ctx({"id": 1, "xp": 100})["attendance_xp"] == 0


def test_an_absent_user_still_gets_the_keys_the_templates_read(wired):
    """page.html reads level_ctx.attendance_xp unguarded; a missing key is a
    silently-empty banner, not an error, so assert the shape."""
    bp, _ = wired
    for ctx in (bp._level_ctx(None), bp._level_ctx({})):
        assert ctx["total_xp"] == 0
        assert ctx["earned_xp"] == 0
        assert ctx["attendance_xp"] == 0
        assert ctx["level"] is not None


def test_an_unmigrated_database_reports_no_attendance_rather_than_guessing(wired,
                                                                          monkeypatch):
    """Before migration 315 there is no ledger. The fallback ranks off the total, so
    the split must collapse to 'all of it earned' rather than claim a phantom
    attendance figure."""
    bp, _ = wired
    fadb = importlib.import_module("apps.forge_academy.db")
    monkeypatch.setattr(fadb, "earned_xp", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(bp, "earned_xp", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    ctx = bp._level_ctx({"id": 1, "xp": 1815})
    assert ctx["earned_xp"] == 1815
    assert ctx["attendance_xp"] == 0


@pytest.mark.parametrize("template", ["page.html", "mission.html"])
def test_the_figure_beside_the_rank_bar_is_the_earned_one(template):
    """Both bars fill from level_ctx.pct, which is earned-based. Printing
    fa_user.xp above them is what made the bar look stuck at 1815 XP."""
    src = (TEMPLATES / template).read_text(encoding="utf-8")
    # Only the markup — the <style> block names the same CSS classes and would
    # otherwise decide where this test thinks the bar starts.
    markup = src[src.index("{% block content %}"):]
    bar_class = "fa-xp-bar-wrap" if template == "page.html" else "fa-xp-mini"
    bar = markup[markup.index(bar_class):]
    bar = bar[:bar.index("{% endif %}")]
    assert "level_ctx.earned_xp" in bar, f"{template} rank bar is not earned-labelled"
    assert "fa_user.xp" not in bar, (
        f"{template} still prints the running total beside an earned-XP bar"
    )


def test_the_hub_states_the_attendance_split_in_a_live_region():
    """Standing text, not a dismissible one-time notice — the split is permanent.
    role="status" so it is announced when the hub re-renders after the daily-login
    award, matching faEnrolNotice (aca-trn-06)."""
    src = (TEMPLATES / "page.html").read_text(encoding="utf-8")
    markup = src[src.index("{% block content %}"):]
    guard = "{% if level_ctx and level_ctx.attendance_xp %}"
    assert guard in markup, "the split line is not guarded on attendance being non-zero"
    block = markup[markup.index(guard):]
    block = block[:block.index("{% endif %}")]
    assert 'role="status"' in block, "the split line is not an announced live region"
    assert "level_ctx.total_xp" in block and "level_ctx.attendance_xp" in block
    assert "daily-login attendance" in block


def test_the_profile_no_longer_calls_the_running_total_earned():
    """'Total Earned' asserted the opposite of the truth: since aca-int-07 'earned'
    means is_attendance=0, and this figure includes 1565 XP of logins."""
    src = (TEMPLATES / "profile.html").read_text(encoding="utf-8")
    assert ">Total Earned<" not in src
    assert ">Total XP<" in src
    assert "level_ctx.earned_xp" in src


@pytest.mark.parametrize("template", ["page.html", "mission.html", "profile.html"])
def test_the_icdev_mirror_matches(template):
    """A template edited only under tools/ is served by the repo but not by the
    installed package."""
    root = pathlib.Path(__file__).resolve().parent.parent
    a = (TEMPLATES / template).read_text(encoding="utf-8")
    b = (root / "icdev" / "tools" / "dashboard" / "templates" / "forge_academy"
         / template).read_text(encoding="utf-8")
    assert a == b, f"{template} drifted from its icdev/ mirror"
