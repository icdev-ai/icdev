# CUI // SP-CTI
"""Cortex chat routing + session-reuse tests (ctx-canvas-02).

Covers:
  * intent_router.route() — search / ask / complete / agent classification.
  * POST /cortex/api/chat — a search-shaped, a data-question, and a generative
    message each reach the correct cortex.* facade (visible in metadata), with
    the cortex api functions monkeypatched so routing is asserted in isolation.
  * Ungrounded answers carry the visible grounded=False banner signal.
  * Turns persist to cortex_messages and reload via GET /api/session/<id>.
  * The multi-step-goal (agent) intent is gated behind a confirm affordance —
    never auto-executed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# intent_router unit tests
# ---------------------------------------------------------------------------
class TestIntentRouter:
    def test_search_shaped_routes_to_search(self):
        from tools.cortex import intent_router

        for msg in [
            "find documents about the migration plan",
            "search the knowledge base for zero trust guidance",
            "look up sources on FedRAMP boundary",
        ]:
            d = intent_router.route(msg)
            assert d["facade"] == "search", (msg, d)

    def test_data_question_routes_to_ask(self):
        from tools.cortex import intent_router

        for msg in [
            "how many active sessions are there?",
            "list all users in the compliance tenant",
            "what is the average result count grouped by domain?",
        ]:
            d = intent_router.route(msg)
            assert d["facade"] == "ask", (msg, d)

    def test_generative_routes_to_complete(self):
        from tools.cortex import intent_router

        for msg in [
            "write a summary of the Q3 report",
            "draft an email to the security team",
            "translate this paragraph into Spanish",
        ]:
            d = intent_router.route(msg)
            assert d["facade"] == "complete", (msg, d)

    def test_multi_step_goal_routes_to_agent_with_confirm(self):
        from tools.cortex import intent_router

        d = intent_router.route(
            "build a pipeline that ingests the CVE feed then generate a report and deploy it"
        )
        assert d["facade"] == "agent"
        assert d["requires_confirm"] is True

    def test_empty_message_defaults_to_ask(self):
        from tools.cortex import intent_router

        d = intent_router.route("   ")
        assert d["facade"] == "ask"

    def test_decision_shape(self):
        from tools.cortex import intent_router

        d = intent_router.route("find docs about X")
        for key in ("intent", "facade", "confidence", "reason", "requires_confirm"):
            assert key in d
        assert 0.0 <= d["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Blueprint /api/chat routing tests (cortex api fns monkeypatched)
# ---------------------------------------------------------------------------
@pytest.fixture
def client(icdev_db, monkeypatch):
    # Point the app's get_connection() at conftest's temp DB (icdev_db), which
    # carries dashboard_users + the 'test-admin' user the auth before_request
    # hook validates AND the cortex_* chat tables. NOTE: production/runtime is
    # PostgreSQL; SQLite is used here only because conftest.py hard-forces
    # ICDEV_STORAGE_BACKEND=sqlite for the whole platform test suite (CI Test
    # gate). get_connection()'s SQLite branch re-reads ICDEV_DB_PATH at call
    # time, so this redirect takes effect per request. Live-PG behaviour is
    # proven separately by db/verify_tenant_isolation.py.
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    # auth._get_db() passes get_connection(db_path=config.DB_PATH) explicitly,
    # which otherwise overrides ICDEV_DB_PATH and points the auth query at the
    # real data/icdev.db (no dashboard_users). Pin it to the same temp DB so
    # auth + cortex persistence share one SQLite file.
    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))

    from tools.cortex.blueprint import cortex_bp
    from tools.dashboard.app import app

    if "cortex" not in app.blueprints:
        app.register_blueprint(cortex_bp)
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield test_client


def _post(client, body):
    return client.post(
        "/cortex/api/chat", data=json.dumps(body), content_type="application/json"
    )


class _FakeResult:
    """Minimal CortexResult stand-in for monkeypatched facades."""

    def __init__(self, text, grounded, citations=None, gates=None, confidence="include"):
        self.text = text
        self.grounded = grounded
        self.citations = citations or []
        self.metadata = {"confidence": confidence}

        class _Gov:
            def __init__(self, gates):
                self.gates_run = gates or []
                self.outcomes = {g: "pass" for g in (gates or [])}
                self.blocked = False

            def to_dict(self):
                return {
                    "gates_run": self.gates_run,
                    "outcomes": self.outcomes,
                    "blocked": self.blocked,
                }

        self.governance = _Gov(gates)


class _FakeCitation:
    def __init__(self, source_id, title, snippet):
        self.source_id = source_id
        self.title = title
        self.snippet = snippet
        self.source_table = ""

    def to_dict(self):
        return {
            "source_id": self.source_id,
            "title": self.title,
            "snippet": self.snippet,
            "source_table": self.source_table,
        }


class _FakeSearchHit:
    def __init__(self, content, citation):
        self.content = content
        self.citation = citation


class TestChatRouting:
    def test_search_message_calls_cortex_search(self, client, monkeypatch):
        import tools.cortex.api as cortex_api

        called = {}

        def fake_search(question, ctx=None, **kw):
            called["search"] = question
            return [_FakeSearchHit("relevant passage", _FakeCitation("rag:1", "Doc A", "snippet"))]

        def boom(*a, **k):
            raise AssertionError("wrong facade invoked")

        monkeypatch.setattr(cortex_api, "search", fake_search)
        monkeypatch.setattr(cortex_api, "ask", boom)
        monkeypatch.setattr(cortex_api, "complete", boom)

        resp = _post(client, {"question": "find documents about the migration plan"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["mode"] == "search"
        assert data["intent"] == "search"
        assert "search" in called
        assert data["grounded"] is True
        assert data["citations"] and data["citations"][0]["source_id"] == "rag:1"

    def test_data_question_calls_cortex_ask(self, client, monkeypatch):
        import tools.cortex.api as cortex_api

        called = {}

        def fake_ask(question, ctx=None, **kw):
            called["ask"] = question
            return _FakeResult("42 rows.", True, gates=["iqe_execution"])

        def boom(*a, **k):
            raise AssertionError("wrong facade invoked")

        monkeypatch.setattr(cortex_api, "ask", fake_ask)
        monkeypatch.setattr(cortex_api, "search", boom)
        monkeypatch.setattr(cortex_api, "complete", boom)

        resp = _post(client, {"question": "how many active sessions are there?"})
        data = resp.get_json()
        assert data["mode"] == "ask"
        assert called.get("ask")
        assert data["grounded"] is True
        assert "iqe_execution" in data["governance"]["gates_run"]

    def test_generative_message_calls_cortex_complete(self, client, monkeypatch):
        import tools.cortex.api as cortex_api

        called = {}

        def fake_complete(prompt, ctx=None, **kw):
            called["complete"] = prompt
            return _FakeResult("Here is a draft.", False, confidence="")

        def boom(*a, **k):
            raise AssertionError("wrong facade invoked")

        monkeypatch.setattr(cortex_api, "complete", fake_complete)
        monkeypatch.setattr(cortex_api, "search", boom)
        monkeypatch.setattr(cortex_api, "ask", boom)

        resp = _post(client, {"question": "write a summary of the Q3 report"})
        data = resp.get_json()
        assert data["mode"] == "complete"
        assert called.get("complete")
        # Generative answers are ungrounded — the visible banner fires.
        assert data["grounded"] is False

    def test_explicit_mode_overrides_routing(self, client, monkeypatch):
        import tools.cortex.api as cortex_api

        called = {}

        def fake_complete(prompt, ctx=None, **kw):
            called["complete"] = prompt
            return _FakeResult("draft", False)

        monkeypatch.setattr(cortex_api, "complete", fake_complete)
        # A data-question phrased message, but the picker pins "complete".
        resp = _post(client, {"question": "how many rows are there?", "mode": "complete"})
        data = resp.get_json()
        assert data["mode"] == "complete"
        assert data["routing"]["source"] == "user"
        assert called.get("complete")

    def test_agent_intent_gated_behind_confirm(self, client, monkeypatch):
        import tools.cortex.api as cortex_api

        def boom(*a, **k):
            raise AssertionError("agent must not auto-execute a facade")

        monkeypatch.setattr(cortex_api, "search", boom)
        monkeypatch.setattr(cortex_api, "ask", boom)
        monkeypatch.setattr(cortex_api, "complete", boom)

        resp = _post(
            client,
            {"question": "build a pipeline that ingests data then deploy and monitor it"},
        )
        data = resp.get_json()
        assert data["mode"] == "agent"
        assert data["requires_confirm"] is True
        assert data["grounded"] is False

    def test_facade_error_degrades_not_500(self, client, monkeypatch):
        import tools.cortex.api as cortex_api

        def raises(*a, **k):
            raise RuntimeError("backend down")

        monkeypatch.setattr(cortex_api, "ask", raises)
        resp = _post(client, {"question": "how many sessions exist?"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["degraded"] is True
        assert data["grounded"] is False
        assert data["citations"] == []


class TestSessionPersistence:
    def test_turns_persist_and_reload(self, client, monkeypatch):
        import tools.cortex.api as cortex_api

        monkeypatch.setattr(
            cortex_api, "complete",
            lambda prompt, ctx=None, **kw: _FakeResult("draft answer", False),
        )

        r1 = _post(client, {"question": "write a haiku"})
        session_id = r1.get_json()["session_id"]

        r2 = _post(client, {"question": "write another haiku", "session_id": session_id})
        assert r2.get_json()["session_id"] == session_id

        reload = client.get(f"/cortex/api/session/{session_id}")
        assert reload.status_code == 200
        body = reload.get_json()
        # 2 exchanges → 4 turns (user+assistant each).
        assert body["turn_count"] == 4
        roles = [t["role"] for t in body["turns"]]
        assert roles == ["user", "assistant", "user", "assistant"]
        assert body["turns"][0]["content"] == "write a haiku"
        assert body["session"]["session_id"] == session_id

    def test_reload_unknown_session_is_empty(self, client):
        resp = client.get("/cortex/api/session/does-not-exist")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["turn_count"] == 0
        assert body["session"] is None
