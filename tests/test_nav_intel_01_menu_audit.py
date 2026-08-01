# CUI // SP-CTI
"""nav-intel-01 — Intelligence-menu backing-logic audit fixes.

A deep audit of the Intelligence-menu surfaces (/ai-observatory, /ontology,
/autoresearch, /system-graph, /components-map, /skillhub, /writeguard,
/code-quality, and the Pulse governance judge/publish path) surfaced six
classes of defect. This file locks the *small in-place* fixes landed alongside
the audit:

  (b) Fail-open — Pulse WriteGuard quality gate. Per-checker runtime exceptions
      returned a *passing* default score (70/100), so a broken/erroring checker
      inflated the composite toward ``passed``. Now a checker that raised on the
      content (``status == "error"``) is excluded from scoring AND forces
      ``passed = False`` (fail-closed). ``tools/pulse/writeguard.py``.

  (d) Unescaped LLM/user content rendered via ``innerHTML``. The AI-Observatory
      decision table (LLM ``decision`` / ``decision_type`` / ``model_used``) and
      the Autoresearch experiment table (LLM ``hypothesis``) now HTML-escape
      every interpolated field through an ``_esc`` helper; the Code-Quality
      table escapes function/file identifiers.

  (e) RBAC on mutating endpoints. ``POST /api/ontology/rebuild`` (destructive —
      wipes & rebuilds all ontology_* tables), the SkillHub/marketplace install
      endpoint, the Code-Quality scan trigger, the Components-Map refresh
      subprocess trigger, and the Pulse judge / undo-reject editorial actions
      are now role-gated (401 anon / 403 wrong role).

  (f) PG-dialect ``?`` placeholders in runtime SQL rewritten to ``%s``:
      ai_observatory/analytics.py, ontology/federation.py,
      autoresearch/fitness_proposal_quality.py, marketplace/catalog_manager.py.

  Plus a correctness fix: the SkillHub storefront route unwraps
  ``list_assets()`` (which returns ``{"assets": [...], ...}``) before filtering,
  instead of iterating the dict keys and 500-ing on any q/category filter.

RBAC deny-cases are exercised functionally against the real blueprints using
the fake-auth (X-Test-Role) harness from tests/test_nav_sec_06_mutation_rbac.py.
app.py inline routes are asserted at the source level (building create_app costs
~15s and the decorators are the security-relevant surface).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.timeout(120)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ============================================================================
# (b) Pulse fail-open — WriteGuard quality gate is fail-closed on checker error
# ============================================================================


def test_pulse_quality_check_fails_closed_when_checker_errors(monkeypatch):
    """A checker that raised on the content (status='error') must NOT be able to
    approve it: passed is forced False and checker_errored is surfaced True."""
    from tools.pulse import writeguard

    # Simulate a moderation/checker that crashed on THIS content. Historically
    # this returned score=70 with status='error' and still counted toward the
    # composite, inflating it toward passed.
    monkeypatch.setattr(
        writeguard,
        "check_grammar",
        lambda text: {"status": "error", "findings": [], "score": 70, "error": "boom"},
    )

    result = writeguard.run_full_quality_check("Some perfectly ordinary blog body text.")

    assert result["checker_errored"] is True
    assert result["passed"] is False, "errored checker must fail-closed, never approve"


def test_pulse_quality_check_exposes_checker_errored_flag():
    """The result contract carries the checker_errored flag (default False on a
    clean run) so callers/UI can distinguish a real fail from a degraded one."""
    from tools.pulse import writeguard

    result = writeguard.run_full_quality_check("A short neutral sentence for scoring.")
    assert "checker_errored" in result
    assert isinstance(result["checker_errored"], bool)


# ============================================================================
# (d) Unescaped-content — templates escape LLM/user fields before innerHTML
# ============================================================================

_AIOBS_TPL = "tools/dashboard/templates/ai_observatory/page.html"
_AR_TPL = "tools/dashboard/templates/autoresearch.html"
_CQ_TPL = "tools/dashboard/templates/code_quality.html"


def test_ai_observatory_template_defines_and_uses_escaper():
    src = _read(_AIOBS_TPL)
    assert "function _esc(" in src
    # LLM-derived fields must flow through _esc, not be interpolated raw.
    assert "_esc(d.decision_type" in src
    assert "_esc(d.model_used" in src
    assert "_esc(e.message)" in src
    # The decision text is escaped inside _truncate.
    assert "return _esc(t);" in src
    # Regression guard: the old raw interpolations are gone.
    assert "'</span>' + (d.decision_type" not in src
    assert "+ (d.model_used || '—') +" not in src


def test_autoresearch_template_escapes_hypothesis():
    src = _read(_AR_TPL)
    assert "function _esc(" in src
    assert "_esc(hyp)" in src  # title attribute
    assert "_esc(hyp.substring(0, 60))" in src
    # Regression guard: raw hypothesis interpolation removed.
    assert 'title="${exp.hypothesis || \'\'}"' not in src


def test_code_quality_template_escapes_identifiers():
    src = _read(_CQ_TPL)
    assert "function _esc(" in src
    assert "_esc(f.function_name" in src
    assert "_esc((f.file_path" in src


# ============================================================================
# (f) PG-dialect — runtime SQL uses %s, not ? placeholders
# ============================================================================


def test_ai_observatory_analytics_uses_pg_placeholders():
    src = _read("tools/ai_observatory/analytics.py")
    assert 'canvas_type = %s' in src
    assert 'decision_type = %s' in src
    assert 'canvas_type = ?' not in src
    assert 'decision_type = ?' not in src


def test_ontology_federation_uses_pg_placeholders():
    src = _read("tools/ontology/federation.py")
    # The kg_nodes IN(...) lookups build their placeholder list with %s now.
    assert 'join("%s" for _ in etypes)' in src
    assert 'join("?" for _ in etypes)' not in src


def test_fitness_proposal_quality_uses_pg_placeholders():
    src = _read("tools/autoresearch/fitness_proposal_quality.py")
    assert "WHERE created_at >= %s" in src
    assert "WHERE created_at >= ?" not in src


def test_catalog_manager_list_assets_uses_pg_placeholders():
    src = _read("tools/marketplace/catalog_manager.py")
    # The list_assets filter block previously used ? placeholders on a PG conn.
    assert "asset_type = %s" in src
    assert "LIMIT %s OFFSET %s" in src
    assert "asset_type = ?" not in src
    assert "LIMIT ? OFFSET ?" not in src


# ============================================================================
# (correctness) SkillHub storefront unwraps list_assets() dict result
# ============================================================================


def test_list_assets_returns_dict_contract():
    """list_assets returns a dict envelope; the storefront route must unwrap it."""
    from tools.marketplace import catalog_manager

    import inspect

    src = inspect.getsource(catalog_manager.list_assets)
    assert 'return {"assets"' in src  # envelope shape the route must unwrap


def test_studio_marketplace_route_unwraps_asset_envelope():
    src = _read("tools/dashboard/api/studio.py")
    # Both the assets and categories routes must pull the list out of the dict.
    assert src.count('_res.get("assets", [])') >= 2
    assert "assets = list_assets()\n        # Client-side" not in src


# ============================================================================
# (e) RBAC — mutating endpoints require an authorized role
# ============================================================================

# app.py inline routes: assert decorators at source (create_app is ~15s cold).


def test_app_py_pulse_and_cmap_routes_have_role_gate():
    # nav-misc-03: the pulse routes were extracted from app.py into the
    # pulse_api blueprint (tools/dashboard/api/pulse.py) with their
    # @require_role gates preserved verbatim. The components-map routes remain
    # inline in app.py.
    pulse_src = _read("tools/dashboard/api/pulse.py")
    app_src = _read("tools/dashboard/app.py")

    def _decorators_above(src: str, route_marker: str) -> str:
        idx = src.index(route_marker)
        # Grab the ~4 lines between the route decorator and the def.
        window = src[idx: idx + 400]
        return window

    judge = _decorators_above(pulse_src, '"/api/pulse/posts/<post_id>/judge"')
    assert "@require_role(*_PULSE_EDITORIAL_ROLES)" in judge

    undo = _decorators_above(pulse_src, '"/api/pulse/posts/<post_id>/undo-reject"')
    assert "@require_role(*_PULSE_EDITORIAL_ROLES)" in undo

    refresh = _decorators_above(app_src, '"/api/components-map/refresh"')
    assert '@require_role("admin", "pm")' in refresh


# Blueprint routes: exercise the gate functionally with fake-auth.

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
def blueprint_client(tmp_path, monkeypatch):
    for var in _NO_BYPASS_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "nav_intel.db"))

    try:
        from tools.ontology.blueprint import bp as ontology_bp
        from tools.dashboard.api.studio import studio_api
        from tools.dashboard.api.code_quality import code_quality_api
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"blueprint(s) not importable: {exc}")

    def _register(app):
        app.register_blueprint(ontology_bp)
        app.register_blueprint(studio_api)
        app.register_blueprint(code_quality_api)

    app = _fake_auth_app(_register)
    with app.test_client() as c:
        yield c


# (method, path) for the newly-gated mutating blueprint routes.
_RBAC_ROUTES = [
    ("POST", "/api/ontology/rebuild"),
    ("POST", "/api/studio/marketplace/assets/demo-asset/install"),
    ("POST", "/api/code-quality/scan"),
]


@pytest.mark.parametrize("method,path", _RBAC_ROUTES)
def test_blueprint_mutation_anonymous_is_401(blueprint_client, method, path):
    resp = blueprint_client.open(path, method=method, json={})
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", _RBAC_ROUTES)
def test_blueprint_mutation_developer_is_403(blueprint_client, method, path):
    resp = blueprint_client.open(
        path, method=method, json={}, headers={"X-Test-Role": "developer"}
    )
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


def test_ontology_rebuild_admin_not_blocked(blueprint_client, monkeypatch):
    # Neutralize the destructive rebuild so the allow-case has no side effects.
    import tools.ontology.federation as fed

    monkeypatch.setattr(fed, "build_federation", lambda *a, **k: {"status": "ok", "classes": 0})
    resp = blueprint_client.post("/api/ontology/rebuild", json={}, headers={"X-Test-Role": "admin"})
    assert resp.status_code not in (401, 403), resp.status_code


def test_ontology_read_stays_open(blueprint_client):
    # GET classes is a read — must not require a mutation role.
    resp = blueprint_client.get("/api/ontology/classes", headers={"X-Test-Role": "developer"})
    assert resp.status_code not in (401, 403), resp.status_code
