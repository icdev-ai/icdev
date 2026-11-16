# CUI // SP-CTI
"""Turning ACE auto-dispatch on is a person's declared step; turning it off is a stop (icx-ace-01).

* A grant is refused (and leaves no decision behind) for a topic the role does
  not declare auto-eligible, a role below trusted, no reason, an expiry past
  ``max_grant_days`` or in the past, a ``max_per_day`` out of bounds, a service
  key, or a person who does not hold ``decl-wft-ace-auto-dispatch``.
* A holder's grant is a decided step of that template, bound to the payload.
* Revoke and stop-all take effect at once with a stated reason and no step;
  resume needs the holder's step.
* The Co-Worker page carries the Event feed and Auto-dispatch panels and calls
  the routes; the role loader refuses an eligible topic the role does not hear.
"""
from __future__ import annotations

import importlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.ace._dispatch_support import DEV, PM, SERVICE, TOPIC, VIEWER, isolated_client, make_role

ROOT = Path(__file__).resolve().parents[2]
PANELS = ROOT / "tools" / "dashboard" / "templates" / "coworker" / "_dispatch_panels.html"


def _bp():
    return importlib.import_module("icdev.tools.ace.blueprint")


def _ad():
    return importlib.import_module("icdev.tools.ace.auto_dispatch")


def _ledger_rows(template_id="decl-wft-ace-auto-dispatch"):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT step_id, event, decision, actor FROM wf_decision_steps WHERE template_id = %s",
            (template_id,)).fetchall()]
    finally:
        conn.close()


def _body(**over):
    when = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
    body = {"role_id": "evt_auto", "topic": TOPIC, "reason": "routine and reviewed",
            "expires_at": when, "max_per_day": 4}
    body.update(over)
    return body


def _grant(user, **over):
    return isolated_client(user, _bp().ace_api_bp).post("/api/ace/auto-dispatch/grant",
                                                        json=_body(**over))


@pytest.mark.parametrize("over, status, code", [
    ({"role_id": "evt_plain"}, 403, "not_eligible"),
    ({"topic": "document.analyzed"}, 403, "not_eligible"),
    ({"reason": "  "}, 400, "reason"),
    ({"expires_at": ""}, 400, "expiry"),
    ({"expires_at": "2020-01-01"}, 400, "expiry"),
    ({"expires_at": (datetime.now(timezone.utc) + timedelta(days=45)).date().isoformat()},
     400, "expiry"),
    ({"max_per_day": 0}, 400, "max_per_day"),
    ({"max_per_day": 500}, 400, "max_per_day"),
])
def test_a_grant_is_refused_and_leaves_no_decision(ace_env, over, status, code):
    resp = _grant(PM, **over)
    assert resp.status_code == status, resp.get_data(as_text=True)
    assert resp.get_json()["code"] == code
    assert _ledger_rows() == []
    assert _ad().grants() == []


def test_a_grant_is_refused_below_trusted(ace_env):
    ace_env.trust["evt_auto"] = 0.5  # supervised
    resp = _grant(PM)
    assert resp.status_code == 403 and resp.get_json()["code"] == "trust"
    assert _ledger_rows() == []


@pytest.mark.parametrize("user, status, code", [(SERVICE, 403, "not_a_person"),
                                                (VIEWER, 403, "role"), (None, 401, "no_person")])
def test_only_a_holder_may_grant(ace_env, user, status, code):
    resp = _grant(user)
    assert resp.status_code == status
    assert resp.get_json()["workflow_refused"] is True and resp.get_json()["code"] == code
    assert _ad().grants() == []


def test_a_holders_grant_is_a_decided_step_bound_to_its_payload(ace_env):
    resp = _grant(PM)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    out = resp.get_json()
    grant, step = out["grant"], out["decision_step"]
    assert step["template_id"] == "decl-wft-ace-auto-dispatch" and step["decided_by"] == "u-pm"
    assert (grant["role_id"], grant["topic"], grant["max_per_day"]) == ("evt_auto", TOPIC, 4)
    assert grant["expires_at"].endswith("23:59:59")
    rows = _ledger_rows()
    assert [(r[1], r[2], r[3]) for r in rows] == [("decided", "grant", "u-pm")]
    from icdev.tools.workflow_hitl import declared

    assert declared._events(step["step_id"])["decided"]["payload_digest"] == \
        declared.payload_digest({k: grant[k] for k in ("role_id", "topic", "expires_at",
                                                       "max_per_day", "reason")})
    listed = _ad().grants()
    assert [(g["grant_id"], g["state"], g["granted_by"]) for g in listed] == [
        (grant["grant_id"], "live", "u-pm")]
    # One live grant per role/topic.
    again = _grant(PM)
    assert again.status_code == 409 and again.get_json()["code"] == "already_granted"


