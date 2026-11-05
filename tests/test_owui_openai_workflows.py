#!/usr/bin/env python3
# CUI // SP-CTI
"""Governed workflows behind /v1 model ids (owui-flow-01).

``icdev/intake`` must STAGE requirements for human review and never write the
board; ``icdev/kanban`` must only read it, and only for roles the model
declares. The intake hook and the database are stubbed; the /v1 wiring is
exercised through the blueprint with a stub principal.
"""

import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask, g, request  # noqa: E402

from tools.saas import openai_compat, openai_workflows as wf  # noqa: E402

USER = [{"role": "user", "content": "The system shall support SSO via SAML 2.0"}]


class Rows:
    def __init__(self, rows, sink):
        self.rows, self.sink = rows, sink

    def execute(self, sql, params=()):
        self.sink.append((sql, params))
        return self

    def fetchall(self):
        return self.rows

    def close(self):
        pass


# ---------------------------------------------------------------------------
# intake
# ---------------------------------------------------------------------------
def test_intake_stages_for_review_and_links_to_it(monkeypatch):
    monkeypatch.setenv("ICDEV_DASHBOARD_URL", "http://icdev.test")
    seen = []

    def fake(context_id, text):
        seen.append((context_id, text))
        return {"session_id": "s1", "requirements_found": 2, "hitl_instance_id": "h1",
                "tasks_staged": 2, "review_url": "/workflow/?instance=h1"}

    out = wf.run_intake(USER, context_id="owui-x", process=fake)
    assert seen == [("owui-x", USER[0]["content"])]
    assert "http://icdev.test/workflow/?instance=h1" in out["text"]
    assert "until a human approves" in out["text"]
    assert out["details"]["hitl_instance_id"] == "h1"


def test_intake_without_a_requirement_says_so():
    out = wf.run_intake([{"role": "user", "content": "hi"}], context_id="c",
                        process=lambda c, t: {"skipped": True})
    assert out["details"] == {"skipped": True}


def test_intake_failure_is_a_workflow_error():
    with pytest.raises(wf.WorkflowError):
        wf.run_intake(USER, context_id="c", process=lambda c, t: {"error": "db down"})


def test_conversation_key_is_stable_across_turns_and_per_user():
    turn1 = USER
    turn2 = USER + [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "and MFA"}]
    assert wf.conversation_key("t", "u", turn1) == wf.conversation_key("t", "u", turn2)
    assert wf.conversation_key("t", "u", turn1) != wf.conversation_key("t", "other", turn1)
    assert wf.conversation_key("t", "u", turn1, "chat-9") != wf.conversation_key("t", "u", turn1)


# ---------------------------------------------------------------------------
# kanban
# ---------------------------------------------------------------------------
def test_kanban_reads_counts_for_the_named_project_prefix():
    sink = []
    out = wf.run_kanban([{"role": "user", "content": "status of owui- please"}],
                        get_conn=lambda: Rows([{"status": "done", "n": 11}, {"status": "backlog", "n": 5}], sink),
                        prefixes={"owui-": "Open WebUI", "ow-": "Other"})
    (sql, params), = sink
    assert sql.lstrip().upper().startswith("SELECT") and params == ("owui-%",)
    assert out["details"] == {"prefix": "owui-", "counts": {"done": 11, "backlog": 5}, "total": 16}
    assert "Open WebUI" in out["text"]


def test_kanban_without_a_prefix_reads_the_whole_board():
    sink = []
    out = wf.run_kanban([{"role": "user", "content": "board status"}],
                        get_conn=lambda: Rows([{"status": "done", "n": 3}], sink), prefixes={"owui-": "x"})
    assert sink[0][1] == () and out["details"]["prefix"] is None


# ---------------------------------------------------------------------------
# /v1 wiring
# ---------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    import importlib

    monkeypatch.setattr(importlib.import_module("tools.audit.audit_logger"), "log_event", lambda **kw: 1)
    app = Flask(__name__)

    @app.before_request
    def _p():
        g.tenant_id, g.user_id = "tenant-1", "user-1"
        g.user_role = request.headers.get("X-Test-Role", "tenant_admin")

    app.register_blueprint(openai_compat.openai_bp)
    return app.test_client()


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake_run(workflow, messages, **kw):
        calls.append(workflow)
        return {"text": f"{workflow} ok", "details": {}}

    monkeypatch.setattr(wf, "run", fake_run)
    monkeypatch.setattr(openai_compat, "_router", lambda: type("R", (), {"_config": {"routing": {}}})())
    return calls


def _post(client, model, role="tenant_admin", **extra):
    return client.post("/v1/chat/completions", headers={"X-Test-Role": role},
                       json={"model": model, "messages": USER, **extra})


def test_workflow_models_are_listed(client, ran):
    ids = [m["id"] for m in client.get("/v1/models").get_json()["data"]]
    assert {"icdev/intake", "icdev/kanban"} <= set(ids)


def test_workflow_model_answers_as_a_chat_completion(client, ran):
    body = _post(client, "icdev/intake").get_json()
    assert body["choices"][0]["message"]["content"] == "intake ok"
    assert ran == ["intake"]


def test_workflow_model_streams(client, ran):
    frames = [f for f in _post(client, "icdev/kanban", stream=True).get_data(as_text=True).split("\n\n") if f]
    assert frames[-1] == "data: [DONE]"
    assert any("kanban ok" in f for f in frames)


def test_role_not_declared_for_the_workflow_is_refused(client, ran):
    resp = _post(client, "icdev/kanban", role="viewer")
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "role_not_permitted"
    assert ran == []


def test_workflow_failure_is_a_clean_error(client, monkeypatch, ran):
    def boom(workflow, messages, **kw):
        raise wf.WorkflowError("intake failed: db down")

    monkeypatch.setattr(wf, "run", boom)
    resp = _post(client, "icdev/intake")
    assert resp.status_code == 502
    assert "db down" in resp.get_json()["error"]["message"]


def test_declared_workflows_all_exist():
    cfg = openai_compat.load_config()
    for m in cfg["models"]:
        if m.get("kind") == "workflow":
            assert m["workflow"] in wf.WORKFLOWS
            assert m.get("required_roles"), f"{m['id']} must declare required_roles"
