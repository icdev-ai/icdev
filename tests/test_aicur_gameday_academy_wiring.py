# CUI // SP-CTI
"""aicur-fix-02: Academy GameDay XP and seed bonus have real callers.

``award_gameday_xp`` and ``get_gameday_seed_bonus`` were declared in
``apps/forge_academy/gamification.py`` and called by nothing. Now:

* ending a TTX session (``TTXEngine.end_session``) pays every Academy-linked
  player by their team's final rank, once per session;
* the facilitator's snake draft adds each player's seed bonus to their match
  confidence, and ``confirm_formation`` carries the Academy link onto the team
  member row so the end-of-session award can find it.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import apps.forge_academy.db as fadb
import apps.forge_academy.gamification as gam
from apps.ai_gameday import registration as reg
from tools.ttx import engine as ttx_engine
from tools.ttx import leaderboard
from tools.ttx import team_manager

SCHEMA = """
CREATE TABLE ttx_teams (
    team_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL, team_name TEXT NOT NULL,
    join_code TEXT NOT NULL UNIQUE, total_score INTEGER DEFAULT 0,
    rank_pos INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE ttx_team_members (
    member_id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL, player_name TEXT NOT NULL, role_id TEXT NOT NULL,
    persona_json TEXT DEFAULT '{}', joined_at TEXT
);
CREATE TABLE ttx_registrations (
    registration_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL, player_name TEXT NOT NULL, email TEXT,
    stated_skill TEXT NOT NULL, matched_role_id TEXT NOT NULL,
    matched_role_label TEXT NOT NULL, match_confidence REAL DEFAULT 1.0,
    match_method TEXT DEFAULT 'selected', match_reasoning TEXT,
    academy_username TEXT, registered_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE ttx_formation_plan (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL, registration_id INTEGER NOT NULL,
    team_slot INTEGER NOT NULL, team_name TEXT NOT NULL,
    confirmed INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE fa_xp_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, xp_delta INTEGER, source_type TEXT, source_id INTEGER
);
"""

USERS = {"ada": {"id": 1, "username": "ada"}, "grace": {"id": 2, "username": "grace"}}


class _Conn:
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        return self._raw.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._raw.commit()


@pytest.fixture()
def db(monkeypatch, tmp_path):
    raw = sqlite3.connect(str(tmp_path / "gd.db"))
    raw.row_factory = sqlite3.Row
    raw.executescript(SCHEMA)
    conn = _Conn(raw)
    for mod in (reg, team_manager, leaderboard, fadb):
        monkeypatch.setattr(mod, "get_connection", lambda *a, **kw: conn)
    monkeypatch.setattr(fadb, "get_user_by_username", lambda name, *a, **kw: USERS.get(name))
    yield raw
    raw.close()


@pytest.fixture()
def awards(monkeypatch):
    """Record award_gameday_xp calls and write the ledger row it would write."""
    calls = []

    def fake_award(user_id, tournament_id, final_rank, total_participants, source_id=None):
        calls.append((user_id, tournament_id, final_rank, total_participants, source_id))
        fadb.get_connection().execute(
            "INSERT INTO fa_xp_ledger (user_id, xp_delta, source_type, source_id) "
            "VALUES (%s, 100, 'gameday', %s)", (user_id, source_id),
        )
        return {"xp_awarded": 100, "achievements_unlocked": [], "gameday_rank": final_rank}

    monkeypatch.setattr(gam, "award_gameday_xp", fake_award)
    return calls


def _team(db, name, members):
    """members: [(player_name, academy_username_or_None)]"""
    team = team_manager.create_team(7, name)
    for player, username in members:
        team_manager.add_member(team["team_id"], player, "ir_lead",
                                persona={"academy_username": username})
    return team["team_id"]


# --------------------------------------------------------------------------
# End of session -> Academy XP
# --------------------------------------------------------------------------

def test_end_session_pays_linked_players_by_final_rank(db, awards, monkeypatch):
    first = _team(db, "Red", [("Ada", "ada"), ("Anon", None)])
    second = _team(db, "Blue", [("Grace", "grace")])
    final = [{"rank": 1, "team_id": first}, {"rank": 2, "team_id": second}]
    monkeypatch.setattr(ttx_engine, "compute_leaderboard", lambda sid: final)
    monkeypatch.setattr(ttx_engine, "update_session_state", lambda sid, st: {"state": st})

    ttx_engine.TTXEngine().end_session(7)

    assert sorted(awards) == [(1, "ttx-7", 1, 2, 7), (2, "ttx-7", 2, 2, 7)]


def test_ending_a_session_twice_pays_once(db, awards):
    team = _team(db, "Red", [("Ada", "ada")])
    final = [{"rank": 1, "team_id": team}]
    leaderboard.award_academy_xp(7, final)
    assert leaderboard.award_academy_xp(7, final) == []
    assert len(awards) == 1


def test_unknown_academy_username_is_skipped(db, awards):
    team = _team(db, "Red", [("Ghost", "no-such-user")])
    assert leaderboard.award_academy_xp(7, [{"rank": 1, "team_id": team}]) == []
    assert awards == []


def test_member_column_link_is_honoured():
    """The scripted sims set ttx_team_members.academy_username directly."""
    assert leaderboard._member_academy_username(
        {"academy_username": "ada", "persona_json": "{}"}) == "ada"
    assert leaderboard._member_academy_username(
        {"persona_json": json.dumps({"academy_username": "grace"})}) == "grace"
    assert leaderboard._member_academy_username({"persona_json": "{}"}) is None


# --------------------------------------------------------------------------
# Team seeding -> seed bonus
# --------------------------------------------------------------------------

def _register(name, confidence, username=None):
    return reg.create_registration(1, {
        "player_name": name, "role_id": "ir_lead", "role_label": "IR Lead",
        "stated_skill": f"{name} skills", "match_confidence": confidence,
        "academy_username": username,
    })


def test_seed_bonus_is_looked_up_per_linked_player(db, monkeypatch):
    monkeypatch.setattr(gam, "get_gameday_seed_bonus", lambda uid: 0.25 if uid == 1 else 0.1)
    _register("Ada", 0.5, "ada")
    _register("Anon", 0.5)
    _register("Ghost", 0.5, "no-such-user")
    bonuses = {r["player_name"]: r["academy_seed_bonus"]
               for r in reg.apply_academy_seed_bonus(reg.list_registrations(1))}
    assert bonuses == {"Ada": 0.25, "Anon": 0.0, "Ghost": 0.0}


def test_seed_bonus_lookup_failure_still_drafts(db, monkeypatch):
    def boom(uid):
        raise RuntimeError("academy db down")
    monkeypatch.setattr(gam, "get_gameday_seed_bonus", boom)
    _register("Ada", 0.5, "ada")
    assert reg.apply_academy_seed_bonus(reg.list_registrations(1))[0]["academy_seed_bonus"] == 0.0


def test_seed_bonus_reorders_the_draft(db, monkeypatch):
    """Ada (0.8 + 0.25) outranks Grace (0.9 + 0) and is dealt the first pick."""
    monkeypatch.setattr(gam, "get_gameday_seed_bonus", lambda uid: 0.25 if uid == 1 else 0.0)
    _register("Grace", 0.9)
    _register("Ada", 0.8, "ada")
    roster = reg.apply_academy_seed_bonus(reg.list_registrations(1))
    teams = reg.snake_draft(roster, 2)
    assert teams[0]["members"][0]["player_name"] == "Ada"


def test_confirm_formation_carries_the_academy_link(db):
    _register("Ada", 0.5, "ada")
    reg.save_formation_plan(1, reg.snake_draft(reg.list_registrations(1), 1))
    reg.confirm_formation(1)
    persona = json.loads(db.execute("SELECT persona_json FROM ttx_team_members").fetchone()[0])
    assert persona["academy_username"] == "ada"
