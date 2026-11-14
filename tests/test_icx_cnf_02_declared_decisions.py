# CUI // SP-CTI
"""Declared decision templates: every decision a person takes runs through one (icx-cnf-02).

Covers:
  * the templates under args/workflow_hitl_templates/ are well formed, seed into
    wf_templates, and each decides surfaces no other template decides;
  * take_step: a holder's step opens a wf instance and closes its manual stage;
    no person, a service key, or the wrong role is refused; deny ends ``rejected``;
    a store that cannot record the step refuses it (fail closed);
  * propose / decide / spend: separation from the proposer, one decision, one
    spend, the exact payload;
  * the migration's tables are append-only;
  * the routes: ACE launch, HITL resolve, suggestion confirm/decline, profile
    delete; Cortex chat confirm, REST agent, award and BOM; the Cortex finding
    adjudication; the /decisions page and its decide route;
  * ACE abort stays an immediate stop;
  * the hitl_workflow coherence check maps every decision endpoint to a template.

Run: pytest tests/test_icx_cnf_02_declared_decisions.py -v
"""
from __future__ import annotations

import base64
import importlib
import io
import sqlite3

import pytest
from flask import Flask, g

PM = {"id": "u-pm", "role": "pm"}
DEV = {"id": "u-dev", "role": "developer"}
CO = {"id": "u-co", "role": "co"}
SERVICE = {"id": "cortex-svc:compass", "role": "service"}


@pytest.fixture()
def wf_db(icdev_db, monkeypatch):
    """The platform DB (MINIMAL_ICDEV_SCHEMA carries the wf_ and ledger tables)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    return icdev_db


@pytest.fixture()
def declared(wf_db):
    from tools.workflow_hitl import declared as mod

    return mod


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ── Templates ────────────────────────────────────────────────────────────────


def test_every_template_is_well_formed_and_decides_its_own_surfaces():
    from tools.workflow_hitl import declared

    templates = declared.load_templates()
    assert len(templates) == 8
    owners = {}
    for tid, tmpl in templates.items():
        assert declared.validate_template(tmpl) == [], tid
        stage = declared.manual_stage(tmpl)
        assert stage["role"] and "service" not in stage["holders"]
        for surface in tmpl["decides"]:
            assert surface not in owners, f"{surface} decided by {owners.get(surface)} and {tid}"
            owners[surface] = tid
    assert set(owners) == {
        "ace_api.api_hitl_resolve", "ace_api.api_suggestion_confirm",
        "ace_api.api_suggestion_decline", "ace_api.api_launch", "ace_api.api_session_rerun",
        "ace_api.api_profiles_delete", "cortex.api_chat", "cortex.api_v1_agent",
        "cortex.api_v1_award", "cortex.api_v1_bom",
        "dic.api_explorer_cortex_finding_adjudicate",
    }
    assert declared.manual_stage(templates["decl-wft-cortex-award"])["separate_from"] == "proposer"


def test_a_malformed_template_is_named():
    from tools.workflow_hitl import declared

    bad = {"id": "x", "decides": ["a.b"], "stages": [
        {"name": "propose", "step_type": "automated"},
        {"name": "go", "step_type": "manual", "holders": ["service"],
         "decisions": {"go": "maybe"}}]}
    errors = " ".join(declared.validate_template(bad))
    assert "no role" in errors and "service key" in errors and "affirm or deny" in errors


def test_seed_is_idempotent_and_never_a_canvas_default(declared, wf_db):
    first = declared.seed_declared_templates()
    second = declared.seed_declared_templates()
    assert len(first["seeded"]) == 8 and second == {"seeded": [], "updated": []}
    rows = _rows(wf_db, "SELECT id, is_default, is_system FROM wf_templates "
                        "WHERE id LIKE 'decl-wft-%'")
    assert len(rows) == 8 and all(r[1] == 0 and r[2] == 1 for r in rows)


# ── take_step ────────────────────────────────────────────────────────────────


def test_a_holders_step_opens_and_closes_a_workflow_instance(declared, wf_db):
    step = declared.take_step("decl-wft-ace-run-launch", surface="ace_api.api_launch",
                              decision="launch", subject="triage", actor=DEV)
    assert step["status"] == "decided" and step["decided_by"] == "u-dev"
    inst = _rows(wf_db, "SELECT template_id, status, current_stage FROM wf_instances WHERE id=?",
                 (step["instance_id"],))
    assert inst == [("decl-wft-ace-run-launch", "approved", "launch")]
    fb = _rows(wf_db, "SELECT decision, submitted_by FROM wf_feedback WHERE instance_id=?",
               (step["instance_id"],))
    assert fb == [("approve", "u-dev")]
    ledger = _rows(wf_db, "SELECT event, actor FROM wf_decision_steps WHERE step_id=?",
                   (step["step_id"],))
    assert ledger == [("decided", "u-dev")]


def test_a_deny_ends_the_instance_rejected_with_the_reason(declared, wf_db):
    step = declared.take_step("decl-wft-ace-hitl-resolve", surface="ace_api.api_hitl_resolve",
                              decision="reject", reason="wrong target", actor=DEV)
    assert step["affirmative"] is False
    assert _rows(wf_db, "SELECT status FROM wf_instances WHERE id=?",
                 (step["instance_id"],)) == [("rejected",)]
    assert _rows(wf_db, "SELECT decision, kickback_reason FROM wf_feedback WHERE instance_id=?",
                 (step["instance_id"],)) == [("kickback", "wrong target")]


@pytest.mark.parametrize("actor,status,code", [
    (None, 401, "no_person"),
    (SERVICE, 403, "not_a_person"),
    (DEV, 403, "role"),
])
def test_only_a_person_holding_the_role_takes_the_step(declared, actor, status, code):
    with pytest.raises(declared.StepRefused) as err:
        declared.take_step("decl-wft-ace-profile-delete", surface="ace_api.api_profiles_delete",
                           decision="delete", actor=actor)
    assert (err.value.status, err.value.code) == (status, code)


def test_a_required_reason_and_a_known_decision(declared):
    with pytest.raises(declared.StepRefused) as err:
        declared.take_step("decl-wft-cortex-finding-adjudicate",
                           surface="dic.api_explorer_cortex_finding_adjudicate",
                           decision="accept", actor=PM)
    assert err.value.code == "reason"
    with pytest.raises(declared.StepRefused) as err:
        declared.take_step("decl-wft-ace-run-launch", surface="ace_api.api_launch",
                           decision="force", actor=PM)
    assert err.value.code == "decision"


def test_a_step_that_cannot_be_recorded_is_refused(tmp_path, monkeypatch):
    """Fail closed: no wf tables, so no step, and the caller must not run the effect."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "empty.db"))
    from tools.workflow_hitl import declared

    with pytest.raises(declared.StepRefused) as err:
        declared.take_step("decl-wft-ace-run-launch", surface="ace_api.api_launch",
                           decision="launch", actor=PM)
    assert err.value.status == 503


