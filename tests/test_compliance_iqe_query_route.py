# CUI // SP-CTI
"""rmf-ui-11: the Compliance Hub's own IQE endpoint answers, instead of 500ing on import.

``POST /api/compliance/iqe-query`` is what the hub's IQE widget posts to
(``iqe_api_route`` in boundary_canvas/compliance_hub.html). Since the handler
was written (2026-06-28) it did ``from tools.iqe.parser import Parser`` -- a
name tools/iqe/parser.py has never exported (its public entry point is
``parse()``; the class is ``_Parser``). The import sits OUTSIDE the handler's
``try``, so every request raised ImportError before touching the question and
Flask answered 500. Found while browser-verifying the migrated hub: the page
rendered clean, and the first question typed into its widget returned
"Internal Server Error".

This test INVOKES the route (a symbol-existence check would have passed the
old code too -- ``Parser`` was importable from nowhere, but nothing asked).
The NL translation and the executor are stubbed so the test needs no LLM and
no board; the IQE string is parsed for real, which is the call that broke.
RED on the merge base (500), GREEN here.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def client(monkeypatch):
    from flask import Flask

    import tools.dashboard.api.compliance as compliance_mod
    import tools.iqe.executor as executor_mod
    import tools.iqe.nl_to_iqe as nl_mod

    monkeypatch.setattr(
        nl_mod,
        "nl_to_iqe",
        lambda question, collections: {
            "iqe": 'foreach v in compliance.violations where v.status == "open" select v.control_id, v.severity',
            "explanation": "stubbed translation",
        },
    )
    monkeypatch.setattr(
        executor_mod,
        "execute_query",
        lambda ast, conn: [{"control_id": "AC-2", "severity": "high"}],
    )

    class _Conn:
        closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(compliance_mod, "_get_db", lambda: _Conn())

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(compliance_mod.compliance_api)
    return app.test_client()


def test_iqe_query_answers_with_rows_for_a_question(client):
    resp = client.post("/api/compliance/iqe-query", json={"question": "open violations", "execute": True})
    assert resp.status_code == 200, resp.data[:400]
    body = resp.get_json()
    assert body["ok"] is True
    assert body["iqe"].startswith("foreach v in compliance.violations")
    assert body["row_count"] == 1
    assert body["results"] == [{"control_id": "AC-2", "severity": "high"}]


def test_iqe_query_refuses_an_empty_question(client):
    resp = client.post("/api/compliance/iqe-query", json={"question": "   "})
    assert resp.status_code == 400
    assert "question is required" in resp.get_json()["error"]


def test_a_malformed_translation_is_reported_not_a_500(client, monkeypatch):
    """A syntax error in the translated IQE is the handler's own error path, with the string attached."""
    import tools.iqe.nl_to_iqe as nl_mod

    monkeypatch.setattr(
        nl_mod, "nl_to_iqe", lambda q, c: {"iqe": "this is not iqe", "explanation": ""}
    )
    resp = client.post("/api/compliance/iqe-query", json={"question": "anything"})
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["iqe"] == "this is not iqe"
    assert "error" in body
