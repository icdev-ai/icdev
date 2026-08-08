# CUI // SP-CTI
"""aca-trn-04 — nobody could run a training programme, and nobody could have two.

Two defects, one card, because the second is the reason the first could not be
built safely.

**Nothing could run a programme.** ``/academy/org-readiness`` reported cohort
counts, skill gaps and a composite score to the admin/pm/isso tier and stopped
there. Every other Academy route was self-service and per-learner: no way to
assign a mission, set a due date, look at what a learner submitted, or record a
verdict on it. The Academy could measure a training programme but not run one.

**The multi-learner paths had never been exercised.** The platform has exactly
one enrolled learner, so every cross-learner query was correct-by-vacuity. The
card asked for these to be confirmed before building a cohort surface on top of
them, and three were wrong:

  * ``fa_guilds`` had no ``tenant_id``. The invite code was the only key
    ``join_guild`` checked and it is global, so a leaked code admitted a learner
    from another tenant into ``get_guild_stats`` — which returns every member's
    display name and XP. ``/api/academy/guild/<id>`` had no authorisation at all
    beyond that, making it an id-enumeration read across tenants.
  * ``_leaderboard_cache_fresh`` ignored ``tenant_id`` while every read and write
    around it was scoped by it. The first tenant to refresh made the cache look
    fresh for all of them, so every other tenant's refresh was skipped forever.
  * ``join_guild`` returned ``None`` with a 200, so an unresolvable invite was
    indistinguishable from a successful join.

The schema comes from ``db._DDL`` rather than a hand-copied literal so these
tests cannot drift from the DDL the application actually ships.
"""
from __future__ import annotations

import importlib

import pytest

from _academy_conn import academy_conn

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


