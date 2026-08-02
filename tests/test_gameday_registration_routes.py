# CUI // SP-CTI
"""The registration routes must actually render and answer (gdx-reg-01).

Separate from ``test_gameday_registration.py``, which covers the logic. This
file covers the wiring, because the failure that produced this card was
*exactly* a wiring failure: the templates, the tables and the design doc all
existed and only the routes were missing, so nothing that tested the logic in
isolation would have noticed.

Both templates are rendered against a real Flask app. ``register.html`` iterates
``roles`` as a list while ``registrations.html`` does ``ROLES[role_id]`` on a
dict — a mismatch there raises only at render time, never at import.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

flask = pytest.importorskip("flask")
jinja2 = pytest.importorskip("jinja2")

from apps.ai_gameday import blueprint as bpmod  # noqa: E402
from apps.ai_gameday import registration as reg  # noqa: E402
from tests.test_gameday_registration import ROLES, SCHEMA, _Conn  # noqa: E402

SESSION = {
    "session_id": 1,
    "scenario_slug": "ai_gameday",
    "join_code": "ABC123",
    "max_teams": 4,
    "config_json": json.dumps({"scenario": {"roles": ROLES}}),
}


#: A stand-in for the dashboard chrome. The two templates under test extend
#: ``base.html``, which needs the dashboard's context processors (``nav_tree``
#: and friends). Rendering the real one here would make these tests fail
#: whenever unrelated dashboard chrome changes, which is not what they are for
#: — they assert that *the registration templates* render against *the context
#: the registration routes pass*. The stub keeps every block the pages define.
_BASE_STUB = (
    "<!doctype html><html><head><title>{% block title %}{% endblock %}</title>"
    "{% block head %}{% endblock %}</head>"
    "<body>{% block content %}{% endblock %}{% block scripts %}{% endblock %}</body></html>"
)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    raw = sqlite3.connect(str(tmp_path / "gd.db"))
    raw.row_factory = sqlite3.Row
    raw.executescript(SCHEMA)
    raw.commit()
    conn = _Conn(raw)

    monkeypatch.setattr(reg, "get_connection", lambda *a, **kw: conn)
    from tools.ttx import team_manager
    monkeypatch.setattr(team_manager, "get_connection", lambda *a, **kw: conn)

    monkeypatch.setattr(bpmod, "_ensure_init", lambda: None)
    monkeypatch.setattr(bpmod, "get_session", lambda sid: dict(SESSION) if sid == 1 else None)

    app = flask.Flask(
        __name__,
        template_folder=str(ROOT / "tools" / "dashboard" / "templates"),
    )
    app.jinja_loader = jinja2.ChoiceLoader([
        jinja2.DictLoader({"base.html": _BASE_STUB}),
        app.jinja_loader,
    ])
    app.config.update(TESTING=True, SECRET_KEY="test")

    @app.before_request
    def _auth():
        # Mirrors the dashboard's g.current_user contract that
        # apps/ai_gameday/auth.py reads. admin/IL5 so require_facilitator passes.
        flask.g.current_user = {
            "username": "facilitator", "role": "admin", "impact_level": "IL5",
        }

    app.register_blueprint(bpmod.bp)
    app.raw_db = raw
    return app.test_client()


def _register(client, name, skill="I reverse engineer malware samples"):
    return client.post(
        "/api/gameday/session/1/register",
        json={"player_name": name, "role_id": "malware_analyst",
              "role_label": "Malware Analyst", "stated_skill": skill},
    )


# --------------------------------------------------------------------------
# Pages render
# --------------------------------------------------------------------------

def test_register_page_renders(client):
    r = client.get("/gameday/session/1/register")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "ABC123" in body, "join code must reach the template"
    assert "Malware Analyst" in body, "roles must render as selectable cards"


def test_register_page_404s_for_an_unknown_session(client):
    assert client.get("/gameday/session/99/register").status_code == 404


def test_registrations_page_renders_with_roles_as_a_dict(client):
    """registrations.html does ROLES[role_id]; a list here would render wrong."""
    _register(client, "Ada")
    r = client.get("/gameday/session/1/registrations")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Ada" in body
    assert '"malware_analyst"' in body, "ROLES must be keyed by role id"


def test_registrations_page_renders_with_an_empty_roster(client):
    assert client.get("/gameday/session/1/registrations").status_code == 200


# --------------------------------------------------------------------------
# The API contract the templates call
# --------------------------------------------------------------------------

def test_match_skill_returns_the_shape_the_form_reads(client):
    r = client.post("/api/gameday/session/1/match-skill",
                    json={"stated_skill": "I reverse engineer malware samples"})
    d = r.get_json()
    assert d["ok"] is True
    assert set(d["match"]) >= {"role_id", "role_label", "confidence", "method", "reasoning"}
    assert d["match"]["role_id"] == "malware_analyst"


def test_match_skill_404s_for_an_unknown_session(client):
    r = client.post("/api/gameday/session/99/match-skill", json={"stated_skill": "x"})
    assert r.status_code == 404


def test_register_creates_a_registration(client):
    r = _register(client, "Ada")
    assert r.status_code == 201
    assert r.get_json()["ok"] is True
    assert len(reg.list_registrations(1)) == 1


def test_register_rejects_a_missing_name(client):
    r = client.post("/api/gameday/session/1/register", json={"role_id": "comms"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_form_teams_returns_the_counts_the_panel_shows(client):
    for n in ("Ada", "Grace", "Alan", "Katherine"):
        _register(client, n)
    r = client.post("/api/gameday/session/1/form-teams", json={"max_teams": 2})
    d = r.get_json()
    assert d["ok"] is True
    assert d["num_teams"] == 2
    assert d["total_players"] == 4
    assert sum(len(t["members"]) for t in d["teams"]) == 4


def test_form_teams_with_no_roster_is_a_400_not_a_crash(client):
    r = client.post("/api/gameday/session/1/form-teams", json={})
    assert r.status_code == 400
    assert "No registrations" in r.get_json()["error"]


def test_form_teams_falls_back_to_the_session_max(client):
    for n in ("Ada", "Grace", "Alan", "Katherine", "Edsger"):
        _register(client, n)
    d = client.post("/api/gameday/session/1/form-teams", json={}).get_json()
    assert d["num_teams"] == SESSION["max_teams"]


def test_move_updates_the_plan(client):
    for n in ("Ada", "Grace"):
        _register(client, n)
    teams = client.post("/api/gameday/session/1/form-teams",
                        json={"max_teams": 2}).get_json()["teams"]
    target = teams[0]["members"][0]["registration_id"]
    d = client.post("/api/gameday/session/1/formation-plan/move",
                    json={"registration_id": target, "target_team_slot": 1,
                          "target_team_name": "Team 2"}).get_json()
    assert d["ok"] is True
    slot = {m["registration_id"]: t["team_slot"] for t in d["teams"] for m in t["members"]}
    assert slot[target] == 1


def test_move_of_an_undrafted_player_is_a_400(client):
    _register(client, "Ada")
    r = client.post("/api/gameday/session/1/formation-plan/move",
                    json={"registration_id": 999, "target_team_slot": 0,
                          "target_team_name": "Team 1"})
    assert r.status_code == 400


def test_confirm_creates_teams_and_reports_counts(client):
    for n in ("Ada", "Grace", "Alan", "Katherine"):
        _register(client, n)
    client.post("/api/gameday/session/1/form-teams", json={"max_teams": 2})
    d = client.post("/api/gameday/session/1/confirm-teams", json={}).get_json()
    assert d["ok"] is True
    assert d["teams_created"] == 2
    assert d["members_created"] == 4


def test_confirm_without_a_draft_is_a_400(client):
    r = client.post("/api/gameday/session/1/confirm-teams", json={})
    assert r.status_code == 400
    assert "no formation plan" in r.get_json()["error"]


def test_delete_registration_removes_it(client):
    rid = _register(client, "Ada").get_json()["registration"]["registration_id"]
    assert client.delete(f"/api/gameday/registration/{rid}").status_code == 200
    assert reg.list_registrations(1) == []


def test_delete_of_an_unknown_registration_is_a_404(client):
    assert client.delete("/api/gameday/registration/999").status_code == 404


def test_scenario_recommendation_shape(client):
    _register(client, "Ada")
    d = client.get("/api/gameday/session/1/scenario-recommendation").get_json()
    assert d["ok"] is True
    assert set(d) >= {"tech_ratio", "reasoning", "recommended_slug", "all_options"}
    assert 0.0 <= d["tech_ratio"] <= 1.0


def test_setting_an_unknown_scenario_is_refused(client):
    r = client.patch("/api/gameday/session/1/scenario", json={"scenario_slug": "nope"})
    assert r.status_code == 404


def test_setting_a_scenario_requires_a_slug(client):
    r = client.patch("/api/gameday/session/1/scenario", json={})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# The whole flow
# --------------------------------------------------------------------------

def test_register_draft_move_confirm_end_to_end(client):
    for n in ("Ada", "Grace", "Alan", "Katherine", "Edsger", "Barbara"):
        _register(client, n)

    teams = client.post("/api/gameday/session/1/form-teams",
                        json={"max_teams": 3}).get_json()["teams"]
    assert len(teams) == 3

    moved = teams[0]["members"][0]["registration_id"]
    client.post("/api/gameday/session/1/formation-plan/move",
                json={"registration_id": moved, "target_team_slot": 2,
                      "target_team_name": "Team 3"})

    d = client.post("/api/gameday/session/1/confirm-teams", json={}).get_json()
    assert d["members_created"] == 6

    raw = client.application.raw_db
    assert raw.execute("SELECT COUNT(*) FROM ttx_teams").fetchone()[0] == 3
    # The manual move survived into the materialised teams.
    names = {
        r[0] for r in raw.execute(
            "SELECT t.team_name FROM ttx_team_members m "
            "JOIN ttx_teams t ON t.team_id = m.team_id "
            "WHERE m.player_name = (SELECT player_name FROM ttx_registrations "
            "WHERE registration_id = ?)", (moved,)
        ).fetchall()
    }
    assert names == {"Team 3"}
