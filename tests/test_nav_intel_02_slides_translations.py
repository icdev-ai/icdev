# CUI // SP-CTI
"""nav-intel-02 — audit-and-fix regression tests for the /slides and
/translations Build-menu pipelines.

Covers the concrete bugs fixed in this PR:

  1. translation_manager._update_job_status paramstyle mismatch (B7) — the
     UPDATE statement mixed '?' and '%s' placeholders. On the PostgreSQL primary
     backend the '?' placeholders raise, and the exception was swallowed, so job
     status/metrics were NEVER persisted. Statement must be all '%s'.
  2. dependency_mapper.resolve_imports call-order bug (B6) — both callers passed
     languages before the import list, so _normalize_lang(list).lower() raised
     and dep_mappings was always empty. Import list must be the first argument.
  3. translations.html chart-fallback XSS (d) — user-supplied language names were
     interpolated into innerHTML via '<pre>' + JSON.stringify(...). Must use
     textContent.
  4. Slides blueprint RBAC (e) — costly/destructive endpoints (generate, revise,
     regenerate, PUT/DELETE slide, template upload/fill) must be gated to
     authoring roles; viewers get 403, unauthenticated 401.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# (B7) translation_manager._update_job_status — %s-only paramstyle
# ---------------------------------------------------------------------------
class _RecordingCursor:
    def __init__(self, store):
        self._store = store

    def execute(self, sql, params=None):
        self._store.append((sql, params))
        return self


class _RecordingConn:
    def __init__(self, store):
        self._store = store

    def cursor(self):
        return _RecordingCursor(self._store)

    def commit(self):
        pass

    def close(self):
        pass


def test_update_job_status_uses_only_pyformat_placeholders(monkeypatch):
    from tools.translation import translation_manager as tm

    captured = []
    # Patch the name bound inside the module namespace (shim-aware).
    monkeypatch.setattr(tm, "get_connection", lambda *a, **k: _RecordingConn(captured))

    tm._update_job_status(
        "dummy.db", "job-1", "completed",
        translated_units=5, mocked_units=0, gate_result="pass",
    )

    assert captured, "UPDATE was never executed"
    sql, params = captured[0]
    # The whole statement must be PostgreSQL pyformat — no bare '?' placeholders.
    assert "?" not in sql, f"'?' placeholder leaked into PG-primary SQL: {sql}"
    assert sql.count("%s") == len(params), (
        f"placeholder/param count mismatch: {sql} params={params}"
    )
    assert "status = %s" in sql
    assert "WHERE id = %s" in sql


def test_update_job_status_noop_without_ids(monkeypatch):
    from tools.translation import translation_manager as tm

    captured = []
    monkeypatch.setattr(tm, "get_connection", lambda *a, **k: _RecordingConn(captured))
    tm._update_job_status(None, "job", "completed")
    tm._update_job_status("db", None, "completed")
    assert captured == []


# ---------------------------------------------------------------------------
# (B6) dependency_mapper.resolve_imports call-order contract
# ---------------------------------------------------------------------------
def test_resolve_imports_import_list_is_first_argument():
    from tools.translation.dependency_mapper import resolve_imports

    resolutions = resolve_imports(["os", "requests"], "python", "go")
    assert isinstance(resolutions, list)
    sources = {r["source_import"] for r in resolutions}
    assert sources == {"os", "requests"}


def test_resolve_imports_wrong_order_is_broken():
    """Documents the original bug: passing languages first mis-binds args and
    _normalize_lang() ends up calling .lower() on the import list."""
    from tools.translation.dependency_mapper import resolve_imports

    with pytest.raises(AttributeError):
        resolve_imports("python", "go", ["os", "requests"])


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "rel",
    [
        "tools/translation/translation_manager.py",
        "tools/translation/code_translator.py",
        "icdev/tools/translation/translation_manager.py",
        "icdev/tools/translation/code_translator.py",
    ],
)
def test_callers_pass_imports_first(rel):
    src = _read(rel)
    assert "resolve_imports(imports," in src, f"{rel}: import list must be first arg"
    # The buggy language-first orders must be gone.
    assert "resolve_imports(src_lang, tgt_lang, imports" not in src
    assert "resolve_imports(args.source_language, args.target_language, imports" not in src


# ---------------------------------------------------------------------------
# (d) translations.html chart-fallback XSS
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rel",
    [
        "tools/dashboard/templates/translations.html",
        "icdev/tools/dashboard/templates/translations.html",
    ],
)
def test_translations_chart_fallback_is_xss_safe(rel):
    html = _read(rel)
    # The unsafe innerHTML string-concat pattern must be gone.
    assert "'<pre>' + JSON.stringify" not in html, f"{rel}: unsafe innerHTML fallback still present"
    # Safe DOM construction via textContent must be present.
    assert "textContent = JSON.stringify(data.status_distribution" in html
    assert "textContent = JSON.stringify(data.language_pair_frequency" in html


# ---------------------------------------------------------------------------
# (e) Slides blueprint RBAC on costly/destructive endpoints
# ---------------------------------------------------------------------------
_USERS = {
    "admin-1": {"id": "admin-1", "role": "admin"},
    "viewer-1": {"id": "viewer-1", "role": "viewer"},
}


@pytest.fixture
def slides_client():
    from flask import Flask, g, session
    from tools.slides.blueprint import slides_bp

    app = Flask(__name__)
    app.secret_key = "nav-intel-02-test"
    app.config["TESTING"] = True
    app.register_blueprint(slides_bp)

    @app.before_request
    def _fake_auth():  # mirrors _auth_before_request: session user_id -> g.current_user
        g.current_user = _USERS.get(session.get("user_id"))

    return app.test_client()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


# (endpoint, http method, body) for every gated route.
_GATED = [
    ("/slides/api/generate", "post", {"title": "x"}),
    ("/slides/api/1/revise", "post", {"slide_id": 1, "feedback": "x"}),
    ("/slides/api/1/regenerate-slide", "post", {"slide_id": 1}),
    ("/slides/api/1/slides/1", "put", {"title": "x"}),
    ("/slides/api/1/slides/1", "delete", None),
    ("/slides/api/templates/1/fill", "post", {"selections": [1]}),
]


def _call(client, path, method, body):
    fn = getattr(client, method)
    if body is None:
        return fn(path)
    return fn(path, json=body)


@pytest.mark.parametrize("path,method,body", _GATED)
def test_gated_endpoint_denies_viewer(slides_client, path, method, body):
    _login(slides_client, "viewer-1")
    resp = _call(slides_client, path, method, body)
    assert resp.status_code == 403, f"{method.upper()} {path} should be 403 for viewer, got {resp.status_code}"


@pytest.mark.parametrize("path,method,body", _GATED)
def test_gated_endpoint_rejects_anonymous(slides_client, path, method, body):
    resp = _call(slides_client, path, method, body)
    assert resp.status_code == 401, f"{method.upper()} {path} should be 401 for anon, got {resp.status_code}"


def test_gated_endpoint_allows_admin_past_rbac(slides_client):
    """Admin passes the role gate — a DELETE of a nonexistent slide reaches the
    handler and returns 404 (not 401/403)."""
    _login(slides_client, "admin-1")
    resp = slides_client.delete("/slides/api/999999/slides/999999")
    assert resp.status_code not in (401, 403), (
        f"admin should pass RBAC, got {resp.status_code}"
    )