@pytest.fixture()
def fadb(monkeypatch):
    """Real Academy DDL on SQLite, with both modules pointed at it.

    ``instructor`` imports ``get_connection`` into its own namespace, so patching
    only ``db`` would leave it talking to the real database (the leak documented
    in monkeypatch-function-object-leaks-via-lazy-import).
    """
    db = importlib.import_module("apps.forge_academy.db")
    inst = importlib.import_module("apps.forge_academy.instructor")
    conn = academy_conn()
    conn.executescript(db._DDL)
    conn.commit()
    monkeypatch.setattr(db, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(inst, "get_connection", lambda *a, **k: conn)
    try:
        yield inst, db, conn
    finally:
        conn.close()


def _learner(conn, uid, username, tenant, role="devops", xp=0, name=None):
    conn.execute(
        "INSERT INTO fa_users (id,username,display_name,role,xp,tenant_id) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (uid, username, name or username.title(), role, xp, tenant),
    )
    conn.commit()


def _mission(conn, mid, slug, title, role_filter="all", active=1, tier=1):
    conn.execute(
        "INSERT INTO fa_missions (id,slug,title,tier,role_filter,is_active) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (mid, slug, title, tier, role_filter, active),
    )
    conn.commit()


def _progress(conn, uid, mid, status="completed", score=40, xp_earned=40):
    conn.execute(
        "INSERT INTO fa_mission_progress (user_id,mission_id,status,score,xp_earned) "
        "VALUES (%s,%s,%s,%s,%s)",
        (uid, mid, status, score, xp_earned),
    )
    conn.commit()


# ===========================================================================
# The multi-learner paths the card asked to confirm BEFORE building on them
# ===========================================================================

def test_two_tenants_may_hold_the_same_username(fadb):
    """UNIQUE(username, tenant_id) — the constraint the card named.

    If this were UNIQUE(username) alone, the second tenant to enrol an 'admin'
    would be refused an account, which is the failure that never shows up while
    exactly one learner exists.
    """
    _, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _learner(conn, 2, "alice", TENANT_B)
    rows = conn.execute(
        "SELECT tenant_id FROM fa_users WHERE username='alice' ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == [TENANT_A, TENANT_B]


def test_roster_returns_only_this_tenants_learners(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _learner(conn, 2, "bob", TENANT_A)
    _learner(conn, 3, "carol", TENANT_B)
    names = {r["username"] for r in inst.roster(TENANT_A)}
    assert names == {"alice", "bob"}
    assert {r["username"] for r in inst.roster(TENANT_B)} == {"carol"}


def test_roster_includes_learners_who_never_picked_a_role(fadb):
    """'Who has not started' is the first question an instructor asks.

    The leaderboard filters ``role != 'unset'``, which is precisely why these
    learners were invisible everywhere. A roster that hides them cannot answer it.
    """
    inst, _, conn = fadb
    _learner(conn, 1, "started", TENANT_A, role="devops")
    _learner(conn, 2, "never", TENANT_A, role="unset")
    assert {r["username"] for r in inst.roster(TENANT_A)} == {"started", "never"}


def test_untenanted_install_is_one_population_not_two(fadb):
    """NULL and '' both mean 'no tenant' and must not split one cohort in half.

    The SaaS middleware returns None while refresh_leaderboard_cache has written
    ''. Comparing them raw would make half a real cohort invisible.
    """
    inst, _, conn = fadb
    _learner(conn, 1, "nulled", None)
    _learner(conn, 2, "empty", "")
    assert {r["username"] for r in inst.roster(None)} == {"nulled", "empty"}


def test_get_learner_refuses_across_tenants(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    assert inst.get_learner(1, TENANT_A) is not None
    assert inst.get_learner(1, TENANT_B) is None


def test_guild_stats_refuses_another_tenants_guild(fadb):
    """The id-enumeration read. Same answer as a missing guild, by design."""
    _, db, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    guild = db.create_guild("A Team", "", created_by=1, invite_code="CODEA",
                            tenant_id=TENANT_A)
    assert db.get_guild_stats(guild["id"], tenant_id=TENANT_A) is not None
    assert db.get_guild_stats(guild["id"], tenant_id=TENANT_B) is None
    assert db.get_guild_stats(9999, tenant_id=TENANT_A) is None


def test_guild_members_are_filtered_to_the_tenant(fadb):
    """Rows joined before the fix carry no tenant and must not leak through it."""
    _, db, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _learner(conn, 2, "mallory", TENANT_B)
    guild = db.create_guild("A Team", "", created_by=1, invite_code="CODEA",
                            tenant_id=TENANT_A)
    conn.execute("INSERT INTO fa_guild_members (guild_id,user_id) VALUES (%s,%s)",
                 (guild["id"], 2))
    conn.commit()
    stats = db.get_guild_stats(guild["id"], tenant_id=TENANT_A)
    assert [m["display_name"] for m in stats["members"]] == ["Alice"]


def test_join_guild_refuses_a_leaked_cross_tenant_invite_code(fadb):
    _, db, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _learner(conn, 2, "mallory", TENANT_B)
    db.create_guild("A Team", "", created_by=1, invite_code="CODEA",
                    tenant_id=TENANT_A)
    assert db.join_guild("CODEA", 2, tenant_id=TENANT_B) is None
    assert db.join_guild("CODEA", 1, tenant_id=TENANT_A) is not None


def test_leaderboard_cache_freshness_is_per_tenant(fadb):
    """One tenant's refresh must not mark every other tenant's cache fresh.

    With the tenant clause missing, tenant B's refresh is skipped forever: its
    rows are never written, the cache query returns nothing, and it silently
    falls back to the uncached path (which has no rank_pos).
    """
    _, db, conn = fadb
    from datetime import datetime, timezone
    _learner(conn, 1, "alice", TENANT_A)
    conn.execute(
        "INSERT INTO fa_leaderboard_cache (user_id,period,score,computed_at,tenant_id) "
        "VALUES (%s,'weekly',10,%s,%s)",
        (1, datetime.now(timezone.utc).isoformat(), TENANT_A),
    )
    conn.commit()
    assert db._leaderboard_cache_fresh(conn, "weekly", TENANT_A) is True
    assert db._leaderboard_cache_fresh(conn, "weekly", TENANT_B) is False


# ===========================================================================
# Assignment
# ===========================================================================

def test_assign_a_mission_to_one_learner(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Harden a Container")
    a = inst.create_assignment(assigned_by="pm@x", mission_id=10,
                               target_type="learner", target_user_id=1,
                               due_at="2026-09-01", tenant_id=TENANT_A)
    assert a["status"] == "open"
    listed = inst.list_assignments(TENANT_A)
    assert len(listed) == 1
    assert listed[0]["label"] == "Harden a Container"
    assert [t["user_id"] for t in listed[0]["targets"]] == [1]


def test_cohort_membership_is_resolved_at_read_time(fadb):
    """A learner who enrols into the role AFTER the assignment inherits it.

    That is what "assign the SecOps track to SecOps" means to the person typing
    it. Freezing the member list at assign time would silently exclude everyone
    who joined the team later — the common case in a training programme.
    """
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A, role="secops_eng")
    _mission(conn, 10, "m1", "Threat Model", role_filter="secops_eng")
    inst.create_assignment(assigned_by="pm@x", mission_id=10,
                           target_type="cohort", target_role="secops_eng",
                           tenant_id=TENANT_A)
    assert inst.list_assignments(TENANT_A)[0]["target_count"] == 1

    _learner(conn, 2, "bob", TENANT_A, role="secops_eng")
    listed = inst.list_assignments(TENANT_A)[0]
    assert listed["target_count"] == 2
    assert {t["user_id"] for t in listed["targets"]} == {1, 2}


def test_a_cohort_assignment_never_reaches_another_tenant(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A, role="devops")
    _learner(conn, 2, "carol", TENANT_B, role="devops")
    _mission(conn, 10, "m1", "Pipeline Basics")
    inst.create_assignment(assigned_by="pm@x", mission_id=10,
                           target_type="cohort", target_role="devops",
                           tenant_id=TENANT_A)
    assert [t["user_id"] for t in inst.list_assignments(TENANT_A)[0]["targets"]] == [1]
    assert inst.list_assignments(TENANT_B) == []


def test_a_track_covers_whole_role_tokens_not_substrings(fadb):
    """aca-hyg-02 again: 'swe' LIKE-matches 'swe_arch'. Assignments must not.

    A track that silently includes architect-only missions makes a plain SWE's
    assignment permanently incompletable, and it reads as a learner failure.
    """
    inst, _, conn = fadb
    _mission(conn, 10, "m1", "SWE Only", role_filter="swe")
    _mission(conn, 11, "m2", "Arch Only", role_filter="swe_arch")
    _mission(conn, 12, "m3", "Everyone", role_filter="all")
    titles = {m["title"] for m in inst.track_missions("swe_arch", conn)}
    assert titles == {"Arch Only", "Everyone"}


def test_a_track_ignores_inactive_missions(fadb):
    inst, _, conn = fadb
    _mission(conn, 10, "m1", "Live", role_filter="devops")
    _mission(conn, 11, "m2", "Retired", role_filter="devops", active=0)
    assert [m["title"] for m in inst.track_missions("devops", conn)] == ["Live"]


def test_the_all_cohort_targets_every_learner_in_the_tenant(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A, role="devops")
    _learner(conn, 2, "bob", TENANT_A, role="isso")
    _learner(conn, 3, "carol", TENANT_B, role="devops")
    _mission(conn, 10, "m1", "Everyone")
    inst.create_assignment(assigned_by="pm@x", assignment_type="track",
                           track_key="all", target_type="cohort",
                           target_role="all", tenant_id=TENANT_A)
    assert {t["user_id"] for t in inst.list_assignments(TENANT_A)[0]["targets"]} == {1, 2}


@pytest.mark.parametrize("kwargs, fragment", [
    ({"assignment_type": "sudden"}, "assignment type"),
    ({"target_type": "everyone"}, "target type"),
    ({"mission_id": None}, "mission_id is required"),
    ({"mission_id": 999}, "does not exist"),
    ({"assignment_type": "track", "track_key": "wizard"}, "unknown track"),
    ({"target_user_id": None}, "target_user_id is required"),
    ({"target_type": "cohort", "target_role": "wizard"}, "unknown cohort role"),
])
def test_an_invalid_assignment_is_refused_and_writes_nothing(fadb, kwargs, fragment):
    """A rejected request must leave no audit row claiming an assignment exists."""
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    args = {"assigned_by": "pm@x", "mission_id": 10, "target_type": "learner",
            "target_user_id": 1, "tenant_id": TENANT_A}
    args.update(kwargs)
    with pytest.raises(inst.AssignmentError) as exc:
        inst.create_assignment(**args)
    assert fragment in str(exc.value)
    assert conn.execute("SELECT COUNT(*) FROM fa_assignments").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fa_instructor_audit").fetchone()[0] == 0


def test_an_inactive_mission_cannot_be_assigned(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Retired", active=0)
    with pytest.raises(inst.AssignmentError, match="inactive"):
        inst.create_assignment(assigned_by="pm@x", mission_id=10,
                               target_user_id=1, tenant_id=TENANT_A)


def test_a_track_with_no_active_missions_is_refused(fadb):
    """An assignment nobody can complete shows as permanently 0% — a learner smear."""
    inst, _, conn = fadb
    _mission(conn, 10, "m1", "Dev Only", role_filter="devops")
    with pytest.raises(inst.AssignmentError, match="no active missions"):
        inst.create_assignment(assigned_by="pm@x", assignment_type="track",
                               track_key="isso", target_type="cohort",
                               target_role="isso", tenant_id=TENANT_A)


def test_a_learner_in_another_tenant_cannot_be_assigned_to(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    with pytest.raises(inst.AssignmentError, match="not in this tenant"):
        inst.create_assignment(assigned_by="pm@x", mission_id=10,
                               target_user_id=1, tenant_id=TENANT_B)


def test_an_unparseable_due_date_is_refused_rather_than_dropped(fadb):
    """Storing NULL would mean a deadline that never goes overdue and nobody finds out."""
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    with pytest.raises(inst.AssignmentError, match="not a date"):
        inst.create_assignment(assigned_by="pm@x", mission_id=10, target_user_id=1,
                               due_at="next tuesday", tenant_id=TENANT_A)


def test_an_overdue_assignment_is_flagged_until_it_is_complete(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Late Mission")
    inst.create_assignment(assigned_by="pm@x", mission_id=10, target_user_id=1,
                           due_at="2020-01-01", tenant_id=TENANT_A)
    assert inst.list_assignments(TENANT_A)[0]["overdue"] is True

    _progress(conn, 1, 10, status="completed")
    listed = inst.list_assignments(TENANT_A)[0]
    assert listed["overdue"] is False
    assert listed["percent"] == 100


def test_cancelling_an_assignment_clears_it_from_the_open_list(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    a = inst.create_assignment(assigned_by="pm@x", mission_id=10, target_user_id=1,
                               tenant_id=TENANT_A)
    assert inst.cancel_assignment(a["id"], actor="pm@x", tenant_id=TENANT_A) is True
    assert inst.list_assignments(TENANT_A, include_cancelled=False) == []
    assert inst.list_assignments(TENANT_A)[0]["status"] == "cancelled"


def test_an_assignment_cannot_be_cancelled_from_another_tenant(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    a = inst.create_assignment(assigned_by="pm@x", mission_id=10, target_user_id=1,
                               tenant_id=TENANT_A)
    assert inst.cancel_assignment(a["id"], actor="mallory", tenant_id=TENANT_B) is False
    assert inst.list_assignments(TENANT_A)[0]["status"] == "open"


def test_filtering_by_learner_includes_their_cohort_assignments(fadb):
    """A plain ``target_user_id=?`` query would miss every cohort assignment."""
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A, role="devops")
    _mission(conn, 10, "m1", "Cohort Work", role_filter="devops")
    inst.create_assignment(assigned_by="pm@x", mission_id=10, target_type="cohort",
                           target_role="devops", tenant_id=TENANT_A)
    assert len(inst.list_assignments(TENANT_A, user_id=1)) == 1
    assert inst.list_assignments(TENANT_A, user_id=2) == []


def test_the_roster_counts_open_and_overdue_work_per_learner(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A, role="devops")
    _mission(conn, 10, "m1", "Late One")
    _mission(conn, 11, "m2", "Not Due Yet")
    inst.create_assignment(assigned_by="pm@x", mission_id=10, target_user_id=1,
                           due_at="2020-01-01", tenant_id=TENANT_A)
    inst.create_assignment(assigned_by="pm@x", mission_id=11, target_user_id=1,
                           due_at="2099-01-01", tenant_id=TENANT_A)
    row = inst.roster(TENANT_A)[0]
    assert row["open_assignments"] == 2
    assert row["overdue_assignments"] == 1


# ===========================================================================
# Review and override
# ===========================================================================

def test_a_review_records_a_verdict(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    _progress(conn, 1, 10)
    inst.record_review(user_id=1, verdict="approved", reviewer="pm@x",
                       mission_id=10, comment="good work", tenant_id=TENANT_A)
    reviews = inst.learner_reviews(1)
    assert len(reviews) == 1
    assert reviews[0]["verdict"] == "approved"
    assert reviews[0]["reviewer"] == "pm@x"


def test_an_override_moves_the_score_and_records_what_it_replaced(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    _progress(conn, 1, 10, score=40, xp_earned=40)
    inst.record_review(user_id=1, verdict="approved", reviewer="pm@x",
                       mission_id=10, override_score=90, tenant_id=TENANT_A)
    score = conn.execute(
        "SELECT score FROM fa_mission_progress WHERE user_id=1 AND mission_id=10"
    ).fetchone()[0]
    assert score == 90
    assert inst.learner_reviews(1)[0]["prior_score"] == 40


def test_an_override_never_mints_xp(fadb):
    """The invariant aca-int-07 bought and this card must not sell back.

    Every XP point in this schema has a provenance row in fa_xp_ledger. An
    instructor-mintable XP path is a rank bought rather than demonstrated — so an
    override records a judgement about a SCORE and the ledger stays a record of
    what was actually graded.
    """
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A, xp=100)
    _mission(conn, 10, "m1", "Real Mission")
    _progress(conn, 1, 10, score=40, xp_earned=40)
    inst.record_review(user_id=1, verdict="approved", reviewer="pm@x",
                       mission_id=10, override_score=100, tenant_id=TENANT_A)
    assert conn.execute("SELECT xp FROM fa_users WHERE id=1").fetchone()[0] == 100
    assert conn.execute(
        "SELECT xp_earned FROM fa_mission_progress WHERE user_id=1"
    ).fetchone()[0] == 40
    assert conn.execute("SELECT COUNT(*) FROM fa_xp_ledger").fetchone()[0] == 0


def test_a_mission_the_learner_never_opened_cannot_be_overridden(fadb):
    """Otherwise a progress row is fabricated out of an instructor's opinion."""
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Untouched Mission")
    with pytest.raises(inst.AssignmentError, match="no progress"):
        inst.record_review(user_id=1, verdict="approved", reviewer="pm@x",
                           mission_id=10, override_score=90, tenant_id=TENANT_A)
    assert conn.execute("SELECT COUNT(*) FROM fa_mission_progress").fetchone()[0] == 0


@pytest.mark.parametrize("kwargs, fragment", [
    ({"verdict": "vibes"}, "unknown verdict"),
    ({"override_score": 101}, "between 0 and 100"),
    ({"override_score": -1}, "between 0 and 100"),
    ({"override_score": "great"}, "whole number"),
    ({"override_score": 90, "mission_id": None}, "needs the mission"),
])
def test_an_invalid_review_is_refused_and_writes_nothing(fadb, kwargs, fragment):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    _progress(conn, 1, 10, score=40)
    args = {"user_id": 1, "verdict": "approved", "reviewer": "pm@x",
            "mission_id": 10, "tenant_id": TENANT_A}
    args.update(kwargs)
    with pytest.raises(inst.AssignmentError) as exc:
        inst.record_review(**args)
    assert fragment in str(exc.value)
    assert conn.execute("SELECT COUNT(*) FROM fa_instructor_reviews").fetchone()[0] == 0
    assert conn.execute(
        "SELECT score FROM fa_mission_progress WHERE user_id=1"
    ).fetchone()[0] == 40


def test_a_learner_in_another_tenant_cannot_be_reviewed(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    with pytest.raises(inst.AssignmentError, match="not in this tenant"):
        inst.record_review(user_id=1, verdict="approved", reviewer="mallory",
                           tenant_id=TENANT_B)


# ===========================================================================
# Audit
# ===========================================================================

def test_every_instructor_action_is_attributable(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    _progress(conn, 1, 10, score=40)
    a = inst.create_assignment(assigned_by="pm@x", actor_role="pm", mission_id=10,
                               target_user_id=1, tenant_id=TENANT_A)
    inst.record_review(user_id=1, verdict="approved", reviewer="isso@x",
                       actor_role="isso", mission_id=10, override_score=88,
                       tenant_id=TENANT_A)
    inst.cancel_assignment(a["id"], actor="pm@x", actor_role="pm", tenant_id=TENANT_A)

    trail = inst.audit_trail(TENANT_A)
    assert [r["action"] for r in trail] == [
        "assignment.cancel", "review.record", "assignment.create"]
    assert {r["actor"] for r in trail} == {"pm@x", "isso@x"}
    review_row = next(r for r in trail if r["action"] == "review.record")
    assert '"override_score": 88' in review_row["detail_json"]
    assert '"prior_score": 40' in review_row["detail_json"]


def test_the_audit_trail_does_not_cross_tenants(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    inst.create_assignment(assigned_by="pm@x", mission_id=10, target_user_id=1,
                           tenant_id=TENANT_A)
    assert len(inst.audit_trail(TENANT_A)) == 1
    assert inst.audit_trail(TENANT_B) == []


def test_a_failed_audit_write_does_not_undo_the_grade(fadb, caplog):
    """A broken audit trail must be loud, not a rolled-back grade the user saw succeed."""
    inst, _, conn = fadb
    conn.execute("DROP TABLE fa_instructor_audit")
    conn.commit()
    inst.log_audit("review.record", "pm@x", tenant_id=TENANT_A)  # must not raise
    assert "audit write failed" in caplog.text


def test_the_audit_table_is_registered_append_only():
    """The hook is what makes 'append-only' true; the DDL only says it."""
    import pathlib
    hook = pathlib.Path(__file__).resolve().parents[1] / "tools/hooks/shared_checks.py"
    assert "fa_instructor_audit" in hook.read_text(encoding="utf-8")


# ===========================================================================
# Submissions and evidence
# ===========================================================================

def test_submissions_separate_graded_work_from_pages_turned(fadb):
    """A completed 'read' step is a page turned, not a demonstration.

    Counting it as evidence is how a learner reaches 100% having proved nothing —
    the same conflation aca-int-07 found between logins and earned XP.
    """
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    conn.execute(
        "INSERT INTO fa_mission_steps (id,mission_id,step_num,title,step_type) "
        "VALUES (1,10,1,'Read The Docs','read'),(2,10,2,'Write The Code','coding')"
    )
    conn.execute(
        "INSERT INTO fa_step_progress (user_id,step_id,status,submission) "
        "VALUES (1,1,'completed',''),(1,2,'completed','def solve(): return 42')"
    )
    conn.commit()
    by_title = {s["step_title"]: s for s in inst.learner_submissions(1)}
    assert by_title["Read The Docs"]["assessed"] is False
    assert by_title["Read The Docs"]["has_submission"] is False
    assert by_title["Write The Code"]["assessed"] is True
    assert by_title["Write The Code"]["has_submission"] is True


def test_evidence_reads_the_ledger_rather_than_recomputing_it(fadb):
    """Only the ledger can say WHY a learner has the points they have."""
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A, xp=150)
    conn.execute(
        "INSERT INTO fa_xp_ledger (user_id,xp_delta,reason,is_attendance) "
        "VALUES (1,100,'daily_login',1),(1,50,'step_pass',0)"
    )
    conn.commit()
    evidence = inst.learner_evidence(1)
    attendance = [e for e in evidence if e.get("is_attendance")]
    earned = [e for e in evidence if e.get("kind") == "xp" and not e.get("is_attendance")]
    assert sum(e["xp_delta"] for e in attendance) == 100
    assert sum(e["xp_delta"] for e in earned) == 50


def test_the_roster_counts_a_learners_submissions_and_reviews(fadb):
    inst, _, conn = fadb
    _learner(conn, 1, "alice", TENANT_A)
    _mission(conn, 10, "m1", "Real Mission")
    _progress(conn, 1, 10, status="completed")
    conn.execute(
        "INSERT INTO fa_mission_steps (id,mission_id,step_num,title,step_type) "
        "VALUES (1,10,1,'Write The Code','coding')"
    )
    conn.execute(
        "INSERT INTO fa_step_progress (user_id,step_id,status,submission) "
        "VALUES (1,1,'completed','answer')"
    )
    conn.commit()
    inst.record_review(user_id=1, verdict="approved", reviewer="pm@x",
                       mission_id=10, tenant_id=TENANT_A)
    row = inst.roster(TENANT_A)[0]
    assert row["missions_completed"] == 1
    assert row["steps_completed"] == 1
    assert row["submissions"] == 1
    assert row["reviews"] == 1
