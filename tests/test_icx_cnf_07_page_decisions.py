# CUI // SP-CTI
"""The last two GUI gaps: each decision has a page, through its declared path (icx-cnf-07).

The feature-conformance audit (icx-cnf-01) left two mutating routes that no page
called. Both were already declared ``workflow_hitl`` decisions (icx-cnf-02):

  * ``DELETE /api/ace/profiles/<role_id>`` is the PERSON'S step of
    ``decl-wft-ace-profile-delete`` (held by admin, pm), taken in the request;
  * ``POST /cortex/api/v1/award`` only PROPOSES ``decl-wft-cortex-award``: a
    contracts person other than the proposer accepts at ``/decisions``. A won
    bid never self-approves.

This pins what closed them, end to end:

  * ``/coworker/roles`` lists the generated profiles with a Delete button that
    calls the route, and offers it only to a holder; a built-in role gets none;
  * ``/cortex/pmo`` has a Propose-award card that posts the opportunity id and
    nothing else, then sends the person to ``/decisions``; it never decides;
  * the call each page makes, replayed against the real route, runs through the
    declared step: a non-holder deletes nothing, the proposer cannot accept.

Run: pytest tests/test_icx_cnf_07_page_decisions.py -v
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATES = ROOT / "tools" / "dashboard" / "templates"
ROLES = TEMPLATES / "coworker" / "roles.html"
PMO = TEMPLATES / "cortex" / "pmo.html"

PM = {"id": "u-pm", "role": "pm"}
DEV = {"id": "u-dev", "role": "developer"}
CO = {"id": "u-co", "role": "co"}

GENERATED = {"role_id": "gen_analyst", "display_name": "Generated Analyst",
             "description": "made by the generator", "source": "generated",
             "trust_tier": "yellow"}
BUILTIN = {"role_id": "ai_developer", "display_name": "AI Developer", "description": "",
           "source": "builtin", "trust_tier": "yellow"}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _isolated_app(user, *blueprints):
    """A fresh Flask app with the dashboard's rendering context (the convention in
    tests/cortex/test_cortex_console_pages.py) and ``user`` signed in."""
    from flask import Flask, g

    from tools.dashboard.app import app as _dashboard_app

    app = Flask(__name__, template_folder=_dashboard_app.template_folder,
                static_folder=_dashboard_app.static_folder)
    app.config.update(_dashboard_app.config)
    app.jinja_env.filters.update(_dashboard_app.jinja_env.filters)
    app.jinja_env.globals.update(_dashboard_app.jinja_env.globals)
    app.template_context_processors[None].extend(
        _dashboard_app.template_context_processors.get(None, []))
    app.secret_key = "test-secret"
    app.config["TESTING"] = True

    @app.before_request
    def _sign_in():
        g.current_user = dict(user) if user else None
        g.security_context = {"tenant_id": "default", "user_id": (user or {}).get("id", ""),
                              "classification": "CUI"}

    for bp in blueprints:
        app.register_blueprint(bp)
    return app.test_client()


# ── ACE: /coworker/roles deletes a generated profile ─────────────────────────


@pytest.fixture()
def ace_env(wf_db, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(tmp_path / "ace.db"))
    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    monkeypatch.setitem(bp_mod._state, "db_ready", True)
    import icdev.tools.ace.profile_generator as pg

    deleted: list[str] = []
    monkeypatch.setattr(pg, "list_profiles", lambda: [dict(BUILTIN), dict(GENERATED)])
    monkeypatch.setattr(pg, "delete_profile",
                        lambda rid: deleted.append(rid) or {"role_id": rid, "deleted": True})
    return bp_mod, deleted


def _roles_call() -> tuple[str, str]:
    """(url prefix, method) of the delete call the roles page makes.

    The page names the route by endpoint id (icx-cnf-08); its rule comes from the
    generated table, so the replay below hits exactly the route the page calls.
    """
    from tools.dashboard.routes_gen import rule_of

    text = _text(ROLES)
    match = re.search(r"icdevFetch\('(ace_api\.api_profiles_delete)',\s*\{role_id:\s*\w+\},\s*"
                      r"\{\s*method:\s*'(\w+)'", text)
    assert match, "the roles page makes no delete call to ace_api.api_profiles_delete"
    return rule_of(match.group(1)).replace("<role_id>", ""), match.group(2)


def test_the_roles_page_calls_the_profile_delete_route():
    prefix, method = _roles_call()
    assert (prefix, method) == ("/api/ace/profiles/", "DELETE")
    text = _text(ROLES)
    # A refusal from the declared step is shown to the person, not swallowed.
    assert "workflow_refused" in text
    # Deletion cannot be undone: the person confirms before the call.
    assert "confirm(" in text


def test_the_roles_page_offers_delete_only_on_generated_profiles_to_a_holder(ace_env):
    bp_mod, _ = ace_env
    body = _isolated_app(PM, bp_mod.ace_bp).get("/coworker/roles").get_data(as_text=True)
    assert "Generated profiles" in body
    assert 'data-delete-profile="gen_analyst"' in body
    assert 'data-delete-profile="ai_developer"' not in body
    assert "decl-wft-ace-profile-delete" in body

    as_dev = _isolated_app(DEV, bp_mod.ace_bp).get("/coworker/roles").get_data(as_text=True)
    assert 'data-delete-profile="gen_analyst"' not in as_dev
    assert "held by admin, pm" in as_dev


def test_the_roles_page_call_runs_through_the_persons_step(ace_env):
    bp_mod, deleted = ace_env
    prefix, method = _roles_call()
    url = prefix + "gen_analyst"

    refused = _isolated_app(DEV, bp_mod.ace_api_bp).open(url, method=method)
    assert refused.status_code == 403
    assert refused.get_json()["workflow_refused"] is True
    assert deleted == []

    ok = _isolated_app(PM, bp_mod.ace_api_bp).open(url, method=method)
    assert ok.status_code == 200, ok.get_data(as_text=True)
    step = ok.get_json()["decision_step"]
    assert step["template_id"] == "decl-wft-ace-profile-delete"
    assert step["decided_by"] == "u-pm" and step["status"] == "decided"
    assert deleted == ["gen_analyst"]


# ── Cortex: /cortex/pmo proposes an award ────────────────────────────────────


def _pmo_award_body() -> str:
    text = _text(PMO)
    start = text.find("function pmoAward")
    assert start >= 0, "the PMO page has no award call"
    return text[start:text.index("</script>", start)]


def test_the_pmo_page_proposes_award_with_the_opportunity_id_only():
    from tools.cortex.console import PMO_VERBS

    award = next(v for v in PMO_VERBS if v["verb"] == "award")
    text = _text(PMO)
    # By endpoint id (icx-cnf-08), and that endpoint's rule is the verb's own path.
    from tools.dashboard.routes_gen import rule_of
    assert "award: icdevRoute('cortex.api_v1_award')" in text
    assert rule_of("cortex.api_v1_award") == award["path"]
    call = _pmo_award_body()
    assert "cortexPost(CORTEX_PMO_PATHS.award, {opportunity_id: opp})" in call
    # The page proposes and sends the person to /decisions; it never decides.
    assert "/decisions" in call
    assert "/decide" not in text
    assert "accept" not in call.split("cortexPost(", 1)[1].split(")", 1)[0]


def test_the_pmo_page_renders_the_award_card(wf_db):
    from tools.cortex.blueprint import cortex_bp

    body = _isolated_app(PM, cortex_bp).get("/cortex/pmo").get_data(as_text=True)
    assert 'id="pmo-award"' in body
    assert "Propose award" in body
    assert 'href="/decisions"' in body
    assert "decl-wft-cortex-award" in body


def test_the_pmo_page_call_is_a_proposal_another_person_accepts(wf_db, monkeypatch):
    import tools.govcon.portfolio_manager as pm_mod
    from tools.cortex.blueprint import cortex_bp
    from tools.workflow_hitl import declared
    from tools.workflow_hitl.decisions_blueprint import decisions_bp

    monkeypatch.setattr(pm_mod, "transition_from_opportunity",
                        lambda oid, created_by="": {"status": "ok", "contract_id": "c-7",
                                                    "contract_status": "draft"})
    from tools.dashboard.routes_gen import rule_of

    endpoint = re.search(r"award: icdevRoute\('([\w.]+)'\)", _text(PMO)).group(1)
    path = rule_of(endpoint)
    resp = _isolated_app(PM, cortex_bp).post(path, json={"opportunity_id": "opp-77"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    step = resp.get_json()["acceptance"]
    assert step["template_id"] == "decl-wft-cortex-award" and step["status"] == "pending"

    # A won bid never self-approves: the proposer cannot accept it.
    as_pm = _isolated_app(PM, decisions_bp)
    assert as_pm.post(f"/decisions/api/steps/{step['step_id']}/decide",
                      json={"decision": "accept"}).status_code == 403
    pending = declared.list_steps("pending")
    assert [s["step_id"] for s in pending] == [step["step_id"]]
    assert pending[0]["proposed_by"] == "u-pm"

    as_co = _isolated_app(CO, decisions_bp)
    done = as_co.post(f"/decisions/api/steps/{step['step_id']}/decide",
                      json={"decision": "accept"})
    assert done.status_code == 200 and done.get_json()["decided_by"] == "u-co"