# ── propose / decide / spend ─────────────────────────────────────────────────


def test_the_proposer_may_not_decide_and_a_step_is_decided_once(declared):
    prop = declared.propose("decl-wft-cortex-award", surface="cortex.api_v1_award",
                            subject="opp-1", payload={"opportunity_id": "opp-1"}, actor=PM)
    assert prop["status"] == "pending" and prop["decide_at"] == "/decisions"
    with pytest.raises(declared.StepRefused) as err:
        declared.decide(prop["step_id"], decision="accept", actor=PM)
    assert err.value.code == "separation"
    with pytest.raises(declared.StepRefused) as err:
        declared.decide(prop["step_id"], decision="reject", actor=CO)
    assert err.value.code == "reason"
    assert declared.decide(prop["step_id"], decision="accept", actor=CO)["decided_by"] == "u-co"
    with pytest.raises(declared.StepRefused) as err:
        declared.decide(prop["step_id"], decision="accept", actor={"id": "u-co2", "role": "co"})
    assert err.value.code == "already_decided"
    assert [s["step_id"] for s in declared.list_steps("decided")] == [prop["step_id"]]
    assert declared.list_steps("pending") == []


def test_a_decided_step_is_spent_once_for_its_exact_payload(declared):
    payload = {"goal": "triage the CVEs", "mode": "auto"}
    prop = declared.propose("decl-wft-cortex-agent-launch", surface="cortex.api_v1_agent",
                            subject="g", payload=payload, actor=SERVICE)
    kwargs = {"template_id": "decl-wft-cortex-agent-launch", "surface": "cortex.api_v1_agent"}
    with pytest.raises(declared.StepRefused) as err:
        declared.spend(prop["step_id"], payload=payload, **kwargs)
    assert err.value.code == "not_decided"
    declared.decide(prop["step_id"], decision="launch", actor=DEV)
    with pytest.raises(declared.StepRefused) as err:
        declared.spend(prop["step_id"], payload={**payload, "goal": "other"}, **kwargs)
    assert err.value.code == "payload"
    assert declared.spend(prop["step_id"], payload=payload, **kwargs)["status"] == "spent"
    with pytest.raises(declared.StepRefused) as err:
        declared.spend(prop["step_id"], payload=payload, **kwargs)
    assert err.value.code == "spent"


