# CUI // SP-CTI
"""penta-gd-07 — AI GameDay Flask blueprint route regression tests.

Two parts:

Part A — full facilitator-driven session lifecycle over the HTTP test client:
  create session -> join session (team) -> join team (member) -> dispatch
  inject -> submit response -> api-log receipt -> leaderboard -> session
  responses -> AAR. Asserts each step's status + payload and that scoring
  flows end-to-end (receipt + judge + time bonus) with a MOCKED LLM router.

Part B — a no-500 sweep over every GET route with dummy ids: 404/400 are
  acceptable, a 500 is a failure. Page routes render against the real dashboard
  templates with a lenient Jinja ``Undefined`` so missing dashboard-chrome
  context (``nav_tree`` etc., supplied by the full app but not this bare test
  app) doesn't masquerade as a 500.

Auth uses the ``g.current_user`` contract from tests/test_penta_gd_auth.py
(admin @ IL5 == facilitator). The LLM router is mocked via importlib+setattr on
tools.llm.router. A temp SQLite DB is seeded with the shared conftest schema,
then ``apps.ai_gameday.db.migrate()`` builds the full ttx_* schema (storage
translate layer / %s params — no raw sqlite3 in the query path).
"""

from __future__ import annotations

import hashlib
import importlib
import pathlib

import jinja2
import pytest
from flask import Flask, g

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TPL_DIR = REPO_ROOT / "tools" / "dashboard" / "templates"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gd_db(tmp_path, monkeypatch):
    """Temp SQLite DB seeded with the shared schema, then the full ttx_* schema
    via db.migrate() (process-global guard reset first)."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    db_mod = importlib.import_module("apps.ai_gameday.db")
    db_mod._migrated = False
    db_mod.migrate()
    return db_path


class _FakeResp:
    def __init__(self, content: str):
        self.content = content


class _FakeReq:
    def __init__(self, **kw):
        self.kw = kw


@pytest.fixture
def mock_router(monkeypatch):
    """Install a fake LLMRouter/LLMRequest so the judge scores deterministically
    (has_any_llm True + valid JSON => a real weighted score, never unscored)."""
    router_mod = importlib.import_module("tools.llm.router")

    class _FakeRouter:
        def __init__(self, *a, **k):
            pass

        def has_any_llm(self):
            return True

        def invoke(self, function, req):
            return _FakeResp(
                '{"scores": {"d1": 8, "d2": 7, "d3": 9}, '
                '"rationale": "solid response", "confidence": 0.9}'
            )

    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    monkeypatch.setattr(router_mod, "LLMRequest", _FakeReq)
    return _FakeRouter


class _SilentUndefined(jinja2.Undefined):
    """Renders missing dashboard-chrome context as empty instead of raising, so
    a page view that returns 200 in the full app doesn't 500 here purely for
    lack of the dashboard's context processors."""

    def __iter__(self):
        return iter(())

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return self

    def __getitem__(self, key):
        return self

    def __call__(self, *a, **k):
        return self

    def __str__(self):
        return ""

    def __html__(self):
        return ""

    def __bool__(self):
        return False


def _make_app(role="admin", impact_level="IL5"):
    app = Flask(__name__, template_folder=str(_TPL_DIR))
    app.secret_key = "penta-gd-07-routes"
    app.config["TESTING"] = True
    app.jinja_env.undefined = _SilentUndefined

    from apps.ai_gameday.blueprint import bp

    app.register_blueprint(bp)

    @app.route("/login", endpoint="login_page")
    def _login_page():  # pragma: no cover - trivial
        return "login", 200

    @app.before_request
    def _auth():
        g.current_user = {
            "id": "u-test",
            "role": role,
            "tenant_id": None,
            "impact_level": impact_level,
        }
        g.tenant_id = None

    return app


# ===========================================================================
# Part A — full session lifecycle
# ===========================================================================

