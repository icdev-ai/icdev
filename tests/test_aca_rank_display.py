# CUI // SP-CTI
"""aca-int-07 — the rank on screen must be the rank the ledger supports.

Migration 316 corrected fa_users.level to 'recruit' and every academy page went on
rendering 'Operative'. _level_ctx — the ONLY thing the UI consults for rank, used in
eight routes — recomputed it from fa_user["xp"], the running total, so the stored
column the migration fixed was never what the profile displayed.

Live at the time: 1715 total, of which 1465 was 41 daily logins. The rank on screen
had been bought by showing up.
"""
from __future__ import annotations

import importlib

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


@pytest.fixture()
def wired(monkeypatch):
    """The live shape: 1715 total, 1465 of it attendance."""
    conn = academy_conn()
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO fa_users (id, username, xp) VALUES (1, 'learner', 1715)")
    for i in range(41):
        conn.execute(
            "INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, is_attendance) "
            "VALUES (1, %s, 'daily_login', 1)", (36 if i < 40 else 25,))
    conn.execute(
        "INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, source_type, "
        "source_id, is_attendance) VALUES (1, 250, 'step_pass', 'step', 93, 0)")
    conn.commit()
    fadb = importlib.import_module("apps.forge_academy.db")
    monkeypatch.setattr(fadb, "get_connection", lambda *a, **k: conn)
    bp = importlib.import_module("apps.forge_academy.blueprint")
    return bp, fadb, conn


def test_rank_comes_from_earned_xp_not_the_total(wired):
    bp, fadb, _ = wired
    ctx = bp._level_ctx({"id": 1, "xp": 1715})
    assert ctx["level"]["slug"] == fadb.xp_to_level(250)["slug"]
    assert ctx["level"]["slug"] != fadb.xp_to_level(1715)["slug"], (
        "41 daily logins still bought a rank on screen"
    )


def test_progress_to_next_rank_is_also_earned_based(wired):
    """Otherwise the bar fills from attendance while the label refuses to advance."""
    bp, _, _ = wired
    ctx = bp._level_ctx({"id": 1, "xp": 1715})
    assert ctx["xp_current"] <= 250


def test_a_learner_with_only_attendance_holds_the_entry_rank(wired):
    bp, fadb, conn = wired
    conn.execute("DELETE FROM fa_xp_ledger WHERE is_attendance=0")
    conn.commit()
    ctx = bp._level_ctx({"id": 1, "xp": 1715})
    assert ctx["level"]["slug"] == fadb.xp_to_level(0)["slug"]


def test_an_absent_user_does_not_crash_the_page(wired):
    bp, _, _ = wired
    assert bp._level_ctx(None)["level"] is not None
    assert bp._level_ctx({})["level"] is not None


def test_the_displayed_total_is_untouched(wired):
    """Attendance is excluded from RANK, not confiscated from the profile."""
    _, _, conn = wired
    assert conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"] == 1715


def test_the_ui_has_exactly_one_place_that_decides_rank():
    """Eight routes call _level_ctx. A second computation would drift from it."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent / "apps" / "forge_academy"
           / "blueprint.py").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert body.count("xp_to_next_level(") <= 2, (
        "rank is computed outside _level_ctx; it will drift from the ledger"
    )