def test_a_declined_step_cannot_be_spent(declared):
    prop = declared.propose("decl-wft-cortex-agent-launch", surface="cortex.api_v1_agent",
                            payload={"goal": "g"}, actor=SERVICE)
    declared.decide(prop["step_id"], decision="decline", reason="not now", actor=DEV)
    with pytest.raises(declared.StepRefused) as err:
        declared.spend(prop["step_id"], template_id="decl-wft-cortex-agent-launch",
                       surface="cortex.api_v1_agent", payload={"goal": "g"})
    assert err.value.code == "denied"


def test_the_ledger_and_adjudications_are_append_only(declared, wf_db):
    declared.take_step("decl-wft-ace-run-launch", surface="ace_api.api_launch",
                       decision="launch", actor=PM)
    conn = sqlite3.connect(str(wf_db))
    try:
        for sql in ("UPDATE wf_decision_steps SET decision='x'", "DELETE FROM wf_decision_steps"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)
    finally:
        conn.close()


def test_the_migration_creates_the_same_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "mig.db"))
    spec = importlib.util.spec_from_file_location(
        "mig_cnf02", "tools/db/migrations/20261114162849_wf_declared_decisions/up.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        assert mod.up(conn)["status"] == "applied"
        assert mod.up(conn)["status"] == "applied"  # converges
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')").fetchall()}
    finally:
        conn.close()
    assert {"wf_decision_steps", "cortex_finding_adjudications",
            "wf_decision_steps_no_update", "cortex_finding_adjudications_no_delete"} <= names


# ── ACE routes ───────────────────────────────────────────────────────────────


def _app(*blueprints, user=None, binding=None):
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(user) if user else None
        g.security_context = {"tenant_id": "default", "user_id": (user or {}).get("id", ""),
                              "classification": "CUI"}
        if binding is not None:
            g.cortex_binding = binding

    for bp in blueprints:
        if bp.name not in app.blueprints:
            app.register_blueprint(bp)
    return app.test_client()


@pytest.fixture()
def ace_env(wf_db, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(tmp_path / "ace.db"))
    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    bp_mod._state["db_ready"] = True
    launches = []

    class _Ctrl:
        def launch(self, **kw):
            launches.append(kw)
            return f"ace-{len(launches)}"

        def abort(self, instance_id):
            launches.append({"aborted": instance_id})

    monkeypatch.setattr(bp_mod.ACEController, "get_instance", classmethod(lambda cls: _Ctrl()))
    return bp_mod, launches


def test_ace_launch_runs_the_persons_step_first(ace_env, wf_db):
    bp_mod, launches = ace_env
    resp = _app(bp_mod.ace_api_bp, user=DEV).post("/api/ace/launch", json={"problem_text": "x"})
    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert resp.get_json()["decision_step"]["template_id"] == "decl-wft-ace-run-launch"
    assert len(launches) == 1


def test_ace_launch_without_a_person_launches_nothing(ace_env):
    bp_mod, launches = ace_env
    resp = _app(bp_mod.ace_api_bp).post("/api/ace/launch", json={"problem_text": "x"})
    assert resp.status_code == 401 and resp.get_json()["workflow_refused"] is True
    assert launches == []


def test_ace_profile_delete_is_held_by_admin_or_pm(ace_env, monkeypatch):
    bp_mod, _ = ace_env
    deleted = []
    import icdev.tools.ace.profile_generator as pg

    monkeypatch.setattr(pg, "delete_profile", lambda rid: deleted.append(rid) or {"deleted": rid})
    assert _app(bp_mod.ace_api_bp, user=DEV).delete("/api/ace/profiles/r1").status_code == 403
    assert deleted == []
    resp = _app(bp_mod.ace_api_bp, user=PM).delete("/api/ace/profiles/r1")
    assert resp.status_code == 200 and deleted == ["r1"]
    assert resp.get_json()["decision_step"]["decided_by"] == "u-pm"


def test_ace_hitl_resolve_records_the_step_then_resolves(ace_env, wf_db, monkeypatch):
    bp_mod, _ = ace_env
    calls = []
    import icdev.tools.ace.coworker_thread as ct

    monkeypatch.setattr(ct.HITLGate, "reject", staticmethod(lambda *a: calls.append(a)))
    resp = _app(bp_mod.ace_api_bp, user=DEV).post(
        "/api/ace/i1/hitl", json={"coworker_id": "c1", "detail": "d", "approved": False,
                                  "reason": "no"})
    assert resp.status_code == 200 and calls == [("c1", "d", "i1")]
    step = resp.get_json()["decision_step"]
    assert step["decision"] == "reject"
    assert _rows(wf_db, "SELECT reason FROM wf_decision_steps WHERE step_id=?",
                 (step["step_id"],)) == [("no",)]


def test_ace_suggestion_confirm_and_decline_are_steps(ace_env, monkeypatch):
    bp_mod, _ = ace_env
    from icdev.tools.ace import chat_trigger

    state = {"s1": "proposed", "s2": "proposed"}
    monkeypatch.setattr(chat_trigger, "get_proposal",
                        lambda sid: {"id": sid, "state": state[sid]} if sid in state else None)
    monkeypatch.setattr(chat_trigger, "confirm_proposal",
                        lambda sid, user_id: {"ok": True, "instance_id": f"ace-{user_id}",
                                              "error": ""})
    monkeypatch.setattr(chat_trigger, "decline_proposal", lambda sid, reason: True)
    monkeypatch.setattr(bp_mod, "_sme_tier_allows", lambda: (True, "professional"))
    client = _app(bp_mod.ace_api_bp, user=DEV)
    resp = client.post("/api/ace/suggestions/s1/confirm")
    assert resp.status_code == 200 and resp.get_json()["instance_id"] == "ace-u-dev"
    assert resp.get_json()["decision_step"]["decision"] == "confirm"
    resp = client.post("/api/ace/suggestions/s2/decline", json={"reason": "later"})
    assert resp.status_code == 200 and resp.get_json()["decision_step"]["decision"] == "decline"
    state["s1"] = "launched"
    resp = client.post("/api/ace/suggestions/s1/confirm")
    assert resp.status_code == 200 and resp.get_json()["decision_step"] is None
    assert client.post("/api/ace/suggestions/nope/confirm").status_code == 404


def test_ace_abort_stays_an_immediate_stop(ace_env, wf_db):
    bp_mod, launches = ace_env
    resp = _app(bp_mod.ace_api_bp).post("/api/ace/i9/abort")
    assert resp.status_code == 200 and launches == [{"aborted": "i9"}]
    assert _rows(wf_db, "SELECT COUNT(*) FROM wf_decision_steps") == [(0,)]


# ── Cortex routes ────────────────────────────────────────────────────────────


def _cortex_client(user=None, binding=None):
    from tools.cortex.blueprint import cortex_bp

    return _app(cortex_bp, user=user, binding=binding)


def _svc_binding(*scopes):
    from tools.cortex.schemas import CortexContext

    return {"ctx": CortexContext(tenant_id="compass", classification="CUI"),
            "scopes": list(scopes), "label": "compass", "tenant_id": "compass",
            "classification_ceiling": "CUI"}


@pytest.fixture()
def agent_calls(wf_db, monkeypatch):
    calls = []
    import tools.cortex.rest_v1 as rest

    class _Result:
        def to_dict(self):
            return {"text": "launched", "data": {}}

    monkeypatch.setattr(rest, "agent", lambda goal, ctx=None, **kw: calls.append(goal) or _Result())
    return calls


def test_rest_agent_from_a_service_key_is_a_proposal_then_a_spent_step(agent_calls):
    from tools.workflow_hitl import declared

    svc = _cortex_client(user=SERVICE, binding=_svc_binding("cortex:agent"))
    body = {"goal": "triage the CVEs", "mode": "single"}
    out = svc.post("/cortex/api/v1/agent", json=body).get_json()
    assert out["launched"] is False and out["pending"] is True and agent_calls == []
    step_id = out["decision_step"]["step_id"]
    again = svc.post("/cortex/api/v1/agent", json={**body, "decision_step": step_id})
    assert again.status_code == 409 and agent_calls == []
    declared.decide(step_id, decision="launch", actor=DEV)
    launched = svc.post("/cortex/api/v1/agent", json={**body, "decision_step": step_id})
    assert launched.get_json()["launched"] is True and agent_calls == ["triage the CVEs"]
    assert svc.post("/cortex/api/v1/agent",
                    json={**body, "decision_step": step_id}).status_code == 409


def test_rest_agent_from_a_holder_launches_in_one_request(agent_calls):
    out = _cortex_client(user=PM).post("/cortex/api/v1/agent",
                                       json={"goal": "triage", "mode": "single"}).get_json()
    assert out["launched"] is True and out["decision_step"]["status"] == "decided"
    assert agent_calls == ["triage"]


def test_chat_confirm_is_the_persons_step(wf_db, monkeypatch):
    import tools.cortex.api as cortex_api
    import tools.cortex.blueprint as bp

    launched = []

    class _Result:
        text = "Agent launched."
        data = {"instance_id": "ace-7"}
        governance = None

    monkeypatch.setattr(bp, "_propose_roles", lambda q: [])
    monkeypatch.setattr(cortex_api, "agent", lambda q, **kw: launched.append(q) or _Result())
    app = Flask(__name__)
    with app.test_request_context("/"):
        g.current_user = DEV
        out = bp._launch_confirmed_agent("do the thing", ctx=None)
    assert out["instance_id"] == "ace-7" and out["decision_step"]["decided_by"] == "u-dev"
    with app.test_request_context("/"):
        g.current_user = {"id": "u-aud", "role": "auditor"}
        refused = bp._launch_confirmed_agent("do the thing", ctx=None)
    assert refused["degraded"] is True and "not launched" in refused["answer"]
    assert launched == ["do the thing"]


def test_award_is_a_proposal_a_different_person_accepts(wf_db, monkeypatch):
    import tools.govcon.portfolio_manager as pm_mod
    from tools.workflow_hitl import declared

    monkeypatch.setattr(pm_mod, "transition_from_opportunity",
                        lambda oid, created_by="": {"status": "ok", "contract_id": "c-1"})
    out = _cortex_client(user=PM).post("/cortex/api/v1/award",
                                       json={"opportunity_id": "opp-9"}).get_json()
    step = out["acceptance"]
    assert out["contract_id"] == "c-1" and step["status"] == "pending"
    with pytest.raises(declared.StepRefused):
        declared.decide(step["step_id"], decision="accept", actor=PM)
    assert declared.decide(step["step_id"], decision="accept", actor=CO)["affirmative"] is True


def test_a_failed_award_withdraws_its_proposal(wf_db, monkeypatch):
    import tools.govcon.portfolio_manager as pm_mod
    from tools.workflow_hitl import declared

    monkeypatch.setattr(pm_mod, "transition_from_opportunity",
                        lambda oid, created_by="": {"status": "error", "message": "not won"})
    resp = _cortex_client(user=PM).post("/cortex/api/v1/award", json={"opportunity_id": "o"})
    assert resp.status_code == 400
    assert declared.list_steps("pending") == []
    assert [s["status"] for s in declared.list_steps()] == ["withdrawn"]


def _bom_doc(**extra):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for row in (["Item", "Part Number", "QTY", "Unit Price", "Extended"],
                ["Core switch", "CS-9500-16X", 2, 21000, 42000]):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return {"filename": "bom.xlsx", "content_base64": base64.b64encode(buf.getvalue()).decode(),
            **extra}


def test_bom_designations_bind_only_through_a_persons_step(wf_db):
    from tools.workflow_hitl import declared

    body = {"documents": [_bom_doc(role="bom_claim", credibility_tier="authoritative")]}
    svc = _cortex_client(user=SERVICE, binding=_svc_binding("cortex:bom"))
    out = svc.post("/cortex/api/v1/bom", json=body).get_json()
    assert out["designation_step"]["status"] == "pending"
    assert out["sources"][0]["set_by"] == "ai_proposed"
    assert out["proposed_designations"] == {"bom.xlsx": {"role": "bom_claim",
                                                         "credibility_tier": "authoritative"}}
    declared.decide(out["designation_step"]["step_id"], decision="designate", actor=CO)
    bound = svc.post("/cortex/api/v1/bom", json={
        **body, "decision_step": out["designation_step"]["step_id"]}).get_json()
    assert bound["sources"][0]["set_by"] == "human"
    assert bound["sources"][0]["credibility_tier"] == "authoritative"
    held = _cortex_client(user=CO).post("/cortex/api/v1/bom", json=body).get_json()
    assert held["designation_step"]["status"] == "decided"
    assert held["sources"][0]["set_by"] == "human"


# ── Cortex finding adjudication ──────────────────────────────────────────────


def _seed_finding(fid="cef-abc", tenant="default"):
    from tools.cortex import finding_store
    from tools.db.storage import get_connection

    row = finding_store.conflict_row(
        {"entity_key": "e1", "entity_label": "Widget", "kind": "status",
         "values": ["active", "retired"], "sides": []}, tenant, "CUI", "Widget", "unknown")
    row["finding_id"] = fid
    conn = get_connection()
    try:
        finding_store._upsert(conn, row)
        conn.commit()
    finally:
        conn.close()
    return fid


def _dic_client(user):
    from tools.document_intelligence.blueprint import dic_bp

    return _app(dic_bp, user=user)


def test_a_finding_goes_open_to_adjudicated_once(wf_db):
    from tools.cortex import finding_store

    fid = _seed_finding()
    assert finding_store.list_findings("default")[0]["state"] == "open"
    url = f"/document-intelligence/api/explorer/cortex-findings/{fid}/adjudicate"
    assert _dic_client(PM).post(url, json={"decision": "accept"}).status_code == 400
    assert _dic_client(DEV).post(url, json={"decision": "accept",
                                            "reason": "r"}).status_code == 403
    resp = _dic_client(PM).post(url, json={"decision": "accept", "reason": "two registries"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["state"] == "adjudicated" and body["adjudication"]["adjudicated_by"] == "u-pm"
    assert body["adjudication"]["step_id"] == body["decision_step"]["step_id"]
    listed = finding_store.list_findings("default")[0]
    assert listed["state"] == "adjudicated" and listed["adjudication"]["decision"] == "accept"
    again = _dic_client(PM).post(url, json={"decision": "reject", "reason": "x"})
    assert again.status_code == 409
    conn = sqlite3.connect(str(wf_db))
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE cortex_finding_adjudications SET decision='reject'")
    finally:
        conn.close()
    assert _dic_client(PM).post(
        "/document-intelligence/api/explorer/cortex-findings/nope/adjudicate",
        json={"decision": "accept", "reason": "x"}).status_code == 404


# ── /decisions ───────────────────────────────────────────────────────────────


def test_the_decisions_page_lists_and_decides(wf_db):
    from tools.workflow_hitl import declared
    from tools.workflow_hitl.decisions_blueprint import decisions_bp

    prop = declared.propose("decl-wft-cortex-award", surface="cortex.api_v1_award",
                            subject="opp-2", payload={"opportunity_id": "opp-2"}, actor=PM)
    as_pm = _app(decisions_bp, user=PM)
    steps = as_pm.get("/decisions/api/steps?status=pending").get_json()["steps"]
    assert [s["step_id"] for s in steps] == [prop["step_id"]]
    assert steps[0]["can_decide"] is False  # the proposer
    as_co = _app(decisions_bp, user=CO)
    assert as_co.get("/decisions/api/steps?status=pending").get_json()["steps"][0]["can_decide"]
    resp = as_co.post(f"/decisions/api/steps/{prop['step_id']}/decide",
                      json={"decision": "accept"})
    assert resp.status_code == 200 and resp.get_json()["decided_by"] == "u-co"
    assert as_pm.post(f"/decisions/api/steps/{prop['step_id']}/decide",
                      json={"decision": "accept"}).status_code == 409
    templates = as_pm.get("/decisions/api/templates").get_json()["templates"]
    assert len(templates) == 8


def test_the_mcp_tool_reads_and_never_decides(wf_db):
    from tools.mcp.gap_handlers import handle_workflow_decision_steps
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, tool_authorization

    out = handle_workflow_decision_steps({"status": "pending"})
    assert out["steps"] == [] and len(out["templates"]) == 8
    assert READ_ONLY_DECLARATIONS["workflow_decision_steps"] is True
    assert tool_authorization("workflow_decision_steps")["tier"] == "read"


# ── Coherence ────────────────────────────────────────────────────────────────


def test_hitl_workflow_maps_every_decision_endpoint_to_a_template():
    from tools.workflow.coherence_checker import check_hitl_workflow

    result = check_hitl_workflow()
    assert result.status == "pass", result.missing
    assert "every audited decision endpoint maps to a declared template" in result.actual