def test_full_session_lifecycle(gd_db, mock_router):
    client = _make_app().test_client()

    # 1) create session (facilitator)
    r = client.post("/api/gameday/session", json={"scenario_slug": "ai_gameday"})
    assert r.status_code == 201, r.get_data(as_text=True)
    session = r.get_json()["session"]
    sid = session["session_id"]
    join_code = session["join_code"]
    assert sid and join_code

    # 2) join session -> creates a team
    r = client.post(
        "/api/gameday/session/join",
        json={"session_code": join_code, "team_name": "Alpha"},
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    team = r.get_json()["team"]
    team_id = team["team_id"]
    team_code = team["join_code"]
    assert team_id and team_code

    # 3) join team -> creates a member
    r = client.post(
        "/api/gameday/team/join",
        json={"team_code": team_code, "player_name": "Neo", "role_id": "role1"},
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    assert r.get_json()["member"]["player_name"] == "Neo"

    # 4) list injects -> pick the first
    r = client.get(f"/api/gameday/session/{sid}/injects")
    assert r.status_code == 200
    injects = r.get_json()["injects"]
    assert injects, "scenario seeded no injects"
    inject_id = injects[0]["inject_id"]

    # 5) dispatch the inject (facilitator)
    r = client.post(f"/api/gameday/inject/{inject_id}/dispatch")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # 6) api-log a receipt -> deterministic sha256[:16] hash
    payload = "knowledge-search-result"
    r = client.post(
        "/api/gameday/api-log",
        json={
            "session_id": sid,
            "team_id": team_id,
            "tool_slug": "knowledge.search",
            "result_payload": payload,
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    call_id = body["call_id"]
    assert body["result_hash"] == hashlib.sha256(payload.encode()).hexdigest()[:16]

    # 7) submit response citing the logged receipt (fast -> time bonus)
    r = client.post(
        "/api/gameday/response",
        json={
            "team_id": team_id,
            "inject_id": inject_id,
            "session_id": sid,
            "response_text": "Our coordinated plan of action.",
            "receipts": [{"tool": "knowledge.search", "call_id": call_id}],
            "time_taken_s": 90,
        },
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    res = r.get_json()
    assert res["ok"] is True
    # receipt credited (call_id was logged), judge scored (LLM mocked), fast bonus.
    assert res["receipt_pts"] > 0
    assert res["judge_unscored"] is False
    assert res["time_bonus_pts"] == 50  # <=120s bracket
    assert res["total_pts"] == res["receipt_pts"] + res["judge_pts"] + res["time_bonus_pts"]

    # 8) leaderboard shows the team
    r = client.get(f"/api/gameday/session/{sid}/leaderboard")
    assert r.status_code == 200
    lb = r.get_json()
    assert lb["total"] >= 1
    assert any(row["team_id"] == team_id for row in lb["leaderboard"])

    # 9) facilitator response monitor surfaces the submission
    r = client.get(f"/api/gameday/session/{sid}/responses")
    assert r.status_code == 200
    resp_body = r.get_json()
    assert resp_body["total"] >= 1
    row = resp_body["responses"][0]
    assert "coordinated plan" in (row.get("response_preview") or "")
    assert row.get("judge_unscored") is False

    # 10) AAR renders as text/plain markdown
    r = client.get(f"/api/gameday/session/{sid}/aar")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/plain")
    assert r.get_data(as_text=True).strip()


def test_receipt_hash_matches_sha256(gd_db):
    """The api-log endpoint returns a stable sha256[:16] of the payload."""
    client = _make_app().test_client()
    r = client.post("/api/gameday/session", json={"scenario_slug": "ai_gameday"})
    sess = r.get_json()["session"]
    r = client.post(
        "/api/gameday/session/join",
        json={"session_code": sess["join_code"], "team_name": "T"},
    )
    team_id = r.get_json()["team"]["team_id"]

    payload = "deterministic-payload-123"
    expected = hashlib.sha256(payload.encode()).hexdigest()[:16]
    hashes = set()
    for _ in range(2):
        r = client.post(
            "/api/gameday/api-log",
            json={"session_id": sess["session_id"], "team_id": team_id,
                  "tool_slug": "genesis.run", "result_payload": payload},
        )
        assert r.status_code == 200
        hashes.add(r.get_json()["result_hash"])
    assert hashes == {expected}


def test_submit_without_logged_receipt_earns_no_receipt_points(gd_db, mock_router):
    """A receipt whose call_id was never logged must not be credited (anti-spoof)."""
    client = _make_app().test_client()
    r = client.post("/api/gameday/session", json={"scenario_slug": "ai_gameday"})
    sess = r.get_json()["session"]
    r = client.post(
        "/api/gameday/session/join",
        json={"session_code": sess["join_code"], "team_name": "T"},
    )
    team_id = r.get_json()["team"]["team_id"]
    r = client.get(f"/api/gameday/session/{sess['session_id']}/injects")
    inject_id = r.get_json()["injects"][0]["inject_id"]

    r = client.post(
        "/api/gameday/response",
        json={
            "team_id": team_id,
            "inject_id": inject_id,
            "session_id": sess["session_id"],
            "response_text": "plan",
            "receipts": [{"tool": "knowledge.search", "call_id": "never-logged"}],
            "time_taken_s": None,
        },
    )
    assert r.status_code == 200
    assert r.get_json()["receipt_pts"] == 0


def test_session_state_transitions(gd_db):
    """PATCH state active/paused/ended round-trips without 500."""
    client = _make_app().test_client()
    sid = client.post(
        "/api/gameday/session", json={"scenario_slug": "ai_gameday"}
    ).get_json()["session"]["session_id"]

    for state in ("active", "paused", "ended"):
        r = client.patch(f"/api/gameday/session/{sid}/state", json={"state": state})
        assert r.status_code == 200, f"{state} -> {r.status_code}"
        assert r.get_json()["ok"] is True

    # Unknown state -> 400, never 500.
    r = client.patch(f"/api/gameday/session/{sid}/state", json={"state": "bogus"})
    assert r.status_code == 400


def test_missing_required_fields_are_400_not_500(gd_db):
    client = _make_app().test_client()
    # response submit without ids
    r = client.post("/api/gameday/response", json={})
    assert r.status_code == 400
    # api-log without ids
    r = client.post("/api/gameday/api-log", json={})
    assert r.status_code == 400
    # join session without code
    r = client.post("/api/gameday/session/join", json={})
    assert r.status_code == 400


# ===========================================================================
# Part B — no-500 sweep over GET routes
# ===========================================================================

_DUMMY = 99999

# (path, expected-not-500). Page routes render real templates via the lenient
# undefined; id-bearing pages hit their "not found" 404 branch with a dummy id.
_GET_ROUTES = [
    # dashboard-chrome pages (render full templates)
    "/gameday",
    "/gameday/scenarios",
    "/gameday/scenarios/builder",
    "/gameday/simulation",
    "/gameday/ai-league",
    "/gameday/ai-league/ops",
    "/gameday/ai-league/team/red",
    # id-bearing pages (dummy id -> 404 before render)
    f"/gameday/session/{_DUMMY}/play",
    f"/gameday/session/{_DUMMY}/facilitate",
    f"/gameday/leaderboard/{_DUMMY}",
    f"/gameday/session/{_DUMMY}/results",
    f"/gameday/session/{_DUMMY}/simulate",
    # JSON API GETs
    f"/api/gameday/session/{_DUMMY}/injects",
    f"/api/gameday/session/{_DUMMY}/leaderboard",
    f"/api/gameday/session/{_DUMMY}/responses",
    f"/api/gameday/session/{_DUMMY}/ribbons",
    f"/api/gameday/session/{_DUMMY}/ontology",
    f"/api/gameday/session/{_DUMMY}/simulate-state",
    f"/api/gameday/session/{_DUMMY}/aar",
    "/api/gameday/ontology/concepts",
    "/api/gameday/scenarios",
    "/api/gameday/inject-templates",
    "/api/gameday/ai-league/leaderboard",
    "/api/gameday/ai-league/team/red",
]


@pytest.mark.parametrize("path", _GET_ROUTES)
def test_get_route_never_500(gd_db, path):
    client = _make_app().test_client()
    r = client.get(path)
    assert r.status_code != 500, f"{path} -> 500\n{r.get_data(as_text=True)[:400]}"
    assert r.status_code in (200, 302, 400, 404), f"{path} -> {r.status_code}"
