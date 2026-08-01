# CUI // SP-CTI
"""Win-theme intake surface (prem-recomp-05).

Themes registered here reach the /proposals + /rfi DRAFTING PROMPTS (via
capture_strategy.resolve_strategy -> response_drafter). That is the whole point —
and it is also why an UNCITED theme must be refused: an unproven claim injected
into a generative prompt is precisely the failure the TRUST rules exist to stop.
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask, g

from tools.cortex.blueprint import cortex_bp
from tools.cortex.schemas import CortexContext


def make_client(*, binding=None):
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)

    @app.before_request
    def _simulate_auth():
        g.current_user = {"id": "u1", "role": "service", "tenant_id": "compass"}
        g.security_context = {
            "tenant_id": "compass", "user_id": "u1", "classification": "CUI",
        }
        if binding is not None:
            g.cortex_binding = binding

    return app.test_client()


def _binding(scopes):
    return {
        "ctx": CortexContext(tenant_id="compass", classification="CUI"),
        "scopes": list(scopes),
        "label": "compass",
    }


@pytest.fixture
def registered(monkeypatch):
    """Capture what reaches win_theme_manager.register_theme."""
    calls = []

    def _fake_register(opportunity_id, theme_type, theme_statement,
                       supporting_evidence=None, target_eval_factor=None,
                       ghost_competitor=None, priority=1):
        calls.append({
            "opportunity_id": opportunity_id, "theme_type": theme_type,
            "statement": theme_statement, "evidence": supporting_evidence,
            "target_eval_factor": target_eval_factor, "priority": priority,
        })
        return {"status": "ok", "theme_id": f"t{len(calls)}"}

    manager = importlib.import_module("tools.govcon.win_theme_manager")
    monkeypatch.setattr(manager, "register_theme", _fake_register)
    return calls


CITED = {
    "statement": "Zero defects escaped to production across 6 CDRL deliveries",
    "evidence": [
        {"claim": "Zero Sev-1 defects in FY26", "source": "MSR 2026-06"},
        {"claim": "All 12 CDRLs on time", "source": "EVM 2026-06"},
    ],
    "target_eval_factor": "Technical Approach",
    "priority": 1,
}


def test_cited_theme_is_registered_with_its_evidence(registered):
    client = make_client(binding=_binding(["cortex:win_themes"]))
    response = client.post("/cortex/api/v1/win_themes", json={
        "opportunity_id": "opp-1", "themes": [CITED]})

    assert response.status_code == 200
    body = response.get_json()
    assert body["registered_count"] == 1
    assert body["refused_count"] == 0

    call = registered[0]
    assert call["opportunity_id"] == "opp-1"
    assert call["theme_type"] == "win_theme"
    assert call["target_eval_factor"] == "Technical Approach"
    # The citations survive into supporting_evidence — a reviewer can trace the
    # claim back to the artifact it came from.
    assert "MSR 2026-06" in call["evidence"]
    assert "EVM 2026-06" in call["evidence"]


def test_uncited_theme_is_refused_not_stored(registered):
    """An unproven claim must never reach a drafting prompt."""
    client = make_client(binding=_binding(["cortex:win_themes"]))
    response = client.post("/cortex/api/v1/win_themes", json={
        "opportunity_id": "opp-1",
        "themes": [{"statement": "We are the best at zero trust"}]})

    assert response.status_code == 200
    body = response.get_json()
    assert body["registered_count"] == 0
    assert body["refused_count"] == 1
    assert "no supporting evidence" in body["refused"][0]["reason"]
    assert registered == []  # nothing was written


def test_a_refused_theme_does_not_block_the_cited_ones(registered):
    client = make_client(binding=_binding(["cortex:win_themes"]))
    response = client.post("/cortex/api/v1/win_themes", json={
        "opportunity_id": "opp-1",
        "themes": [CITED, {"statement": "Trust us"}]})

    body = response.get_json()
    assert body["registered_count"] == 1
    assert body["refused_count"] == 1
    assert len(registered) == 1


def test_empty_evidence_string_is_still_uncited(registered):
    client = make_client(binding=_binding(["cortex:win_themes"]))
    response = client.post("/cortex/api/v1/win_themes", json={
        "opportunity_id": "opp-1",
        "themes": [{"statement": "Claim", "evidence": "   "}]})

    assert response.get_json()["refused_count"] == 1
    assert registered == []


def test_rendered_text_evidence_is_accepted(registered):
    client = make_client(binding=_binding(["cortex:win_themes"]))
    response = client.post("/cortex/api/v1/win_themes", json={
        "opportunity_id": "opp-1",
        "themes": [{"statement": "Claim",
                    "evidence": "Zero Sev-1 defects [MSR 2026-06]"}]})

    assert response.get_json()["registered_count"] == 1
    assert "MSR 2026-06" in registered[0]["evidence"]


def test_bad_theme_type_is_rejected(registered):
    client = make_client(binding=_binding(["cortex:win_themes"]))
    response = client.post("/cortex/api/v1/win_themes", json={
        "opportunity_id": "opp-1",
        "themes": [{**CITED, "theme_type": "vibes"}]})

    assert response.status_code == 400
    assert registered == []


def test_missing_opportunity_id_is_rejected(registered):
    client = make_client(binding=_binding(["cortex:win_themes"]))
    response = client.post("/cortex/api/v1/win_themes",
                           json={"themes": [CITED]})

    assert response.status_code == 400


def test_scope_is_not_in_the_default_grant():
    """A key that can search must not silently also be able to put words in a
    proposal's mouth."""
    keys = importlib.import_module("tools.cortex.service_keys")

    assert "cortex:win_themes" in keys.ALL_SCOPES
    assert "cortex:win_themes" not in keys.DEFAULT_SCOPES


def test_key_without_the_scope_is_denied(registered):
    client = make_client(binding=_binding(["cortex:search"]))
    response = client.post("/cortex/api/v1/win_themes", json={
        "opportunity_id": "opp-1", "themes": [CITED]})

    assert response.status_code == 403
    assert "cortex:win_themes" in response.get_json()["error"]
    assert registered == []


def test_win_themes_is_advertised_on_the_health_probe():
    client = make_client()
    assert "win_themes" in client.get("/cortex/api/v1/health").get_json()["operations"]


def test_themes_registered_here_reach_the_drafting_prompt():
    """The load-bearing wiring: pg_win_themes -> resolve_strategy ->
    build_strategy_block -> response_drafter's prompt. If this chain breaks, a
    pushed theme silently stops shaping the draft (the original bug)."""
    strategy = importlib.import_module("tools.govcon.capture_strategy")
    drafter = importlib.import_module("tools.govcon.response_drafter")

    # resolve_strategy reads the registry table the intake writes...
    source = importlib.import_module("inspect").getsource(strategy.resolve_strategy)
    assert "pg_win_themes" in source

    # ...and the drafter injects the block built from it.
    drafter_source = importlib.import_module("inspect").getsource(drafter)
    assert "build_strategy_block" in drafter_source
    assert "strategy_block" in importlib.import_module("inspect").getsource(
        drafter._build_prompt if hasattr(drafter, "_build_prompt") else drafter)
