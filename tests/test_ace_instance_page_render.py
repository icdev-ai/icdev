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

Uses the real dashboard app singleton (the gated convention in
tests/cortex/test_blueprint_routes.py) because ``base.html`` needs the app's
context processors (nav_tree, ROLE_VIEWS, brand banner); a bare ``Flask()``
cannot render it and would only ever exercise the fallback path.

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
            "VALUES (?, ?, 'ai_developer', 'active', 'yellow', ?)",
            (instance_id, instance_id, '{"problem_text": "Debug", "trigger_source": "dashboard"}'),
        )
        conn.execute(
            "INSERT INTO ace_coworkers (id, instance_id, role_id, display_name, state, trust_tier) "
            "VALUES (?, ?, 'ai_developer', 'AI Developer', 'working', 'yellow')",
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
            "VALUES (?, ?, '[]', ?, 0)",
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


@pytest.fixture()
def client(ace_env, icdev_db, monkeypatch):
    """Authenticated test client on the real dashboard app (never re-registers)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))

    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))

    from tools.dashboard.app import app

    app.config["TESTING"] = True
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
