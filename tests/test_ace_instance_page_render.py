# CUI // SP-CTI
"""GET /coworker/<id> is a PAGE route and must never answer 200 application/json.

qa-fail-5f7cf03a0b0a4351. Measured on the live dashboard 2026-08-22:
``curl -H 'Accept: text/html' /coworker/<id>`` returned ``200 application/json``
for every existing ace_instances row. ``instance.html`` line 508 does
``{{ resume_token | tojson }}``; fc12cfa09 added the ``resume_token`` query to
``icdev/tools/ace/blueprint.py`` ONLY, and the next tools/ -> icdev/ sync
(3d16b47a3) overwrote it -- so the template that ships in both trees asks for a
variable the route that actually runs (``tools/ace/blueprint.py``, per the
xit-decl-02 shim) never passed. Jinja's ``Undefined`` is not JSON serializable,
``render_template`` raised ``TypeError``, and the route's ``except Exception``
logged it at INFO and returned the row data as JSON 200.

Nothing went red: no 500, no route_smoke signal, no CUI banner -- the only thing
that noticed was a Playwright assertion on the banner text. These tests pin both
halves:

* the page renders as HTML with the CUI banner, and the resume token reaches the
  template (present -> button rendered, absent -> no button, never Undefined);
* when a page template DOES fail to render, every ``ace_bp`` page route answers
  **500 text/html**, never 200 JSON -- asserted behaviourally through the real
  dashboard app AND structurally over the blueprint's AST, because a structural
  test pinned to one function is blind to the second site.

Renders through an ISOLATED app that borrows the real dashboard app's
template folder and context processors (the gated convention in
tests/cortex/test_blueprint_routes.py): ``base.html`` needs ``nav_tree`` and the
brand banner, so a bare ``Flask()`` would only ever exercise the fallback path --
and the shared singleton only carries ``ace_bp`` when ``ICDEV_ACE_ENABLED`` is
set, which CI never sets (see ``_isolated_app``).

Run: pytest tests/test_ace_instance_page_render.py -v
"""
from __future__ import annotations

import ast
import importlib
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = ROOT / "tools" / "ace" / "blueprint.py"

CUI_BANNER = "CUI // SP-CTI"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ace_env(tmp_path, monkeypatch):
    """Point ICDEV_ACE_DB_URL at a fresh SQLite DB carrying the ACE schema."""
    db_path = tmp_path / "ace_instance_page.db"
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(db_path))
    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    monkeypatch.setitem(bp_mod._state, "db_ready", True)
    return db_path


def _conn():
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection("ICDEV_ACE_DB_URL")


