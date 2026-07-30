# CUI // SP-CTI
"""aca-int-07 — the displayed rank must match the rank the ledger supports.

fa_users.level is a cache written only inside update_user_xp, so moving rank onto
earned XP fixed nothing for learners who already had one. Straight after migration
315 applied to the live board:

    fa_users.xp     1715
    attendance      1465   41 daily logins
    earned           250   2 completed steps
    fa_users.level  'operative'   <-- still the total-based rank

The fix was in place and completely invisible.
"""
from __future__ import annotations

import importlib
import json
import pathlib

import pytest

from _academy_conn import academy_conn

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = REPO_ROOT / "tools" / "db" / "migrations" / "316_fa_rank_from_earned_xp"

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
def live_shape(monkeypatch):
    """The board as measured: one learner, 1465 attendance, 250 earned."""
    conn = academy_conn()
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO fa_users (id, username, xp, level) "
                 "VALUES (1, 'learner', 1715, 'operative')")
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
    return conn, fadb


def _up():
    spec = importlib.util.spec_from_file_location(
        "mig316", MIGRATION / "up.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.up


def _level(conn):
    return conn.execute("SELECT level FROM fa_users WHERE id=1").fetchone()["level"]


def test_the_stale_rank_is_recomputed_from_earned_xp(live_shape):
    conn, fadb = live_shape
    assert _level(conn) == "operative", "fixture should start from the live state"
    _up()(conn=conn)
    assert _level(conn) == fadb.xp_to_level(250)["slug"]
    assert _level(conn) != "operative", (
        "1465 XP of logging in still bought a rank"
    )


def test_the_total_is_left_alone(live_shape):
    """Attendance is excluded from rank, not confiscated."""
    conn, _ = live_shape
    _up()(conn=conn)
    assert conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"] == 1715


def test_it_is_idempotent(live_shape):
    conn, _ = live_shape
    _up()(conn=conn)
    first = _level(conn)
    _up()(conn=conn)
    assert _level(conn) == first


def test_a_learner_with_no_ledger_drops_to_the_entry_rank(live_shape):
    """The truthful answer when nothing has been demonstrated."""
    conn, fadb = live_shape
    conn.execute("DELETE FROM fa_xp_ledger")
    conn.commit()
    _up()(conn=conn)
    assert _level(conn) == fadb.xp_to_level(0)["slug"]


def test_the_thresholds_are_not_duplicated_into_sql():
    """Re-expressing xp_to_level() as a CASE ladder would drift from it.

    This is why the migration is Python rather than the .sql the other academy
    migrations use.
    """
    src = (MIGRATION / "up.py").read_text(encoding="utf-8")
    assert "xp_to_level" in src, "the thresholds must come from the Python function"
    # "CASE WHEN", not "CASE": the migration's own docstring explains why it avoids a
    # SQL CASE ladder, and a bare substring check matches that prose.
    assert "CASE WHEN" not in src.upper()


def test_the_migration_declares_itself():
    meta = json.loads((MIGRATION / "meta.json").read_text(encoding="utf-8"))
    assert meta["id"] == "316_fa_rank_from_earned_xp"
    assert meta["targets"] == ["icdev.db"]
