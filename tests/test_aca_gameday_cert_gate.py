# CUI // SP-CTI
"""aicur-fix-01 — the certificate GameDay gates read GameDay's real tables.

The Practitioner gate counted ``ttx_receipts``, a table no migration or DDL has
ever created. The query raised, ``except Exception: gd = 0`` swallowed it, and
"GameDay >= 1 scenarios" could never be met by anyone. The Expert tier's
``gameday_top_percentile`` requirement was not read at all — it fell off the end
of the gate chain, so the certificate attested to a placing nobody checked.

Participation is now derived from the tables GameDay actually writes:
``ttx_registrations.academy_username`` (the Academy link captured at sign-up)
-> ``ttx_formation_plan`` -> the ``ttx_teams`` row ``confirm_formation`` created
for that slot -> ``ttx_scores``. A scenario counts once the player's team has a
scored response in it.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from apps.ai_gameday import db as gd_db
from apps.ai_gameday import registration as gd_reg
from apps.forge_academy import db


@pytest.fixture(scope="module")
def _migrated():
    db.migrate()
    gd_db._migrated = False
    gd_db.migrate()


_RUN = uuid.uuid4().hex[:8]  # the test DB outlives a run; keep players distinct


def _u(name: str) -> str:
    return f"{name}_{_RUN}"


def _user(username: str) -> dict:
    return db.get_or_create_user(username, display_name=username, tenant_id=None)


def _session(slug: str) -> int:
    from tools.ttx.session_manager import create_session

    return int(create_session(slug, "live", "facilitator")["session_id"])


def _register(session_id: int, player: str, academy_username: str | None) -> None:
    gd_reg.create_registration(session_id, {
        "player_name": player,
        "role_id": "analyst",
        "role_label": "Analyst",
        "academy_username": academy_username,
    })


def _confirm(session_id: int, max_teams: int) -> dict:
    """Draft + confirm; returns {player_name: team_id}."""
    roster = gd_reg.list_registrations(session_id)
    gd_reg.save_formation_plan(session_id, gd_reg.snake_draft(roster, max_teams))
    gd_reg.confirm_formation(session_id)
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT t.team_id, m.player_name FROM ttx_teams t "
        "JOIN ttx_team_members m ON m.team_id = t.team_id WHERE t.session_id = %s",
        (session_id,),
    ).fetchall()
    return {r["player_name"]: int(r["team_id"]) for r in rows}


def _score(session_id: int, team_id: int, pts: int) -> None:
    conn = db.get_connection()
    inject_id = f"inj-{session_id}-{team_id}"
    conn.execute(
        "INSERT INTO ttx_injects (inject_id, session_id, slug, title) "
        "VALUES (%s, %s, %s, %s)",
        (inject_id, session_id, "inj", "Inject"),
    )
    conn.execute(
        "INSERT INTO ttx_responses (team_id, inject_id, response_text) "
        "VALUES (%s, %s, %s)",
        (team_id, inject_id, "answer"),
    )
    resp_id = conn.execute(
        "SELECT MAX(response_id) FROM ttx_responses WHERE team_id = %s", (team_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ttx_scores (response_id, team_id, inject_id, total_pts) "
        "VALUES (%s, %s, %s, %s)",
        (resp_id, team_id, inject_id, pts),
    )
    conn.commit()


def _gate(result: dict, prefix: str) -> dict:
    hits = [g for g in result["gates"] if g["name"].startswith(prefix)]
    assert len(hits) == 1, f"expected one gate starting {prefix!r}: {result['gates']}"
    return hits[0]


# ---------------------------------------------------------------------------
# Practitioner: GameDay scenarios completed
# ---------------------------------------------------------------------------

def test_scored_participation_meets_the_practitioner_gameday_gate(_migrated):
    user = _user(_u("gd_scored_player"))
    sid = _session("gd_cert_scored")
    _register(sid, "Scored Player", _u("gd_scored_player"))
    teams = _confirm(sid, 1)
    _score(sid, teams["Scored Player"], 40)

    gate = _gate(db.check_cert_eligibility(user["id"], "practitioner"), "GameDay >=")
    assert gate["met"] is True, gate
    assert gate["detail"] == "GameDay scenarios completed: 1"


def test_registration_without_a_scored_response_does_not_count(_migrated):
    user = _user(_u("gd_unscored_player"))
    sid = _session("gd_cert_unscored")
    _register(sid, "Unscored Player", _u("gd_unscored_player"))
    _confirm(sid, 1)

    gate = _gate(db.check_cert_eligibility(user["id"], "practitioner"), "GameDay >=")
    assert gate["met"] is False
    assert gate["detail"] == "GameDay scenarios completed: 0"


def test_another_players_participation_is_not_credited(_migrated):
    user = _user(_u("gd_bystander"))
    sid = _session("gd_cert_other")
    _register(sid, "Someone Else", _u("gd_someone_else"))
    teams = _confirm(sid, 1)
    _score(sid, teams["Someone Else"], 40)

    assert db.gameday_participation(db.get_connection(), _u("gd_bystander"))["scenarios"] == 0
    gate = _gate(db.check_cert_eligibility(user["id"], "practitioner"), "GameDay >=")
    assert gate["met"] is False


# ---------------------------------------------------------------------------
# Expert: top-N% placing in a GameDay
# ---------------------------------------------------------------------------

def test_expert_tier_enforces_the_top_percentile_requirement(_migrated):
    winner = _user(_u("gd_winner"))
    loser = _user(_u("gd_loser"))
    sid = _session("gd_cert_placing")
    _register(sid, "Winner", _u("gd_winner"))
    _register(sid, "Loser", _u("gd_loser"))
    teams = _confirm(sid, 2)
    assert teams["Winner"] != teams["Loser"], "two teams expected"
    _score(sid, teams["Winner"], 90)
    _score(sid, teams["Loser"], 10)

    win_gate = _gate(db.check_cert_eligibility(winner["id"], "expert"), "GameDay top")
    lose_gate = _gate(db.check_cert_eligibility(loser["id"], "expert"), "GameDay top")
    assert win_gate["met"] is True, win_gate
    assert lose_gate["met"] is False, lose_gate


# ---------------------------------------------------------------------------
# A failure to measure is SAID, not read as zero
# ---------------------------------------------------------------------------

def test_missing_gameday_tables_are_reported_not_swallowed(_migrated, monkeypatch):
    user = _user(_u("gd_no_tables"))
    monkeypatch.setattr(db, "_gameday_tables_present", lambda conn: False)
    gate = _gate(db.check_cert_eligibility(user["id"], "practitioner"), "GameDay >=")
    assert gate["met"] is False
    assert "not installed" in gate["detail"].lower()


def test_query_error_is_logged_and_surfaced(_migrated, monkeypatch, caplog):
    user = _user(_u("gd_query_error"))

    def _boom(conn, username):
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "_gameday_participation_rows", _boom)
    with caplog.at_level(logging.WARNING, logger=db.__name__):
        gate = _gate(db.check_cert_eligibility(user["id"], "practitioner"), "GameDay >=")
    assert gate["met"] is False
    assert "could not be measured" in gate["detail"].lower()
    assert any("boom" in r.getMessage() for r in caplog.records)
