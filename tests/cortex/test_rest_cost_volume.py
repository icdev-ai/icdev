# CUI // SP-CTI
"""Cost-volume pricing surface (prem-bid-02).

``generate_cost_volume()`` already existed and already wrote pg_cost_volumes — but it
had ZERO callers outside its own CLI. It was not missing code, it was DEAD code. And it
was dead for a reason: its audit write INSERTed into a column called ``timestamp`` that
does not exist on audit_trail, with no try/except, so every path through it raised.

This is its first real caller. The rule it enforces: **an unrated labour category is
surfaced, never guessed.** The old code priced one at $85/hr by default — a made-up
number loaded through the wrap rates and the price-to-win band until the total looked
exactly like a real one.
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask, g

from tools.cortex.blueprint import cortex_bp
from tools.cortex.schemas import CortexContext

SCOPE = "cortex:cost_volume"


def make_client(*, binding=None):
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)

    @app.before_request
    def _simulate_auth():
        # Mirror tools/dashboard/auth.py: a SERVICE-KEY caller gets role
        # "service" plus the binding's tenant; a dashboard SESSION user
        # gets neither. Measured 2026-08-15: 0 of 13 dashboard_users rows
        # carry a tenant_id, so a session user takes the canvas guard's
        # "authenticated but no tenant" early-allow. Giving the session
        # case a tenant made it a service principal with no service key --
        # a shape that exists nowhere -- and the guard then denied it on a
        # grant check no such caller can satisfy.
        if binding is not None:
            g.current_user = {"id": "cortex-svc:compass", "role": "service",
                              "tenant_id": "compass"}
        else:
            g.current_user = {"id": "u1", "role": "admin"}
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
def priced(monkeypatch):
    """Capture what reaches rate_benchmarker.generate_cost_volume."""
    calls = []

    def _fake(opportunity_id, contract_type="ffp", *, allow_unrated=False):
        calls.append({"opportunity_id": opportunity_id,
                      "contract_type": contract_type,
                      "allow_unrated": allow_unrated})
        if not allow_unrated:
            return {"status": "unpriced", "opportunity_id": opportunity_id,
                    "unrated": [{"labor_category": "Cyber Analyst",
                                 "reason": "no hourly rate ... not guessed either"}],
                    "unrated_count": 1, "priced_count": 0}
        return {"status": "partial", "opportunity_id": opportunity_id,
                "cost_volume_id": "cv-1", "line_items": [], "unrated_count": 1,
                "total_evaluated_price": 1000.0}

    mod = importlib.import_module("tools.govcon.rate_benchmarker")
    monkeypatch.setattr(mod, "generate_cost_volume", _fake)
    return calls


# ---------------------------------------------------------------------------


def test_an_unrated_lcat_refuses_the_price_rather_than_guessing(priced):
    """THE rule. A defaulted rate is a wrong price that looks like a right one."""
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/cost_volume", json={"opportunity_id": "opp-1"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "unpriced"
    assert body["unrated_count"] == 1
    assert "not guessed" in body["unrated"][0]["reason"]
    # No total was produced at all — there is nothing to mistake for a real price.
    assert "total_evaluated_price" not in body


def test_allow_unrated_gives_a_PARTIAL_price_never_an_ok_one(priced):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/cost_volume",
                       json={"opportunity_id": "opp-1", "allow_unrated": True})

    body = resp.get_json()
    assert body["status"] == "partial"      # NOT "ok"
    assert priced[0]["allow_unrated"] is True


def test_contract_type_is_passed_through(priced):
    client = make_client(binding=_binding([SCOPE]))
    client.post("/cortex/api/v1/cost_volume",
                json={"opportunity_id": "opp-1", "contract_type": "CPFF"})
    assert priced[0]["contract_type"] == "cpff"


def test_missing_opportunity_id_is_rejected(priced):
    client = make_client(binding=_binding([SCOPE]))
    assert client.post("/cortex/api/v1/cost_volume", json={}).status_code == 400
    assert priced == []


# -- scope ------------------------------------------------------------------


def test_the_scope_is_NOT_in_the_default_grant():
    """A key that can search must not silently also be able to put a PRICE on a bid."""
    from tools.cortex.service_keys import ALL_SCOPES, DEFAULT_SCOPES

    assert SCOPE in ALL_SCOPES
    assert SCOPE not in DEFAULT_SCOPES


def test_a_key_without_the_scope_is_denied(priced):
    client = make_client(binding=_binding(["cortex:search"]))
    resp = client.post("/cortex/api/v1/cost_volume", json={"opportunity_id": "opp-1"})
    assert resp.status_code == 403
    assert SCOPE in resp.get_json()["error"]
    assert priced == []


def test_cost_volume_is_advertised_on_the_health_probe():
    client = make_client()
    assert "cost_volume" in client.get("/cortex/api/v1/health").get_json()["operations"]


def test_the_client_has_a_pricing_method():
    from tools.cortex.client import CortexClient

    assert hasattr(CortexClient, "price_cost_volume")


# ---------------------------------------------------------------------------
# prem-bid-04 — accepting a price compass owns
# ---------------------------------------------------------------------------


@pytest.fixture
def accepted(monkeypatch):
    calls = []

    def _fake(**kw):
        calls.append(kw)
        return {"status": "accepted", "cost_volume_id": "cv-1",
                "opportunity_id": kw["opportunity_id"],
                "total_evaluated_price": 299376.0, "priced_by": kw["source"],
                "line_item_count": 2}

    mod = importlib.import_module("tools.govcon.cost_volume_intake")
    monkeypatch.setattr(mod, "accept_cost_volume", _fake)
    return calls


def test_a_pushed_price_is_ACCEPTED_not_recomputed(accepted, priced):
    """compass owns the price. ICDEV computing its own number would give two prices for
    one bid — worse than none, because somebody must then decide which is real."""
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/cost_volume", json={
        "opportunity_id": "opp-1",
        "priced": {"status": "ok", "total_price": 299376.0},
        "priced_by": "compass",
    })

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "accepted"
    assert accepted[0]["source"] == "compass"
    # And ICDEV's own pricing path was NOT run.
    assert priced == []


def test_the_key_is_authoritative_for_tenant_on_an_accepted_price(accepted):
    client = make_client(binding=_binding([SCOPE]))
    client.post("/cortex/api/v1/cost_volume", json={
        "opportunity_id": "opp-1",
        "priced": {"status": "ok", "total_price": 1.0},
        "tenant_id": "acme",           # hostile
    })
    assert accepted[0]["tenant_id"] == "compass"


def test_an_unknown_pricing_source_is_rejected(accepted):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/cost_volume", json={
        "opportunity_id": "opp-1",
        "priced": {"status": "ok", "total_price": 1.0},
        "priced_by": "whoever",
    })
    assert resp.status_code == 400
    assert accepted == []


def test_no_priced_body_still_COMPUTES_the_volume(priced):
    """The internal path survives — for opportunities compass never touched."""
    client = make_client(binding=_binding([SCOPE]))
    client.post("/cortex/api/v1/cost_volume", json={"opportunity_id": "opp-1"})
    assert priced and priced[0]["opportunity_id"] == "opp-1"


def test_the_client_has_a_push_method():
    from tools.cortex.client import CortexClient

    assert hasattr(CortexClient, "push_priced_cost_volume")
