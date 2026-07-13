# CUI // SP-CTI
"""Staffing-matrix intake surface (prem-pstaff-02).

Mappings registered here reach the proposal's STAFFING PLAN (via
proposal_key_personnel -> program_bridge._gather_key_personnel). That is the
point — and it is also why an UNEVIDENCED person -> LCAT mapping must be refused:
the mapping is what the proposal asserts to the government about who will do the
work, so an unevidenced one is an assertion nobody can defend at debrief. Same
defect class as an uncited win theme (tests/cortex/test_rest_win_themes.py).
"""
from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path

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
    """Capture what reaches key_personnel.register_person.

    The endpoint imports register_person INSIDE the request, so patching the
    module attribute is what the call site actually resolves.
    """
    calls = []

    def _fake_register(opportunity_id, name, proposed_lcat, evidence, *,
                       person_ref=None, qualification_verdict="qualified",
                       source=None, tenant_id="default", classification="CUI"):
        rows = manager.normalize_evidence(evidence)   # real refusal logic
        if not rows:
            return {"status": "refused", "name": name,
                    "reason": "no qualifying evidence — an unevidenced person -> "
                              "LCAT mapping is an assertion nobody can defend"}
        calls.append({
            "opportunity_id": opportunity_id, "name": name,
            "proposed_lcat": proposed_lcat, "evidence": rows,
            "person_ref": person_ref, "verdict": qualification_verdict,
            "source": source, "tenant_id": tenant_id,
            "classification": classification,
        })
        return {"status": "ok", "person_id": f"pkp-{len(calls)}",
                "person_ref": person_ref or f"name:{name.lower()}",
                "evidence_count": len(rows)}

    manager = importlib.import_module("tools.govcon.key_personnel")
    monkeypatch.setattr(manager, "register_person", _fake_register)
    return calls


EVIDENCED = {
    "name": "Dana Whitfield",
    "proposed_lcat": "Information Security Analyst, Senior",
    "person_ref": "compass:emp-4417",
    "qualification_verdict": "exceeds",
    "evidence": [
        {"claim": "11 years RMF/ATO packages, 4 as ISSO", "source": "resume p.2"},
        {"claim": "Active TS/SCI, CISSP", "source": "clearance record 2026-03"},
    ],
    "source": "compass qualification.py",
}


def test_evidenced_mapping_is_registered_with_its_evidence(registered):
    client = make_client(binding=_binding(["cortex:staffing"]))
    response = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1", "assignments": [EVIDENCED]})

    assert response.status_code == 200
    body = response.get_json()
    assert body["registered_count"] == 1
    assert body["refused_count"] == 0

    call = registered[0]
    assert call["opportunity_id"] == "opp-1"
    assert call["proposed_lcat"] == "Information Security Analyst, Senior"
    assert call["verdict"] == "exceeds"
    assert call["person_ref"] == "compass:emp-4417"
    # The citations survive — a reviewer can trace the LCAT back to the resume
    # line that justifies it, which is the whole point of the registry.
    sources = [row["source"] for row in call["evidence"]]
    assert "resume p.2" in sources
    assert "clearance record 2026-03" in sources


def test_unevidenced_person_is_refused_not_stored(registered):
    """The rule this endpoint exists for: no evidence, no mapping."""
    client = make_client(binding=_binding(["cortex:staffing"]))
    response = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        "assignments": [{"name": "Alex Rivera",
                         "proposed_lcat": "Software Developer, Senior"}]})

    assert response.status_code == 200
    body = response.get_json()
    assert body["registered_count"] == 0
    assert body["refused_count"] == 1
    assert body["refused"][0]["name"] == "Alex Rivera"
    assert "no qualifying evidence" in body["refused"][0]["reason"]
    assert registered == []          # nothing was written


def test_a_refused_person_does_not_block_the_evidenced_ones(registered):
    client = make_client(binding=_binding(["cortex:staffing"]))
    response = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        "assignments": [EVIDENCED,
                        {"name": "Alex Rivera", "proposed_lcat": "Developer"}]})

    body = response.get_json()
    assert body["registered_count"] == 1
    assert body["refused_count"] == 1
    assert len(registered) == 1


def test_empty_evidence_string_is_still_unevidenced(registered):
    client = make_client(binding=_binding(["cortex:staffing"]))
    response = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        "assignments": [{**EVIDENCED, "evidence": "   "}]})

    assert response.get_json()["refused_count"] == 1
    assert registered == []