def test_revoke_is_immediate_with_a_reason_and_no_step(ace_env):
    grant = _grant(PM).get_json()["grant"]
    before = _ledger_rows()
    client = isolated_client(DEV, _bp().ace_api_bp)
    url = f"/api/ace/auto-dispatch/{grant['grant_id']}/revoke"
    assert client.post(url, json={}).status_code == 400  # a stated reason
    assert isolated_client(VIEWER, _bp().ace_api_bp).post(
        url, json={"reason": "x"}).status_code == 403
    resp = client.post(url, json={"reason": "too noisy"})
    assert resp.status_code == 200 and resp.get_json()["state"] == "revoked"
    assert _ledger_rows() == before  # a stop takes no step
    g = _ad().grants()[0]
    assert (g["state"], g["closed_by"], g["closed_reason"]) == ("revoked", "u-dev", "too noisy")
    assert client.post(url, json={"reason": "again"}).status_code == 409


def test_stop_all_is_immediate_and_resume_needs_the_step(ace_env):
    api = _bp().ace_api_bp
    viewer = isolated_client(VIEWER, api)
    assert viewer.post("/api/ace/auto-dispatch/stop-all", json={}).status_code == 400
    stopped = viewer.post("/api/ace/auto-dispatch/stop-all", json={"reason": "incident 42"})
    assert stopped.status_code == 200 and stopped.get_json()["stop_all"]["stopped"] is True
    assert _ledger_rows() == []
    assert isolated_client(None, api).post("/api/ace/auto-dispatch/stop-all",
                                           json={"reason": "x"}).status_code == 401

    refused = viewer.post("/api/ace/auto-dispatch/resume-all", json={"reason": "over"})
    assert refused.status_code == 403 and refused.get_json()["workflow_refused"] is True
    pm = isolated_client(PM, api)
    assert pm.post("/api/ace/auto-dispatch/resume-all", json={}).status_code == 400
    ok = pm.post("/api/ace/auto-dispatch/resume-all", json={"reason": "incident closed"})
    assert ok.status_code == 200, ok.get_data(as_text=True)
    assert ok.get_json()["stop_all"]["stopped"] is False
    assert [(r[1], r[2]) for r in _ledger_rows()] == [("decided", "resume")]
    assert pm.post("/api/ace/auto-dispatch/resume-all",
                   json={"reason": "twice"}).get_json()["code"] == "not_stopped"


def test_the_state_route_reports_eligibility_grants_and_the_switch(ace_env):
    _grant(PM)
    resp = isolated_client(PM, _bp().ace_api_bp).get("/api/ace/auto-dispatch")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["can_grant"] is True
    assert [(e["role_id"], e["topic"], e["trust_band"], e["trust_ok"]) for e in body["eligible"]] \
        == [("evt_auto", TOPIC, "trusted", True)]
    assert body["grants"][0]["launched_today"] == 0
    assert body["limits"] == {"min_trust_band": "trusted", "max_grant_days": 30,
                              "max_per_day_ceiling": 50}
    as_viewer = isolated_client(VIEWER, _bp().ace_api_bp).get("/api/ace/auto-dispatch")
    assert as_viewer.get_json()["can_grant"] is False


def test_the_role_loader_refuses_an_eligible_topic_the_role_does_not_hear():
    with pytest.raises(ValueError, match="does not listen to"):
        make_role("r_bad", listen=("doc.ready",), auto=("task.completed",))
    with pytest.raises(ValueError, match="needs a reason"):
        make_role("r_bad", auto=(TOPIC,), reason="")
    assert make_role("r_none").auto_dispatch == {}


def test_the_shipped_roles_load_with_their_auto_dispatch_blocks():
    from icdev.tools.ace.role_loader import RoleLoader

    roles = RoleLoader().list_roles()
    assert roles
    for role in roles:
        topics = (role.auto_dispatch or {}).get("eligible_topics") or []
        assert set(topics) <= set(role.communication.get("listen_topics") or [])


def test_the_coworker_page_carries_both_panels_and_calls_the_routes(ace_env, monkeypatch):
    bp = _bp()
    monkeypatch.setitem(bp._state, "db_ready", True)
    body = isolated_client(PM, bp.ace_bp).get("/coworker/").get_data(as_text=True)
    assert 'id="ace-event-feed"' in body and 'id="ace-auto-dispatch"' in body
    assert "Decisions" in body and "decl-wft-ace-auto-dispatch" in body

    text = PANELS.read_text(encoding="utf-8")
    # By endpoint id (icx-cnf-08): routes.gen.js fills each rule.
    for call in ("fetch(icdevRoute('ace_api.api_ace_events', {}, {limit: 50}))",
                 "fetch(icdevRoute('ace_api.api_auto_dispatch'))",
                 "post(icdevRoute('ace_api.api_auto_dispatch_grant')",
                 "post(icdevRoute('ace_api.api_auto_dispatch_stop_all')",
                 "post(icdevRoute('ace_api.api_auto_dispatch_resume_all')",
                 "post(icdevRoute('ace_api.api_auto_dispatch_revoke', {grant_id: grantId})"):
        assert call in text, call
    # An auto-launched run can be aborted from the feed at once.
    assert "fetch(icdevRoute('ace_api.api_abort', {instance_id: instanceId})" in text
    # Data is written with textContent, never as HTML.
    assert "innerHTML" not in text