def _seed_instance(instance_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, trust_tier, config_json) "
            "VALUES (%s, %s, 'ai_developer', 'active', 'yellow', %s)",
            (instance_id, instance_id, '{"problem_text": "Debug", "trigger_source": "dashboard"}'),
        )
        conn.execute(
            "INSERT INTO ace_coworkers (id, instance_id, role_id, display_name, state, trust_tier) "
            "VALUES (%s, %s, 'ai_developer', 'AI Developer', 'working', 'yellow')",
            (f"cw-{instance_id}", instance_id),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_session(instance_id: str, token: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ace_sessions (session_id, instance_id, conversation_history, resume_token, turn_count) "
            "VALUES (%s, %s, '[]', %s, 0)",
            (f"sess-{uuid.uuid4().hex[:8]}", instance_id, token),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def instance_id(ace_env):
    iid = f"ace-{uuid.uuid4().hex[:12]}"
    _seed_instance(iid)
    return iid


def _isolated_app():
    """A FRESH Flask app carrying the dashboard's rendering context, with ``ace_bp`` on it.

    NOT the shared ``tools.dashboard.app`` singleton. The ``ace`` canvas is
    ``default_enabled: false`` behind ``ICDEV_ACE_ENABLED``
    (args/component_registry.yaml), so on a runner with no ``.env`` the
    singleton never registers ``ace_bp`` and every ``/coworker/...`` answers
    404 on BOTH trees -- which is how this file's first CI run read
    "13 failed / 2 passed on this tree, 14 failed / 1 passed at the merge
    base": the red-first gate called it broken, not red-first, while it passed
    on a developer machine whose ``.env`` enables the canvas
    (qa-fail-9de4533aba26c880, the sweep that re-found the same defect a day
    later because #1903 could not merge). And a blueprint cannot be registered
    onto the singleton after it has served its first request, so a guard
    around ``register_blueprint`` would make the verdict depend on module
    order. Copying the singleton's template folder, config, filters, globals
    and context processors (``base.html`` reads ``nav_tree``) onto a fresh app
    renders the real pages while mutating nothing other tests observe. Same
    shape as tests/cortex/test_blueprint_routes.py::_isolated_app.
    """
    from flask import Flask

    from tools.dashboard.app import app as _dashboard_app

    bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    app = Flask(
        __name__,
        template_folder=_dashboard_app.template_folder,
        static_folder=_dashboard_app.static_folder,
    )
    app.config.update(_dashboard_app.config)
    app.jinja_env.filters.update(_dashboard_app.jinja_env.filters)
    app.jinja_env.globals.update(_dashboard_app.jinja_env.globals)
    app.template_context_processors[None].extend(
        _dashboard_app.template_context_processors.get(None, [])
    )
    app.secret_key = _dashboard_app.secret_key or "test-secret"
    app.config["TESTING"] = True
    # ace_bp carries url_prefix="/coworker" itself, and its record_once hook
    # registers ace_api_bp (/api/ace) on the same app -- registering ace_bp
    # alone registers both; registering ace_api_bp again raises.
    app.register_blueprint(bp_mod.ace_bp)
    return app


@pytest.fixture()
def client(ace_env, icdev_db, monkeypatch):
    """Test client on an isolated app that renders the REAL dashboard templates."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))

    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))

    app = _isolated_app()
    with app.test_client() as tc:
        with tc.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield tc


def _is_html(resp) -> bool:
    return "text/html" in (resp.content_type or "")


# ---------------------------------------------------------------------------
# The page renders -- and the resume token reaches the template
# ---------------------------------------------------------------------------


def test_instance_page_is_html_with_cui_banner(client, instance_id):
    """The defect as measured: 200 application/json with no banner."""
    r = client.get(f"/coworker/{instance_id}", headers={"Accept": "text/html"})
    assert r.status_code == 200, r.data[:300]
    assert _is_html(r), f"page route answered {r.content_type!r}, not HTML"
    body = r.data.decode("utf-8", errors="replace")
    assert CUI_BANNER in body
    assert instance_id in body


def test_instance_page_without_session_has_no_resume_button(client, instance_id):
    r = client.get(f"/coworker/{instance_id}")
    assert r.status_code == 200 and _is_html(r)
    body = r.data.decode("utf-8", errors="replace")
    # The stylesheet names .resume-session-btn unconditionally; the BUTTON carries the id.
    assert 'id="resume-session-btn"' not in body
    # The JS literal is a real JSON null, never Jinja's Undefined.
    assert "const RESUME_TOKEN  = null;" in body


def test_instance_page_with_session_exposes_resume_token(client, instance_id):
    token = f"rt-{uuid.uuid4().hex[:12]}"
    _seed_session(instance_id, token)
    r = client.get(f"/coworker/{instance_id}")
    assert r.status_code == 200 and _is_html(r)
    body = r.data.decode("utf-8", errors="replace")
    assert 'id="resume-session-btn"' in body
    assert f'const RESUME_TOKEN  = "{token}";' in body


def test_unknown_instance_is_404(client, ace_env):
    r = client.get("/coworker/ace-does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# A template failure on a PAGE route is a 500 HTML page -- never 200 JSON
# ---------------------------------------------------------------------------

_PAGE_PATHS = [
    "/coworker/{iid}",
    "/coworker/",
    "/coworker/roles",
    "/coworker/profiles/new",
    "/coworker/trust",
    "/coworker/sessions",
    "/coworker/sessions/sess-x",
    "/coworker/live/{iid}",
    "/coworker/evals",
    "/coworker/evals/trends",
]


@pytest.mark.parametrize("path", _PAGE_PATHS)
def test_template_failure_on_page_route_is_500_html_not_200_json(client, instance_id, monkeypatch, path):
    """Whatever breaks a template, a page route must not report success as JSON."""
    bp_mod = importlib.import_module("icdev.tools.ace.blueprint")

    def _boom(*_a, **_k):
        raise RuntimeError("simulated template failure")

    monkeypatch.setattr(bp_mod, "render_template", _boom)
    r = client.get(path.format(iid=instance_id), headers={"Accept": "text/html"})
    assert r.status_code == 500, f"{path}: {r.status_code} {r.content_type}"
    assert "application/json" not in (r.content_type or ""), f"{path}: JSON body on a page route"
    assert _is_html(r), f"{path}: {r.content_type!r}"


# ---------------------------------------------------------------------------
# Structural: no ace_bp page route may answer a render_template failure with jsonify
# ---------------------------------------------------------------------------


def _page_route_functions(tree: ast.Module):
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            func = call.func if call else dec
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "route"
                and isinstance(func.value, ast.Name)
                and func.value.id == "ace_bp"
            ):
                yield node
                break


def _calls(node: ast.AST, name: str) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if (isinstance(f, ast.Name) and f.id == name) or (isinstance(f, ast.Attribute) and f.attr == name):
                return True
    return False


def test_no_page_route_falls_back_to_jsonify_on_template_failure():
    """A structural test pinned to instance_detail alone is blind to the other nine."""
    tree = ast.parse(BLUEPRINT.read_text(encoding="utf-8"), filename=str(BLUEPRINT))
    offenders: list[str] = []
    for fn in _page_route_functions(tree):
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            if not any(_calls(stmt, "render_template") for stmt in node.body):
                continue
            for handler in node.handlers:
                if _calls(handler, "jsonify"):
                    offenders.append(f"{fn.name}:{handler.lineno}")
    assert not offenders, f"page routes answering a template failure with JSON: {offenders}"


# ---------------------------------------------------------------------------
# GET /api/ace/<id>/audit -- the endpoint the Activity Log tab fetches
# ---------------------------------------------------------------------------
# task-42a17b8956. 407111d59 added ``api_audit`` to icdev/tools/ace/blueprint.py
# ONLY; the next mirror sync (3d16b47a3) deleted it, and NEITHER tree carried
# the route while instance.html still fetched it: 404 text/html, and
# ``r.json()`` threw in tests/e2e/coworker_lifecycle.spec.ts:476. Same shape as
# the resume_token defect above -- a feature that lands only in the mirror is
# one sync away from vanishing.


def _seed_audit_rows(instance_id: str, n: int) -> None:
    conn = _conn()
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO ace_audit_log (instance_id, coworker_id, action, detail, actor, created_at) "
                "VALUES (%s, %s, %s, %s, 'system', %s)",
                (instance_id, f"cw-{instance_id}", "step_complete", f"step {i}", f"2026-08-22T00:00:{i:02d}Z"),
            )
        conn.commit()
    finally:
        conn.close()


def test_audit_endpoint_returns_events_array_and_count(client, instance_id):
    """The e2e assertion, run in-process: 200 application/json, events[], numeric count."""
    _seed_audit_rows(instance_id, 3)
    r = client.get(f"/api/ace/{instance_id}/audit?limit=50")
    assert r.status_code == 200, r.data[:300]
    assert "application/json" in (r.content_type or ""), r.content_type
    body = r.get_json()
    assert isinstance(body["events"], list)
    assert isinstance(body["count"], int)
    assert body["count"] == len(body["events"]) == 3
    # Oldest first -- the tab appends newer events below older ones.
    assert [e["detail"] for e in body["events"]] == ["step 0", "step 1", "step 2"]
    # Every field instance.html's buildAuditItem reads is present.
    for ev in body["events"]:
        assert {"action", "actor", "detail", "created_at"} <= set(ev)


def test_audit_endpoint_honours_limit(client, instance_id):
    _seed_audit_rows(instance_id, 5)
    r = client.get(f"/api/ace/{instance_id}/audit?limit=2")
    assert r.status_code == 200
    body = r.get_json()
    assert body["count"] == 2 and len(body["events"]) == 2


def test_audit_endpoint_empty_instance_is_json_not_404(client, instance_id):
    """No events yet is an empty array, never a missing route."""
    r = client.get(f"/api/ace/{instance_id}/audit")
    assert r.status_code == 200 and "application/json" in (r.content_type or "")
    assert r.get_json() == {"events": [], "count": 0}


def test_audit_route_exists_in_both_trees():
    """The mirror must carry the route too, or the next sync deletes it again."""
    for tree in (ROOT / "tools" / "ace" / "blueprint.py", ROOT / "icdev" / "tools" / "ace" / "blueprint.py"):
        src = tree.read_text(encoding="utf-8")
        assert '@ace_api_bp.route("/<instance_id>/audit"' in src, tree
        assert "def api_audit(" in src, tree
