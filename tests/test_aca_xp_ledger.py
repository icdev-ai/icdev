# CUI // SP-CTI
"""aca-int-07 — XP has to say what earned it.

Before this, XP lived in exactly one place: fa_users.xp, a running total mutated by
`SET xp = xp + ?` from eleven call sites, with nothing anywhere linking an award to
the work behind it. Measured on the live board:

    fa_users.xp                       1715
    sum(fa_daily_logins.xp_awarded)   1465   across 41 logins
    earned from graded work            250   2 completed steps

85% of the learner's rank was attendance, and rank was computed from the total — so
logging in for 41 days outranked demonstrating anything. A duplicated, negative or
fabricated award was indistinguishable from a real one after the fact, which is why a
certificate citing that total asserted competence with nothing behind it.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

from _academy_conn import academy_conn

SCHEMA = """
CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
                       xp INTEGER DEFAULT 0, level TEXT DEFAULT 'recruit',
                       streak_days INTEGER DEFAULT 0);
CREATE TABLE fa_xp_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                           xp_delta INTEGER, reason TEXT, source_type TEXT,
                           source_id INTEGER, is_attendance INTEGER DEFAULT 0,
                           verified INTEGER DEFAULT 1, note TEXT,
                           created_at TEXT, classification TEXT, tenant_id TEXT);
