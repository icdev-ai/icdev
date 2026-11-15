# CUI // SP-CTI
"""Console pages and read MCP tools for Cortex, ACE and Co-Workers (icx-cnf-05).

The feature-conformance audit (icx-cnf-01) found 24 GUI and 10 AI-native gaps:
REST verbs no page called, features with no page, and features whose only MCP
tool mutates. This pins what closed them:

  * each new page renders, is linked from /cortex (a nav page), and carries the
    literal route paths it calls, so the audit sees the caller;
  * the PMO page never calls award and never sends a BOM credibility
    designation (both are a person's decision, icx-cnf-02);
  * the JSON reads and the MCP tools return the same read model, read only, and
    the service-key read never exposes a hash (nor, over MCP, a prefix);
  * every new MCP tool is declared read-only and authorised at tier ``read``,
    while ``cortex_govern`` stays mutating (running the chain writes audit rows);
  * ``POST /api/ace/events/emit`` is retired and ``/coworker/chat`` calls
    ``POST /api/ace/chat``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATES = ROOT / "tools" / "dashboard" / "templates"

NEW_TOOLS = (
    "cortex_pmo_status",
    "cortex_intake_sessions",
    "cortex_trust_chain",
    "cortex_findings_list",
    "cortex_metrics",
    "cortex_agent_launches",
    "cortex_service_keys",
    "cortex_airgap_posture",
    "ace_checkpoints",
    "coworkers_roster",
)


def _isolated_app(blueprint, role: str):
    """A fresh Flask app with the dashboard's rendering context (see
    tests/cortex/test_blueprint_routes.py for why never the shared singleton),
    and a signed-in user of ``role``."""
    from flask import Flask, g

    from tools.dashboard.app import app as _dashboard_app

    app = Flask(
        __name__,
        template_folder=_dashboard_app.template_folder,
        static_folder=_dashboard_app.static_folder,
    )
    app.config.update(_dashboard_app.config)
    app.jinja_env.filters.update(_dashboard_app.jinja_env.filters)
    app.jinja_env.globals.update(_dashboard_app.jinja_env.globals)
    app.template_context_processors[None].extend(
        _dashboard_app.template_context_processors.get(None, [])
    )
    app.secret_key = "test-secret"
    app.config["TESTING"] = True

    @app.before_request
    def _sign_in():
        g.current_user = {"id": "test-admin", "role": role}

    app.register_blueprint(blueprint)
    return app


@pytest.fixture
def db(icdev_db, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))
    from tools.cortex.metrics import reset_memo

    reset_memo()
    return icdev_db


def _client(role: str):
    from tools.cortex.blueprint import cortex_bp

    return _isolated_app(cortex_bp, role).test_client()


@pytest.fixture
def admin(db):
    with _client("admin") as c:
        yield c


# ── Pages ────────────────────────────────────────────────────────────────────

PAGES = {
    "/cortex/verbs": "Cortex Verbs",
    "/cortex/pmo": "Cortex PMO",
    "/cortex/intake": "Cortex Intake",
    "/cortex/govern": "Cortex TRUST Chain",
    "/cortex/airgap": "Cortex Air-Gap Posture",
    "/cortex/service-keys": "Cortex Service Keys",
}


@pytest.mark.parametrize("path,heading", sorted(PAGES.items()))
def test_each_page_renders_with_banner_and_iqe(admin, path, heading):
    resp = admin.get(path)
    assert resp.status_code == 200, resp.data[:400]
    body = resp.data.decode("utf-8")
    assert heading in body
    assert "CUI // SP-CTI" in body
    assert "iqe" in body.lower()


def test_the_cortex_index_links_every_new_page(admin):
    body = admin.get("/cortex/").data.decode("utf-8")
    for path in PAGES:
        assert f'href="{path}"' in body, path


def test_service_keys_are_for_admin_and_isso_only(db):
    with _client("developer") as c:
        assert c.get("/cortex/service-keys").status_code == 403
        assert c.get("/cortex/api/service-keys").status_code == 403
    with _client("isso") as c:
        assert c.get("/cortex/service-keys").status_code == 200


# ── What the pages call ──────────────────────────────────────────────────────


def test_the_verbs_page_calls_the_five_governed_verbs():
    text = (TEMPLATES / "cortex" / "verbs.html").read_text(encoding="utf-8")
    for verb in ("resolve", "complete", "reason", "classify", "extract"):
        assert f"'/cortex/api/v1/{verb}'" in text


def test_the_pmo_page_never_takes_a_decision():
    from tools.cortex.console import PMO_VERBS

    text = (TEMPLATES / "cortex" / "pmo.html").read_text(encoding="utf-8")
    for verb in PMO_VERBS:
        if verb["verb"] == "award":
            assert verb["path"] not in text, "award is a person's decision, not a page button"
        else:
            assert f"'{verb['path']}'" in text, verb["verb"]
    # The BOM call sends documents only: no designation reaches the engine.
    bom_call = text[text.index("function pmoBom"):]
    assert "credibility_tier" not in bom_call.split("No role or credibility_tier")[1]


def test_the_intake_and_govern_pages_call_their_routes():
    intake = (TEMPLATES / "cortex" / "intake.html").read_text(encoding="utf-8")
    assert "'/cortex/api/v1/intake/session'" in intake
    assert "'/cortex/api/v1/intake/turn'" in intake
    govern = (TEMPLATES / "cortex" / "govern.html").read_text(encoding="utf-8")
    assert "'/cortex/api/v1/govern'" in govern
    assert "'/cortex/api/agent-launches'" in govern


# ── JSON reads ───────────────────────────────────────────────────────────────


def test_pmo_status_without_an_opportunity_is_the_catalog(admin):
    data = admin.get("/cortex/api/pmo/status").get_json()
    assert data["status"] == "no_opportunity"
    decisions = {v["verb"] for v in data["verbs"] if v["decision"]}
    assert decisions == {"award", "bom"}


def test_pmo_status_reads_an_opportunity(admin):
    data = admin.get("/cortex/api/pmo/status?opportunity_id=OPP-T1").get_json()
    assert data["opportunity_id"] == "OPP-T1"
    assert data["status"] in {"ok", "partial"}
    assert data["key_personnel"] == []
    assert data["cost_volume"] is None


def test_trust_chain_lists_the_gates_and_profiles(admin):
    from tools.cortex import governance as gov

    data = admin.get("/cortex/api/trust-chain").get_json()
    assert data["gate_order"] == list(gov.GATE_ORDER)
    assert set(gov.MANDATORY_GATES) <= set(data["profiles"]["default"])
    assert data["status"] in {"idle", "ok", "unavailable"}


def test_airgap_posture_follows_the_switch(admin, monkeypatch):
    monkeypatch.delenv("ICDEV_AIRGAP", raising=False)
    assert admin.get("/cortex/api/airgap").get_json()["airgap"] is False
    monkeypatch.setenv("ICDEV_AIRGAP", "1")
    data = admin.get("/cortex/api/airgap").get_json()
    assert data["airgap"] is True
    assert isinstance(data["ready"], bool)


def test_agent_launches_and_intake_sessions_read_a_fresh_db(admin):
    launches = admin.get("/cortex/api/agent-launches").get_json()
    assert launches["status"] in {"idle", "unavailable"} and launches["launches"] == []
    sessions = admin.get("/cortex/api/intake/sessions").get_json()
    assert sessions["sessions"] == []


def test_service_keys_never_show_a_hash_and_mcp_drops_the_prefix(admin):
    from tools.cortex import service_keys as sk
    from tools.mcp.gap_handlers import handle_cortex_service_keys

    sk.create_key(label="cnf05-test", tenant_id="t1")
    page = admin.get("/cortex/api/service-keys").get_json()
    assert page["count"] == 1 and page["active"] == 1
    key = page["keys"][0]
    assert "key_hash" not in key and key["key_prefix"]
    assert set(page["scopes"]["default"]).isdisjoint(page["scopes"]["explicit_only"])

    tool = handle_cortex_service_keys({})
    assert tool["count"] == 1
    assert "key_prefix" not in tool["keys"][0] and "key_hash" not in tool["keys"][0]


# ── MCP declarations ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_each_new_tool_is_declared_read_only_at_tier_read(name):
    from tools.mcp import tool_registry as reg

    assert name in reg.list_tools()
    assert reg.READ_ONLY_DECLARATIONS[name] is True
    assert reg.tool_authorization(name)["tier"] == reg.TIER_READ


def test_cortex_govern_stays_mutating():
    """Running the chain writes audit and provenance rows; its read is cortex_trust_chain."""
    from tools.mcp import tool_registry as reg

    assert reg.READ_ONLY_DECLARATIONS["cortex_govern"] is False


def test_the_mcp_reads_answer_on_a_fresh_db(db):
    from tools.mcp import gap_handlers as gh

    assert gh.handle_cortex_pmo_status({})["status"] == "no_opportunity"
    assert "gate_order" in gh.handle_cortex_trust_chain({"window_hours": 1})
    assert "summary" in gh.handle_cortex_metrics({})
    assert "stats" in gh.handle_cortex_findings_list({})
    assert gh.handle_cortex_agent_launches({})["launches"] == []
    assert "airgap" in gh.handle_cortex_airgap_posture({})
    assert gh.handle_ace_checkpoints({}) == {"error": "instance_id is required"}
    roster = gh.handle_coworkers_roster({"kind": "persona"})
    assert all(c["kind"] == "persona" for c in roster["coworkers"])
    assert roster["count"] == roster["counts"]["persona"]


# ── ACE ──────────────────────────────────────────────────────────────────────


def test_ace_chat_has_a_page_and_events_emit_is_retired():
    from flask import Flask

    from tools.ace.blueprint import ace_api_bp, ace_bp

    app = Flask(__name__)
    app.register_blueprint(ace_bp)
    rules = {r.rule: r.endpoint for r in app.url_map.iter_rules()}
    assert rules.get("/coworker/chat") == "ace.chat_page"
    assert "/api/ace/events/emit" not in rules
    assert "ace_api" in app.blueprints and ace_api_bp.name == "ace_api"
    chat = (TEMPLATES / "ace" / "chat.html").read_text(encoding="utf-8")
    assert "fetch('/api/ace/chat'" in chat
    sessions = (TEMPLATES / "ace" / "sessions.html").read_text(encoding="utf-8")
    assert 'href="/coworker/chat"' in sessions
