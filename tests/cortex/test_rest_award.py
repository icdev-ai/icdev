# CUI // SP-CTI
"""A won bid becomes a PROPOSED delivery baseline (prem-bid-04).

``transition_from_opportunity`` existed but was reachable only from a dashboard route —
so a bid priced in compass could be WON and there was no way for compass to say so. The
win lived in one system and the contract in another, and a human retyped the number
across the gap.

Three invariants under test:

  * ``cortex:award`` is NOT in the default grant, and is NOT implied by
    ``cortex:cost_volume``. Pricing a bid and declaring it won are different powers: a
    key that can compute a number must not thereby be able to open a contract.
  * ``needs_attention`` is CARRIED, not swallowed. The baseline lands as a draft
    proposal precisely because things are still missing from it (period of performance,
    chiefly) — a caller that never sees the list will treat the draft as finished.
  * "the opportunity is not won" is a 400 with the reason, not a 500. It is a legitimate
    answer to a legitimate question.
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask, g

from tools.cortex.blueprint import cortex_bp
from tools.cortex.schemas import CortexContext
from tools.cortex.service_keys import ALL_SCOPES, DEFAULT_SCOPES

SCOPE = "cortex:award"


def make_client(*, binding=None):
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)

    @app.before_request
    def _simulate_auth():
        g.current_user = {"id": "u1", "role": "service", "tenant_id": "compass"}
        g.security_context = {"tenant_id": "compass", "user_id": "u1", "classification": "CUI"}
        if binding is not None:
            g.cortex_binding = binding

    return app.test_client()


def _binding(scopes):
    return {
        "ctx": CortexContext(tenant_id="compass", classification="CUI"),
        "scopes": list(scopes),
        "label": "compass",
        "tenant_id": "compass",
        "classification_ceiling": "CUI",
    }


@pytest.fixture
def awarded(monkeypatch):
    """Capture what reaches portfolio_manager.transition_from_opportunity."""
    calls = []

    def _fake(opportunity_id, created_by="system"):
        calls.append({"opportunity_id": opportunity_id, "created_by": created_by})
        if opportunity_id == "opp-not-won":
            return {"status": "error",
                    "message": "opportunity opp-not-won is 'submitted', not 'won'"}
        return {
            "status": "proposed",
            "contract_id": "ct-9",
            "opportunity_id": opportunity_id,
            "total_value": 4_200_000.0,
            "contract_type": "ffp",
            "clins": [{"clin": "0001", "value": 4_200_000.0}],
            "needs_attention": ["period of performance is not set — no dates on the "
                                "opportunity, and inventing them would be a guess"],
        }

    mod = importlib.import_module("tools.govcon.portfolio_manager")
    monkeypatch.setattr(mod, "transition_from_opportunity", _fake)
    return calls


# --------------------------------------------------------------------------
# The scope is its own power
# --------------------------------------------------------------------------
def test_award_scope_exists_but_is_not_granted_by_default():
    assert SCOPE in ALL_SCOPES
    assert SCOPE not in DEFAULT_SCOPES


def test_pricing_a_bid_does_not_let_you_declare_it_won(awarded):
    """A key with cost_volume but not award is REFUSED.

    This is the whole point of a separate scope. If cost_volume implied award, the key
    that computes the number would also be the key that opens the contract against it.
    """
    client = make_client(binding=_binding(["cortex:cost_volume"]))
    resp = client.post("/cortex/api/v1/award", json={"opportunity_id": "opp-1"})
    assert resp.status_code == 403
    assert SCOPE in resp.get_json()["error"]
    assert awarded == []  # never reached the transition


def test_award_allowed_with_scope(awarded):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/award", json={"opportunity_id": "opp-1"})
    assert resp.status_code == 200
    assert awarded[0]["opportunity_id"] == "opp-1"


def test_award_requires_authentication():
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)
    resp = app.test_client().post("/cortex/api/v1/award", json={"opportunity_id": "o"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------
# What comes back
# --------------------------------------------------------------------------
def test_baseline_carries_the_price_that_was_bid(awarded):
    client = make_client(binding=_binding([SCOPE]))
    body = client.post("/cortex/api/v1/award",
                       json={"opportunity_id": "opp-1"}).get_json()
    assert body["contract_id"] == "ct-9"
    assert body["total_value"] == 4_200_000.0
    assert body["clins"], "a contract with no CLINs is a contract with no money in it"


def test_needs_attention_is_carried_not_swallowed(awarded):
    """The draft is a PROPOSAL. What is still missing from it must survive the hop."""
    client = make_client(binding=_binding([SCOPE]))
    body = client.post("/cortex/api/v1/award",
                       json={"opportunity_id": "opp-1"}).get_json()
    assert body["status"] == "proposed"
    assert body["needs_attention"], "the caller must be told what contracts staff owe"
    assert "period of performance" in body["needs_attention"][0]


def test_created_by_defaults_to_compass_and_is_recorded(awarded):
    client = make_client(binding=_binding([SCOPE]))
    client.post("/cortex/api/v1/award", json={"opportunity_id": "opp-1"})
    assert awarded[0]["created_by"] == "compass"

    client.post("/cortex/api/v1/award",
                json={"opportunity_id": "opp-2", "created_by": "capture-lead"})
    assert awarded[1]["created_by"] == "capture-lead"


# --------------------------------------------------------------------------
# Refusals are answers, not crashes
# --------------------------------------------------------------------------
def test_not_won_is_a_400_with_the_reason(awarded):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/award", json={"opportunity_id": "opp-not-won"})
    assert resp.status_code == 400
    assert "not 'won'" in resp.get_json()["error"]


def test_missing_opportunity_id_is_refused(awarded):
    client = make_client(binding=_binding([SCOPE]))
    for body in ({}, {"opportunity_id": "   "}):
        resp = client.post("/cortex/api/v1/award", json=body)
        assert resp.status_code == 400
        assert awarded == []


def test_award_is_advertised_on_the_health_probe():
    """A surface compass cannot discover is a surface compass will not use."""
    client = make_client()
    body = client.get("/cortex/api/v1/health").get_json()
    assert "award" in body["operations"]