INSERT INTO fa_users (id, username, xp) VALUES (1, 'learner', 0);
"""


@pytest.fixture()
def fadb(monkeypatch):
    conn = academy_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    mod = importlib.import_module("apps.forge_academy.db")
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: conn)
    return mod, conn


def _rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM fa_xp_ledger ORDER BY id").fetchall()]


# --------------------------------------------------------------------------
# the award cannot happen without the record
# --------------------------------------------------------------------------

def test_an_award_writes_a_ledger_row(fadb):
    mod, conn = fadb
    mod.update_user_xp(1, 50, reason="step_pass", source_type="step", source_id=93)
    rows = _rows(conn)
    assert len(rows) == 1
    assert rows[0]["xp_delta"] == 50
    assert rows[0]["reason"] == "step_pass"
    assert rows[0]["source_id"] == 93, "a ledger row without a source is not evidence"


def test_reason_has_no_default(fadb):
    """A default would let a new award slip in unattributed and still look correct.

    That is exactly how fa_users.xp accumulated 1715 points no record explains. With
    no default this is a TypeError at the call site.
    """
    mod, _ = fadb
    with pytest.raises(TypeError):
        mod.update_user_xp(1, 50)


def test_an_unknown_reason_is_refused(fadb):
    """Adding a category means deciding whether it counts toward rank."""
    mod, _ = fadb
    with pytest.raises(ValueError):
        mod.update_user_xp(1, 50, reason="because")


def test_the_balance_and_the_ledger_move_together(fadb):
    mod, conn = fadb
    for delta, reason in [(50, "step_pass"), (36, "daily_login"), (200, "achievement")]:
        mod.update_user_xp(1, delta, reason=reason)
    balance = conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"]
    ledger = conn.execute(
        "SELECT SUM(xp_delta) AS s FROM fa_xp_ledger WHERE user_id=1").fetchone()["s"]
    assert balance == ledger == 286


# --------------------------------------------------------------------------
# attendance is not achievement
# --------------------------------------------------------------------------

def test_daily_login_is_flagged_as_attendance(fadb):
    mod, conn = fadb
    mod.update_user_xp(1, 36, reason="daily_login")
    assert _rows(conn)[0]["is_attendance"] == 1


def test_step_and_mission_awards_are_not_attendance(fadb):
    mod, conn = fadb
    mod.update_user_xp(1, 50, reason="step_pass")
    mod.update_user_xp(1, 400, reason="mission_complete")
    assert [r["is_attendance"] for r in _rows(conn)] == [0, 0]


def test_earned_xp_excludes_attendance(fadb):
    """The live shape: mostly logins, a little work."""
    mod, conn = fadb
    for _ in range(41):
        mod.update_user_xp(1, 36, reason="daily_login")
    mod.update_user_xp(1, 250, reason="step_pass", source_type="step", source_id=93)
    total = conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"]
    assert total == 1726
    assert mod.earned_xp(1) == 250


def test_rank_is_computed_from_earned_xp_not_the_total(fadb):
    """The whole point. 41 logins must not outrank demonstrating nothing."""
    mod, conn = fadb
    for _ in range(41):
        mod.update_user_xp(1, 36, reason="daily_login")
    attendance_only = conn.execute(
        "SELECT level FROM fa_users WHERE id=1").fetchone()["level"]
    assert mod.earned_xp(1) == 0
    assert attendance_only == mod.xp_to_level(0)["slug"], (
        "1476 XP of pure attendance still bought a rank"
    )


def test_attendance_still_counts_toward_the_displayed_total(fadb):
    """Excluded from rank, not deleted — the profile total must still reconcile."""
    mod, conn = fadb
    mod.update_user_xp(1, 36, reason="daily_login")
    assert conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"] == 36


# --------------------------------------------------------------------------
# provenance survives the call chain
# --------------------------------------------------------------------------

def test_award_step_xp_records_the_step(fadb, monkeypatch):
    mod, conn = fadb
    gam = importlib.import_module("apps.forge_academy.gamification")
    monkeypatch.setattr(gam, "get_user", lambda uid: {"level": "recruit"})
    monkeypatch.setattr(gam, "get_user_achievements", lambda uid: [])
    gam.award_step_xp(1, 50, step_id=93)
    row = _rows(conn)[0]
    assert row["reason"] == "step_pass"
    assert row["source_id"] == 93, (
        "award_step_xp knew which step it was paying for and did not record it"
    )


def test_award_mission_xp_records_the_mission(fadb):
    mod, conn = fadb
    gam = importlib.import_module("apps.forge_academy.gamification")
    gam.award_mission_xp(1, 400, mission_id=7)
    row = _rows(conn)[0]
    assert row["reason"] == "mission_complete"
    assert row["source_id"] == 7


# --------------------------------------------------------------------------
# append-only
# --------------------------------------------------------------------------

def test_the_ledger_is_registered_append_only():
    """Corrections must be compensating rows, or the ledger stops being evidence."""
    import pathlib
    hook = (pathlib.Path(__file__).resolve().parent.parent
            / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
    block = hook[hook.index("APPEND_ONLY_TABLES"):]
    assert '"fa_xp_ledger"' in block[:8000]


def test_a_correction_is_a_new_row_not_an_edit(fadb):
    mod, conn = fadb
    mod.update_user_xp(1, 100, reason="step_pass", source_type="step", source_id=93)
    mod.update_user_xp(1, -100, reason="adjustment", note="reversal of step 93")
    rows = _rows(conn)
    assert len(rows) == 2, "a reversal must leave both rows standing"
    assert sum(r["xp_delta"] for r in rows) == 0
    assert conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()["xp"] == 0


# --------------------------------------------------------------------------
# the migration
# --------------------------------------------------------------------------

def test_the_migration_reconciles_to_the_balance_and_is_idempotent():
    """Reconstruct only what has a surviving source, and name the rest.

    Reproduces the live shape: 1715 total, 41 logins summing to 1465, two completed
    steps. The 150 that neither explains is the speed-bonus multiplier, which is not
    recoverable from stored state — it becomes one opening_balance row flagged
    unverified rather than being spread across plausible-looking reasons.
    """
    import pathlib

    from tools.db.storage import translate_sql

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE fa_users (id INTEGER PRIMARY KEY, xp INTEGER, level TEXT);
    CREATE TABLE fa_daily_logins (id INTEGER PRIMARY KEY, user_id INT, xp_awarded INT);
    CREATE TABLE fa_step_progress (id INTEGER PRIMARY KEY, user_id INT, step_id INT,
                                   status TEXT);
    CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY, xp_partial INT);
    INSERT INTO fa_users VALUES (1, 1715, 'operative');
    """)
    for i, x in enumerate([36] * 40 + [25], 1):
        db.execute("INSERT INTO fa_daily_logins (id,user_id,xp_awarded) VALUES (?,?,?)",
                   (i, 1, x))
    for sid in (91, 93):
        db.execute("INSERT INTO fa_mission_steps VALUES (?,50)", (sid,))
    db.execute("INSERT INTO fa_step_progress VALUES (1,1,91,'completed')")
    db.execute("INSERT INTO fa_step_progress VALUES (2,1,93,'completed')")
    db.commit()

    path = (pathlib.Path(__file__).resolve().parent.parent
            / "tools" / "db" / "migrations" / "315_fa_xp_ledger.sql")
    body = "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines()
                     if not ln.strip().startswith("--"))
    statements = [s.strip() for s in body.split(";") if s.strip()]

    def apply():
        for s in statements:
            db.execute(translate_sql(s, "sqlite"))
        db.commit()

    apply()
    total = db.execute("SELECT SUM(xp_delta) s FROM fa_xp_ledger").fetchone()["s"]
    earned = db.execute(
        "SELECT SUM(xp_delta) s FROM fa_xp_ledger WHERE is_attendance=0").fetchone()["s"]
    assert total == 1715, "the ledger must reconcile to fa_users.xp exactly"
    assert earned == 250

    by_reason = {r["reason"]: r["n"] for r in db.execute(
        "SELECT reason, COUNT(*) n FROM fa_xp_ledger GROUP BY reason")}
    assert by_reason == {"daily_login": 41, "step_pass": 2, "opening_balance": 1}

    unattributed = db.execute(
        "SELECT xp_delta, verified FROM fa_xp_ledger "
        "WHERE reason='opening_balance'").fetchone()
    assert unattributed["xp_delta"] == 150
    assert unattributed["verified"] == 0, (
        "reconstructed XP must be distinguishable from an observed award"
    )

    before = db.execute("SELECT COUNT(*) c FROM fa_xp_ledger").fetchone()["c"]
    apply()
    assert db.execute("SELECT COUNT(*) c FROM fa_xp_ledger").fetchone()["c"] == before


def test_the_migration_has_no_semicolon_inside_a_string_literal():
    """A splitter that is not string-aware would cut an INSERT in half.

    Both backfill notes originally contained one, and the statement that created the
    table was silently dropped in exactly this way earlier in the same session.
    """
    import pathlib

    path = (pathlib.Path(__file__).resolve().parent.parent
            / "tools" / "db" / "migrations" / "315_fa_xp_ledger.sql")
    body = "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines()
                     if not ln.strip().startswith("--"))
    # Alternation, not a regex: a pattern like '[^']*;[^']*' happily pairs the
    # closing quote of one literal with the opening quote of the next and reports
    # every semicolon between them.
    inside = body.split("'")[1::2]
    offenders = [s for s in inside if ";" in s]
    assert not offenders, offenders
