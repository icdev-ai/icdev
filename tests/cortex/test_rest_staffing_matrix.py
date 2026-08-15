# CUI // SP-CTI
"""Staffing-matrix intake surface (prem-pstaff-02).

People registered here land in ``proposal_key_personnel``, which
``program_bridge._gather_key_personnel`` now reads to build a bid's Key Personnel
volume. Before it, that volume was built by regex-scraping capitalised bigrams out of
proposal prose — a pattern that matches "Program Manager" as readily as it matches a
person.

That is why an UNEVIDENCED mapping must be refused: a named human proposed for a labour
category with nothing behind the claim reaches the customer as an assertion nobody can
defend when they ask "why is she a Senior Systems Engineer?". Same defect class as an
uncited win theme.
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask, g

from tools.cortex.blueprint import cortex_bp
from tools.cortex.schemas import CortexContext

SCOPE = "cortex:staffing_matrix"
EVIDENCE = [{"claim": "12 yrs systems engineering on DoD C2", "source": "resume p2"}]


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
        g.security_context = {
            "tenant_id": "compass", "user_id": "u1", "classification": "CUI",
        }
        if binding is not None:
            g.cortex_binding = binding

    return app.test_client()


def _binding(scopes, *, tenant_id="compass", ceiling="CUI"):
    return {
        "ctx": CortexContext(tenant_id=tenant_id, classification=ceiling),
        "scopes": list(scopes),
        "label": "compass",
        "tenant_id": tenant_id,
        "classification_ceiling": ceiling,
    }


@pytest.fixture
def registered(monkeypatch):
    """Capture what reaches key_personnel.register_person."""
    calls = []

    def _fake_register(**kw):
        calls.append(kw)
        from tools.govcon.key_personnel import normalize_evidence

        rows = normalize_evidence(kw.get("evidence"))
        if not rows:
            return {"status": "refused",
                    "reason": f"'{kw.get('name')}' carries NO evidence."}
        return {"status": "registered", "id": f"kp{len(calls)}",
                "action": "registered", "evidence_count": len(rows)}

    mod = importlib.import_module("tools.cortex.rest_v1")
    kp = importlib.import_module("tools.govcon.key_personnel")
    monkeypatch.setattr(kp, "register_person", _fake_register)
    assert mod  # imported for the route registration side-effect
    return calls


def _person(**over):
    p = {
        "person_ref": "p-1",
        "name": "Dana Reeves",
        "proposed_lcat": "Senior Systems Engineer",
        "qualification_verdict": "qualified",
        "evidence": EVIDENCE,
    }
    p.update(over)
    return p


# ---------------------------------------------------------------------------


def test_an_evidenced_person_is_registered_with_their_evidence(registered):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/staffing_matrix",
                       json={"opportunity_id": "opp-1", "people": [_person()]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["registered_count"] == 1
    assert body["refused_count"] == 0
    assert registered[0]["evidence"] == EVIDENCE
    assert registered[0]["proposed_lcat"] == "Senior Systems Engineer"


def test_an_unevidenced_person_is_REFUSED_not_stored(registered):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/staffing_matrix",
                       json={"opportunity_id": "opp-1",
                             "people": [_person(evidence=None)]})
    assert resp.status_code == 200          # a refusal is not an error...
    body = resp.get_json()
    assert body["registered_count"] == 0    # ...but nothing was stored
    assert body["refused_count"] == 1
    assert "NO evidence" in body["refused"][0]["reason"]


def test_a_refused_person_does_not_block_the_evidenced_ones(registered):
    """A compass push of 30 people must not be lost because one has a thin resume."""
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        "people": [
            _person(person_ref="p-good"),
            _person(person_ref="p-bad", name="Sam Vance", evidence=""),
            _person(person_ref="p-also-good", name="Ada Kwan"),
        ],
    })
    body = resp.get_json()
    assert body["registered_count"] == 2
    assert body["refused_count"] == 1
    assert body["refused"][0]["person_ref"] == "p-bad"


def test_the_key_is_authoritative_for_tenant_and_classification(registered):
    """A request body can never widen its own binding."""
    client = make_client(binding=_binding([SCOPE], tenant_id="compass", ceiling="CUI"))
    client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        # A hostile body trying to write into another tenant at a higher marking.
        "people": [_person(tenant_id="acme", classification="SECRET")],
    })
    assert registered[0]["tenant_id"] == "compass"
    assert registered[0]["classification"] == "CUI"


# -- structural errors are 400s, not per-item refusals -----------------------


def test_missing_opportunity_id_is_rejected(registered):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/staffing_matrix", json={"people": [_person()]})
    assert resp.status_code == 400


def test_an_empty_people_list_is_rejected(registered):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/staffing_matrix",
                       json={"opportunity_id": "opp-1", "people": []})
    assert resp.status_code == 400


def test_an_unknown_source_is_rejected(registered):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1", "people": [_person(source="wherever")],
    })
    assert resp.status_code == 400


# -- scope ------------------------------------------------------------------


def test_the_scope_is_NOT_in_the_default_grant():
    """A key that can search must not silently also be able to STAFF a bid — to put a
    named human against a labour category the customer will price and evaluate."""
    from tools.cortex.service_keys import ALL_SCOPES, DEFAULT_SCOPES

    assert SCOPE in ALL_SCOPES
    assert SCOPE not in DEFAULT_SCOPES


def test_a_key_without_the_scope_is_denied(registered):
    client = make_client(binding=_binding(["cortex:search"]))
    resp = client.post("/cortex/api/v1/staffing_matrix",
                       json={"opportunity_id": "opp-1", "people": [_person()]})
    assert resp.status_code == 403
    assert SCOPE in resp.get_json()["error"]
    assert registered == []


def test_staffing_matrix_is_advertised_on_the_health_probe():
    client = make_client()
    ops = client.get("/cortex/api/v1/health").get_json()["operations"]
    assert "staffing_matrix" in ops


def test_the_client_has_a_push_method():
    from tools.cortex.client import CortexClient

    assert hasattr(CortexClient, "push_staffing_matrix")
