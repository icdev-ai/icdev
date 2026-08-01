# CUI // SP-CTI
"""Auth enforcement tests for the Agentic AI Canvas (penta-aadc-01).

The canvas previously carried no auth of its own across ~130 routes, leaving
mass-delete (delete_all_designs) and the live LLM/web-search cost endpoints
(simulate_cot, simulate_cod, run_research_pipeline, run_research_agent)
reachable unauthenticated. A blueprint-level ``before_request`` guard now
enforces authentication for every state-changing (non-GET) request plus an
explicit set of expensive GET endpoints, while read-only GETs keep their
prior posture.

These tests mount ONLY the aadc blueprint on a bare Flask app so the guard is
exercised in isolation from the platform's app-level auth middleware. The
unauthenticated path never reaches a route handler (the guard aborts first),
so no canvas tables are required.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask, g
from werkzeug.exceptions import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.agentic_ai_canvas.blueprint as bp  # noqa: E402


# --- Requests that MUST be authenticated (method, path) --------------------
# Covers the four named live-LLM cost endpoints (all POST) plus the
# mass/single delete, save, and checkpoint-restore state-changers.
PROTECTED_REQUESTS = [
    ("DELETE", "/agentic-ai/api/designs"),                       # delete_all_designs (mass delete)
    ("DELETE", "/agentic-ai/api/designs/d1"),                    # delete_design
    ("PUT", "/agentic-ai/api/designs/d1"),                       # save_design
    ("POST", "/agentic-ai/api/designs"),                         # create_design
    ("POST", "/agentic-ai/api/designs/d1/simulate-cot"),         # simulate_cot (LLM)
    ("POST", "/agentic-ai/api/designs/d1/simulate-cod"),         # simulate_cod (LLM)
    ("POST", "/agentic-ai/api/designs/d1/run-pipeline"),         # run_research_pipeline (LLM/web)
    ("POST", "/agentic-ai/api/designs/d1/run-agent"),            # run_research_agent (LLM/web)
    ("POST", "/agentic-ai/api/designs/d1/checkpoints/c1/restore"),  # restore_checkpoint
    ("POST", "/agentic-ai/api/designs/d1/assess"),              # run_assessment
    ("POST", "/agentic-ai/api/designs/d1/launch"),             # launch_design
]

# Read-only GETs that must keep their current (open) posture — the guard must
# NOT return 401/403 for these.
READONLY_GETS = [
    "/agentic-ai/api/portfolio",
    "/agentic-ai/api/designs/d1",
]


@pytest.fixture
def app(monkeypatch):
    """Bare Flask app with ONLY the aadc blueprint mounted.

    ``_ensure_init`` is neutralised so tests never touch canvas DB init; the
    unauthenticated path aborts in the guard before any handler runs.
    """
    monkeypatch.setattr(bp, "_ensure_init", lambda: None)
    application = Flask(__name__)
    application.secret_key = "test-secret"
    application.config["TESTING"] = True
    # Let handler-internal errors surface as 500 responses rather than
    # propagating: read-only handlers may crash without a DB, but a 500 (not a
    # 401/403) still proves the guard did not auth-block the read.
    application.config["PROPAGATE_EXCEPTIONS"] = False
    application.register_blueprint(bp.aadc_bp)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


class TestUnauthenticatedBlocked:
    @pytest.mark.parametrize("method,path", PROTECTED_REQUESTS)
    def test_state_changing_and_llm_requests_denied(self, client, method, path):
        resp = client.open(path, method=method)
        assert resp.status_code in (401, 403), (
            f"{method} {path} expected 401/403, got {resp.status_code}"
        )

    def test_mass_delete_denied_specifically(self, client):
        # The highest-impact route: unauthenticated mass delete must be blocked.
        resp = client.delete("/agentic-ai/api/designs")
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        "path",
        [
            "/agentic-ai/api/designs/d1/simulate-cot",
            "/agentic-ai/api/designs/d1/simulate-cod",
            "/agentic-ai/api/designs/d1/run-pipeline",
            "/agentic-ai/api/designs/d1/run-agent",
        ],
    )
    def test_llm_cost_endpoints_never_invoke_llm_unauthenticated(self, path, client, monkeypatch):
        # If auth blocks first, the LLM provider is never constructed. Make any
        # LLM construction blow up so a regression that lets the handler run is
        # caught loudly rather than silently costing money.
        def _boom(*_a, **_k):  # pragma: no cover - only runs on regression
            raise AssertionError("LLM invoked on unauthenticated request!")

        try:
            import tools.llm.provider as _prov

            monkeypatch.setattr(_prov, "LLMRequest", _boom, raising=False)
        except Exception:
            pass
        resp = client.post(path, json={})
        assert resp.status_code == 401


class TestReadOnlyPreserved:
    @pytest.mark.parametrize("path", READONLY_GETS)
    def test_get_reads_not_auth_blocked(self, client, path):
        # Read-only GETs keep their prior posture: the guard must not 401/403
        # them. (Handlers may 500 without a DB — that is not an auth block.)
        resp = client.get(path)
        assert resp.status_code not in (401, 403), (
            f"GET {path} was auth-blocked ({resp.status_code}); reads must stay open"
        )


class TestGuardUnit:
    """Direct unit tests of the guard decision logic (DB-free)."""

    def test_needs_auth_true_for_non_get(self, app):
        for m in ("POST", "PUT", "DELETE", "PATCH"):
            with app.test_request_context("/agentic-ai/api/designs", method=m):
                assert bp._aadc_request_needs_auth() is True

    def test_needs_auth_false_for_get(self, app):
        with app.test_request_context("/agentic-ai/api/portfolio", method="GET"):
            assert bp._aadc_request_needs_auth() is False

    def test_guard_allows_authenticated_write(self, app):
        with app.test_request_context(
            "/agentic-ai/api/designs", method="POST", json={}
        ):
            g.current_user = {"id": "u1", "role": "admin", "status": "active"}
            # Guard returns None (allow) and raises nothing.
            assert bp._aadc_auth_guard() is None

    def test_guard_denies_unauthenticated_write(self, app):
        with app.test_request_context(
            "/agentic-ai/api/designs", method="POST", json={}
        ):
            g.current_user = None
            with pytest.raises(HTTPException) as exc:
                bp._aadc_auth_guard()
            assert exc.value.code in (401, 403)

    def test_guard_allows_unauthenticated_read(self, app):
        with app.test_request_context("/agentic-ai/api/portfolio", method="GET"):
            g.current_user = None
            assert bp._aadc_auth_guard() is None

    def test_current_user_from_session_fallback(self, app, monkeypatch):
        # No g.current_user, but a session cookie identifies an active user.
        import tools.dashboard.auth as _auth

        monkeypatch.setattr(
            _auth, "get_user_by_id",
            lambda uid, *a, **k: {"id": uid, "status": "active", "role": "developer"},
        )
        with app.test_request_context("/agentic-ai/api/designs", method="POST"):
            from flask import session

            session["user_id"] = "sess-user"
            assert bp._aadc_current_user() is not None
