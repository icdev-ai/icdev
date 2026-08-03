# CUI // SP-CTI
"""Pre-session registration + snake-draft team formation (gdx-reg-01).

The templates, the tables and the feature doc for this all shipped; the routes
never did. ``apps/ai_gameday/registration.py`` was deleted by ``penta-gd-03`` as
unreachable dead code, and the doc described the endpoints as if they existed.
These tests cover the wiring that closes that gap.

Emphasis is on the two places this can silently do the wrong thing:

* **Draft balance.** A round-robin deal stacks the strongest matches on team 1
  and puts five identically-matched players on the same team. The snake order
  and the role-block interleave are the corrections, and both are asserted.
* **Confirming.** Materialising the draft writes real ``ttx_teams`` rows that
  the rest of GameDay consumes. Confirming twice must replace, not double.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.ai_gameday import registration as reg  # noqa: E402

SCHEMA = """
CREATE TABLE ttx_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_slug TEXT, join_code TEXT, max_teams INTEGER DEFAULT 8,
    config_json TEXT DEFAULT '{}'
);
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
"""

ROLES = [
    {"id": "ir_lead", "label": "Incident Response Lead",
     "description": "Coordinates containment and eradication during an incident.",
     "icon": "🚨", "color": "#f85149"},
    {"id": "malware_analyst", "label": "Malware Analyst",
     "description": "Reverse engineers samples and extracts indicators.",
     "icon": "🧬", "color": "#a371f7"},
    {"id": "comms", "label": "Communications Officer",
     "description": "Handles press statements and stakeholder briefings.",
     "icon": "📣", "color": "#58a6ff"},
]


class _Conn:
    """sqlite3 wrapper matching how the module uses get_connection()."""

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
    raw.execute(
        "INSERT INTO ttx_sessions (session_id, scenario_slug, join_code) VALUES (1,'ai_gameday','ABC123')"
    )
    raw.commit()
    conn = _Conn(raw)
    monkeypatch.setattr(reg, "get_connection", lambda *a, **kw: conn)
    from tools.ttx import team_manager
    monkeypatch.setattr(team_manager, "get_connection", lambda *a, **kw: conn)
    return raw


def _add(db, name, role_id="ir_lead", confidence=1.0, label=None):
    return reg.create_registration(1, {
        "player_name": name, "role_id": role_id,
        "role_label": label or role_id.replace("_", " ").title(),
        "stated_skill": f"{name} skills", "match_confidence": confidence,
    })


# --------------------------------------------------------------------------
# Skill matching
# --------------------------------------------------------------------------

def test_matches_the_role_whose_words_overlap():
    m = reg.match_skill_to_role("I reverse engineer malware samples and pull indicators", ROLES)
    assert m["role_id"] == "malware_analyst"
    assert m["confidence"] > 0
    assert "Matched on" in m["reasoning"]


def test_matches_a_different_role_for_different_words():
    m = reg.match_skill_to_role("I write press statements and brief stakeholders", ROLES)
    assert m["role_id"] == "comms"


def test_no_overlap_reports_zero_confidence_not_a_confident_guess():
    """The form shows this number to the player — it must not invent certainty."""
    m = reg.match_skill_to_role("zzzz qqqq", ROLES)
    assert m["confidence"] == 0.0
    assert "No overlap" in m["reasoning"]


def test_stopwords_do_not_create_a_match():
    m = reg.match_skill_to_role("I have a lot of experience working with the", ROLES)
    assert m["confidence"] == 0.0


def test_no_roles_returns_none():
    assert reg.match_skill_to_role("anything", []) is None


def test_confidence_is_bounded():
    m = reg.match_skill_to_role("malware reverse engineers samples indicators extracts", ROLES)
    assert 0.0 <= m["confidence"] <= 1.0


# --------------------------------------------------------------------------
# Roster
# --------------------------------------------------------------------------

def test_registration_round_trips(db):
    _add(db, "Ada")
    rows = reg.list_registrations(1)
    assert len(rows) == 1
    assert rows[0]["player_name"] == "Ada"
    assert rows[0]["matched_role_id"] == "ir_lead"


def test_player_name_is_required(db):
    with pytest.raises(ValueError, match="player_name"):
        reg.create_registration(1, {"role_id": "ir_lead"})


def test_role_is_required(db):
    with pytest.raises(ValueError, match="role_id"):
        reg.create_registration(1, {"player_name": "Ada"})


def test_out_of_range_confidence_is_clamped(db):
    reg.create_registration(1, {
        "player_name": "Ada", "role_id": "ir_lead", "match_confidence": 9.5,
    })
    assert reg.list_registrations(1)[0]["match_confidence"] == 1.0


def test_non_numeric_confidence_does_not_raise(db):
    reg.create_registration(1, {
        "player_name": "Ada", "role_id": "ir_lead", "match_confidence": "banana",
    })
    assert reg.list_registrations(1)[0]["match_confidence"] == 1.0


def test_deleting_a_registration_also_clears_its_draft_slot(db):
    r = _add(db, "Ada")
    _add(db, "Grace")
    reg.save_formation_plan(1, reg.snake_draft(reg.list_registrations(1), 2))
    assert reg.delete_registration(r["registration_id"]) is True
    remaining = [
        m["registration_id"]
        for team in reg.get_formation_plan(1) for m in team["members"]
    ]
    assert r["registration_id"] not in remaining


def test_deleting_an_unknown_registration_is_false(db):
    assert reg.delete_registration(9999) is False


# --------------------------------------------------------------------------
# Snake draft
# --------------------------------------------------------------------------

def test_draft_is_serpentine_not_round_robin(db):
    """Round-robin would put picks 1 and 4 on the same team; snake must not."""
    for i in range(4):
        _add(db, f"P{i}", confidence=1.0 - i * 0.1)
    teams = reg.snake_draft(reg.list_registrations(1), 2)
    names = [[m["player_name"] for m in t["members"]] for t in teams]
    # order: P0->t0, P1->t1, then reversed: P2->t1, P3->t0
    assert names[0] == ["P0", "P3"]
    assert names[1] == ["P1", "P2"]


def test_duplicate_roles_are_spread_across_teams(db):
    """Five identical roles must not all land on team 1."""
    for i in range(5):
        _add(db, f"Analyst{i}", role_id="malware_analyst")
    teams = reg.snake_draft(reg.list_registrations(1), 5)
    assert all(len(t["members"]) == 1 for t in teams)


def test_teams_are_balanced_in_size(db):
    for i in range(7):
        _add(db, f"P{i}")
    sizes = sorted(len(t["members"]) for t in reg.snake_draft(reg.list_registrations(1), 3))
    assert sizes == [2, 2, 3]


def test_more_teams_than_players_does_not_make_empty_teams(db):
    _add(db, "Solo")
    teams = reg.snake_draft(reg.list_registrations(1), 8)
    assert len(teams) == 1


def test_empty_roster_drafts_nothing(db):
    assert reg.snake_draft([], 4) == []


def test_zero_max_teams_still_produces_one_team(db):
    _add(db, "Ada")
    assert len(reg.snake_draft(reg.list_registrations(1), 0)) == 1


def test_draft_shape_matches_what_the_template_renders(db):
    _add(db, "Ada")
    team = reg.snake_draft(reg.list_registrations(1), 1)[0]
    assert set(team) == {"team_slot", "team_name", "members"}
    assert set(team["members"][0]) >= {
        "registration_id", "player_name", "role_id", "role_label"
    }


# --------------------------------------------------------------------------
# Plan persistence and manual moves
# --------------------------------------------------------------------------

def test_plan_round_trips(db):
    for i in range(4):
        _add(db, f"P{i}")
    reg.save_formation_plan(1, reg.snake_draft(reg.list_registrations(1), 2))
    plan = reg.get_formation_plan(1)
    assert len(plan) == 2
    assert sum(len(t["members"]) for t in plan) == 4


def test_saving_a_plan_replaces_the_previous_one(db):
    for i in range(4):
        _add(db, f"P{i}")
    roster = reg.list_registrations(1)
    reg.save_formation_plan(1, reg.snake_draft(roster, 2))
    reg.save_formation_plan(1, reg.snake_draft(roster, 4))
    assert sum(len(t["members"]) for t in reg.get_formation_plan(1)) == 4


def test_no_plan_yet_is_an_empty_list_not_an_error(db):
    assert reg.get_formation_plan(1) == []


def test_moving_a_player_changes_their_team(db):
    for i in range(4):
        _add(db, f"P{i}")
    reg.save_formation_plan(1, reg.snake_draft(reg.list_registrations(1), 2))
    target = reg.get_formation_plan(1)[0]["members"][0]["registration_id"]
    plan = reg.move_player(1, target, 1, "Team 2")
    slot_of = {
        m["registration_id"]: t["team_slot"] for t in plan for m in t["members"]
    }
    assert slot_of[target] == 1


def test_moving_a_player_not_in_the_plan_raises(db):
    """Silently creating a one-team plan here would lose everyone else."""
    _add(db, "Ada")
    with pytest.raises(ValueError, match="not in the current formation plan"):
        reg.move_player(1, 999, 0, "Team 1")


# --------------------------------------------------------------------------
# Confirming
# --------------------------------------------------------------------------

def test_confirming_creates_real_teams_and_members(db):
    for i in range(4):
        _add(db, f"P{i}")
    reg.save_formation_plan(1, reg.snake_draft(reg.list_registrations(1), 2))
    counts = reg.confirm_formation(1)
    assert counts == {"teams_created": 2, "members_created": 4}
    assert db.execute("SELECT COUNT(*) FROM ttx_teams").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM ttx_team_members").fetchone()[0] == 4


def test_confirmed_teams_get_join_codes(db):
    """They must be indistinguishable from teams created any other way."""
    _add(db, "Ada")
    reg.save_formation_plan(1, reg.snake_draft(reg.list_registrations(1), 1))
    reg.confirm_formation(1)
    code = db.execute("SELECT join_code FROM ttx_teams").fetchone()[0]
    assert code and len(code) >= 4


def test_confirming_twice_replaces_rather_than_doubles(db):
    for i in range(4):
        _add(db, f"P{i}")
    reg.save_formation_plan(1, reg.snake_draft(reg.list_registrations(1), 2))
    reg.confirm_formation(1)
    reg.confirm_formation(1)
    assert db.execute("SELECT COUNT(*) FROM ttx_teams").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM ttx_team_members").fetchone()[0] == 4


def test_confirming_without_a_plan_raises(db):
    with pytest.raises(ValueError, match="no formation plan"):
        reg.confirm_formation(1)


def test_confirming_marks_the_plan_confirmed(db):
    _add(db, "Ada")
    reg.save_formation_plan(1, reg.snake_draft(reg.list_registrations(1), 1))
    reg.confirm_formation(1)
    assert db.execute("SELECT confirmed FROM ttx_formation_plan").fetchone()[0] == 1


# --------------------------------------------------------------------------
# Scenario fit
# --------------------------------------------------------------------------

def test_technical_ratio_of_an_empty_roster_is_zero(db):
    assert reg.technical_ratio([]) == 0.0


def test_technical_ratio_counts_technical_roles(db):
    _add(db, "Ada", role_id="malware_analyst", label="Malware Analyst")
    _add(db, "Grace", role_id="comms", label="Communications Officer")
    assert reg.technical_ratio(reg.list_registrations(1)) == 0.5


def test_scenario_fit_is_high_when_roles_line_up(db):
    _add(db, "Ada", role_id="ir_lead")
    assert reg.scenario_fit(reg.list_registrations(1), {"roles": ROLES}) == 1.0


def test_scenario_with_no_roles_is_neutral_not_zero(db):
    _add(db, "Ada")
    assert reg.scenario_fit(reg.list_registrations(1), {"roles": []}) == 0.5


def test_empty_roster_is_neutral_for_every_scenario(db):
    assert reg.scenario_fit([], {"roles": ROLES}) == 0.5


def test_reasoning_is_honest_about_an_empty_roster(db):
    assert "No registrations" in reg.recommendation_reasoning([], [])


def test_reasoning_names_the_best_option(db):
    _add(db, "Ada")
    text = reg.recommendation_reasoning(
        reg.list_registrations(1),
        [{"slug": "x", "label": "Cyber Siege", "fit_score": 1.0}],
    )
    assert "Cyber Siege" in text and "1 registered" in text