def test_evidence_rows_without_a_claim_cite_nothing(registered):
    """A citation row with only a source proves nothing — it is not evidence."""
    client = make_client(binding=_binding(["cortex:staffing"]))
    response = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        "assignments": [{**EVIDENCED, "evidence": [{"source": "resume.pdf"}]}]})

    assert response.get_json()["refused_count"] == 1
    assert registered == []


def test_rendered_text_evidence_is_accepted(registered):
    client = make_client(binding=_binding(["cortex:staffing"]))
    response = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        "assignments": [{**EVIDENCED,
                         "evidence": "11 years RMF [resume p.2]"}]})

    assert response.get_json()["registered_count"] == 1
    assert registered[0]["evidence"][0]["claim"] == "11 years RMF [resume p.2]"


def test_identity_comes_from_the_session_not_the_body(registered):
    """tenant_id/classification are bound server-side by the key row. A caller
    that sends its own must not be able to write rows into another tenant."""
    client = make_client(binding=_binding(["cortex:staffing"]))
    client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        "tenant_id": "victim", "classification": "SECRET",
        "assignments": [EVIDENCED]})

    assert registered[0]["tenant_id"] == "compass"
    assert registered[0]["classification"] == "CUI"


def test_person_with_no_lcat_is_a_malformed_request(registered):
    """No LCAT means there is no mapping to evidence — that is a 400, not a
    person we are declining to trust."""
    client = make_client(binding=_binding(["cortex:staffing"]))
    response = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        "assignments": [{"name": "Dana Whitfield", "evidence": "resume"}]})

    assert response.status_code == 400
    assert registered == []


def test_bad_verdict_is_rejected(registered):
    client = make_client(binding=_binding(["cortex:staffing"]))
    response = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1",
        "assignments": [{**EVIDENCED, "qualification_verdict": "vibes"}]})

    assert response.status_code == 400
    assert registered == []


def test_missing_opportunity_id_is_rejected(registered):
    client = make_client(binding=_binding(["cortex:staffing"]))
    response = client.post("/cortex/api/v1/staffing_matrix",
                           json={"assignments": [EVIDENCED]})

    assert response.status_code == 400


def test_scope_is_not_in_the_default_grant():
    """A key that can search must not silently also be able to staff a bid."""
    keys = importlib.import_module("tools.cortex.service_keys")

    assert "cortex:staffing" in keys.ALL_SCOPES
    assert "cortex:staffing" not in keys.DEFAULT_SCOPES


def test_key_without_the_scope_is_denied(registered):
    client = make_client(binding=_binding(["cortex:search", "cortex:win_themes"]))
    response = client.post("/cortex/api/v1/staffing_matrix", json={
        "opportunity_id": "opp-1", "assignments": [EVIDENCED]})

    assert response.status_code == 403
    assert "cortex:staffing" in response.get_json()["error"]
    assert registered == []


def test_staffing_matrix_is_advertised_on_the_health_probe():
    client = make_client()
    operations = client.get("/cortex/api/v1/health").get_json()["operations"]
    assert "staffing_matrix" in operations


def test_verdicts_match_the_db_check_constraint():
    """The CHECK is derived from VERDICTS, not hand-maintained beside it."""
    manager = importlib.import_module("tools.govcon.key_personnel")
    migration = (Path(__file__).resolve().parents[2]
                 / "tools" / "db" / "migrations" / "266_proposal_key_personnel.sql")
    check = re.search(
        r"CHECK \(qualification_verdict IN \(([^)]*)\)\)",
        migration.read_text(encoding="utf-8"))

    assert check, "verdict CHECK constraint not found in migration 266"
    in_sql = set(re.findall(r"'([a-z]+)'", check.group(1)))
    assert in_sql == set(manager.VERDICTS)


def test_registered_personnel_reach_the_staffing_plan():
    """The load-bearing wiring: proposal_key_personnel -> program_bridge's
    staffing plan. If this breaks, a pushed person silently stops appearing and
    the bridge falls back to regex-scraping names out of prose (the original
    bug), which knows neither the LCAT nor the evidence."""
    bridge = importlib.import_module("tools.govcon.program_bridge")

    gather = inspect.getsource(bridge._gather_key_personnel)
    assert "proposal_key_personnel" in gather
    # The registry is consulted BEFORE the scrape, not as a decoration after it.
    assert gather.index("proposal_key_personnel") < gather.index("_NAME_PATTERN")

    # ...and what it gathers is rendered into the bridge document with the LCAT
    # and verdict, which the scraped name never had.
    render = inspect.getsource(bridge._render_key_personnel)
    assert "proposed_lcat" in render
    assert "qualification_verdict" in render
