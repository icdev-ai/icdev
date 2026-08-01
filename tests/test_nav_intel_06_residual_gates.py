# CUI // SP-CTI
"""nav-intel-06 — Intelligence-menu residual backing-logic gates.

Follow-on to the nav-intel-01 audit (tests/test_nav_intel_01_menu_audit.py).
Three residual defects on Intelligence-menu surfaces are locked here:

  1. WriteGuard content-management CRUD (glossary / taxonomy / style-profile
     create-update-delete in tools/dashboard/api/writeguard.py) relied on the
     global auth hook only — any authenticated user, down to the lowest-priv
     ``developer``, could mutate the shared, cross-user configuration that
     steers every subsequent analysis. Now role-gated to admin/pm
     (401 anon / 403 wrong role). The interactive per-user tools
     (analyze / rewrite / …) stay open to any authenticated user.

  2. Code-Quality read endpoints (tools/dashboard/api/code_quality.py) caught
     only ``sqlite3.Error``. Under the PostgreSQL primary backend a psycopg
     error is not a sqlite3.Error, so it slipped the handler and bubbled up
     unlogged. Broadened to the storage-layer surface (Exception) WITH logging
     per the degraded-state pattern: fail loud, return an explicit error
     payload, never a silent/unhandled 500.

  3. The Ask-ICDEV Q&A narration path (tools/dashboard/app.py — the LLMRouter
     call funnelled through ``_cm_llm_narrate``, shared by
     /api/components-map/ask and /api/ask-icdev/sessions/<id>/message) had no
     per-user throttle on the LLM cost surface. Now rate-limited per
     authenticated user via ``_narration_budget_ok``; over budget degrades to
     raw evidence rather than incur unbounded LLM calls.

RBAC deny-cases are exercised functionally against the real blueprint using the
fake-auth (X-Test-Role) harness from tests/test_nav_intel_01_menu_audit.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.timeout(180)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ============================================================================
# (1) WriteGuard content-management CRUD — role-gated; analyze stays open
# ============================================================================

_NO_BYPASS_VARS = (
    "ICDEV_AUTH_BYPASS",
    "ICDEV_DASHBOARD_API_KEY",
    "ICDEV_DASHBOARD_DEV_AUTOLOGIN",
)


def _fake_auth_app(register):
    from flask import Flask, g, request, session

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-session", "role": role, "tenant_id": "t"}
            session["user_id"] = "u-session"

    register(app)
    return app


@pytest.fixture()
def wg_client(tmp_path, monkeypatch):
    for var in _NO_BYPASS_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "nav_intel_06.db"))

    try:
        from tools.dashboard.api.writeguard import writeguard_api
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"writeguard blueprint not importable: {exc}")

    app = _fake_auth_app(lambda a: a.register_blueprint(writeguard_api))
    with app.test_client() as c:
        yield c


# (method, path) for the newly-gated WriteGuard content-management routes.
_WG_CRUD_ROUTES = [
    ("POST", "/api/writeguard/glossary"),
    ("PUT", "/api/writeguard/glossary/some-id"),
    ("DELETE", "/api/writeguard/glossary/some-id"),
    ("POST", "/api/writeguard/taxonomy"),
    ("PUT", "/api/writeguard/taxonomy/some-id"),
    ("DELETE", "/api/writeguard/taxonomy/some-id"),
    ("POST", "/api/writeguard/profile"),
    ("DELETE", "/api/writeguard/profiles/some-id"),
]


@pytest.mark.parametrize("method,path", _WG_CRUD_ROUTES)
def test_wg_crud_anonymous_is_401(wg_client, method, path):
    resp = wg_client.open(path, method=method, json={})
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", _WG_CRUD_ROUTES)
def test_wg_crud_developer_is_403(wg_client, method, path):
    resp = wg_client.open(
        path, method=method, json={}, headers={"X-Test-Role": "developer"}
    )
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", _WG_CRUD_ROUTES)
def test_wg_crud_admin_not_role_blocked(wg_client, method, path):
    # admin passes the role gate; the body may then 400/404/500 on missing data
    # or an empty DB, but it must NOT be a 401/403 role denial.
    resp = wg_client.open(
        path, method=method, json={}, headers={"X-Test-Role": "admin"}
    )
    assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}"


def test_wg_analyze_stays_open_to_authenticated_users(wg_client):
    """The interactive per-user analyze tool is not role-gated: a developer
    reaches the handler (400 on empty text) rather than a 403 role denial."""
    resp = wg_client.post(
        "/api/writeguard/analyze", json={}, headers={"X-Test-Role": "developer"}
    )
    assert resp.status_code not in (401, 403), resp.status_code
    assert resp.status_code == 400  # "No text provided" — reached the handler


def test_wg_rewrite_stays_open_to_authenticated_users(wg_client):
    resp = wg_client.post(
        "/api/writeguard/rewrite", json={}, headers={"X-Test-Role": "developer"}
    )
    assert resp.status_code not in (401, 403), resp.status_code
    assert resp.status_code == 400  # "No text provided" — reached the handler


def test_wg_glossary_read_stays_open(wg_client):
    # GET list is a read — must not require a mutation role. It touches the DB
    # (empty tmp DB) so a 500 is acceptable; a 401/403 is not.
    resp = wg_client.get(
        "/api/writeguard/glossary", headers={"X-Test-Role": "developer"}
    )
    assert resp.status_code not in (401, 403), resp.status_code


def test_wg_crud_decorators_present_at_source():
    src = _read("tools/dashboard/api/writeguard.py")
    assert "from tools.dashboard.auth import require_role" in src
    assert '_WG_CRUD_ROLES = ("admin", "pm")' in src
    # Every content-management mutation carries the gate.
    assert src.count("@require_role(*_WG_CRUD_ROLES)") == 8


# ============================================================================
# (2) Code-Quality — PG (non-sqlite) DB error is caught, logged, degraded
# ============================================================================


class _PGLikeError(Exception):
    """Stand-in for a psycopg error: an Exception that is NOT a sqlite3.Error.
    The pre-fix ``except sqlite3.Error`` handler would let this bubble up."""


class _RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


@pytest.fixture()
def cq_client(tmp_path, monkeypatch):
    for var in _NO_BYPASS_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "nav_intel_06_cq.db"))

    try:
        from tools.dashboard.api import code_quality
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"code_quality blueprint not importable: {exc}")

    def _raise_pg(*a, **k):
        raise _PGLikeError("server closed the connection unexpectedly")

    rec = _RecordingLogger()
    monkeypatch.setattr(code_quality, "_get_db", _raise_pg)
    monkeypatch.setattr(code_quality, "logger", rec)

    app = _fake_auth_app(lambda a: a.register_blueprint(code_quality.code_quality_api))
    with app.test_client() as c:
        c._rec = rec  # type: ignore[attr-defined]
        yield c


# Read endpoints whose handlers previously caught only sqlite3.Error.
_CQ_READ_ROUTES = [
    "/api/code-quality/summary",
    "/api/code-quality/top-complex",
    "/api/code-quality/smells",
    "/api/code-quality/trend",
    "/api/code-quality/feedback",
]


@pytest.mark.parametrize("path", _CQ_READ_ROUTES)
def test_cq_pg_error_returns_degraded_500_not_unhandled(cq_client, path):
    resp = cq_client.get(path, headers={"X-Test-Role": "admin"})
    # Broadened handler: an explicit error payload, not a silent/empty success
    # and not an unhandled bubble.
    assert resp.status_code == 500, f"{path} -> {resp.status_code}"
    body = resp.get_json()
    assert body is not None and body.get("error"), f"{path}: {body}"


@pytest.mark.parametrize("path", _CQ_READ_ROUTES)
def test_cq_pg_error_is_logged_not_silent(cq_client, path):
    cq_client._rec.warnings.clear()
    cq_client.get(path, headers={"X-Test-Role": "admin"})
    assert any(
        "code-quality query failed" in w for w in cq_client._rec.warnings
    ), f"{path}: expected a logged warning, got {cq_client._rec.warnings}"


def test_cq_broadened_catch_at_source():
    src = _read("tools/dashboard/api/code_quality.py")
    assert "from tools.logging.icdev_logger import get_logger" in src
    # The narrow sqlite-only catch on the read paths is gone.
    assert "except sqlite3.Error as e:" not in src
    assert src.count('logger.warning("code-quality query failed: %s", e)') == 5


# ============================================================================
# (3) Ask-ICDEV narration — per-user LLM-cost rate limit
# ============================================================================


def test_narration_budget_allows_then_blocks():
    """The sliding-window budget records calls and blocks once the per-minute
    cap is spent for that key."""
    from tools.dashboard.app import _narration_budget_ok

    key = "user-A-" + str(id(object()))
    assert _narration_budget_ok(key, max_per_min=3) is True
    assert _narration_budget_ok(key, max_per_min=3) is True
    assert _narration_budget_ok(key, max_per_min=3) is True
    # Fourth call within the window is over budget.
    assert _narration_budget_ok(key, max_per_min=3) is False


def test_narration_budget_is_per_key():
    from tools.dashboard.app import _narration_budget_ok

    a = "user-B-" + str(id(object()))
    b = "user-C-" + str(id(object()))
    assert _narration_budget_ok(a, max_per_min=1) is True
    assert _narration_budget_ok(a, max_per_min=1) is False
    # A different user has an independent budget.
    assert _narration_budget_ok(b, max_per_min=1) is True


def test_narration_budget_env_override(monkeypatch):
    from tools.dashboard.app import _narration_budget_ok

    monkeypatch.setenv("ICDEV_ASK_NARRATION_MAX_PER_MIN", "2")
    key = "user-D-" + str(id(object()))
    assert _narration_budget_ok(key) is True
    assert _narration_budget_ok(key) is True
    assert _narration_budget_ok(key) is False


def test_narration_chokepoint_wired_at_source():
    """Both /ask endpoints funnel narration through _cm_llm_narrate, so the
    budget guard lives there once and covers both."""
    src = _read("tools/dashboard/app.py")
    assert "def _narration_budget_ok(" in src
    # The single LLM narration function calls the budget guard before invoking
    # the router.
    narrate_idx = src.index("def _cm_llm_narrate(")
    router_idx = src.index("from tools.llm.router import LLMRouter", narrate_idx)
    window = src[narrate_idx:router_idx]
    assert "_narration_budget_ok(" in window, "budget guard must precede the LLM call"
