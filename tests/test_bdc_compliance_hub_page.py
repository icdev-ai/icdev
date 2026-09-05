# CUI // SP-CTI
"""rmf-ui-11: /compliance is GOVERNED -- it lives on the Boundary canvas as /boundary/compliance-hub.

The Compliance Hub was one of ~15 compliance pages that were bare
``@app.route`` handlers in a 10,500-line app.py: no registry entry, NO RBAC
GUARD, no completeness gate, no IQE dispatch. A route on the Boundary & Supply
Chain Canvas blueprint gets all of those by construction -- app.py attaches
``guard_component_access("bdc", <min_il>)`` as a ``before_request`` on every
registered canvas blueprint, the registry's ``url_prefix`` + IQE adapter put
``/boundary/*`` on the client-side path->canvas map, and the canvas
completeness gate owns the template directory.

It is the RMF ARTIFACT hub -- the unified posture across every compliance
module and the page that LINKS the others -- so it moved LAST of the BDC set,
after rmf-ui-01/04/05 had each repointed their own card on it. A test below
pins that the hub still links every sibling at its GOVERNED home and none at
its old URL: the hub is the one page a stale href would keep alive.

The template is ``boundary_canvas/compliance_hub.html`` rather than
``compliance.html``: ``boundary_canvas/compliance.html`` already exists as
BDC's own per-design compliance view, and two pages under one filename is the
kind of silent collision a one-route-per-card migration exists to refuse.

This file clones tests/test_bdc_ato_compliance_page.py (the rmf-ui-01
exemplar): the governed home renders, the old URL redirects rather than 404s,
both base.html copies link the new path, no template links the old one, the
template is mirrored, and the page lands on the ``bdc`` IQE canvas.

Every test here is RED on the merge base (the route is a bare app.py handler
there), which is what the red-first gate records.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

NEW_PATH = "/boundary/compliance-hub"
OLD_PATH = "/compliance"
SIBLING_PATH = "/boundary/compliance/<design_id>"  # BDC's own per-design compliance view, untouched

BLUEPRINT = REPO / "tools" / "boundary_canvas" / "blueprint.py"
BLUEPRINT_MIRROR = REPO / "icdev" / "tools" / "boundary_canvas" / "blueprint.py"
APP_PY = REPO / "tools" / "dashboard" / "app.py"
APP_PY_MIRROR = REPO / "icdev" / "tools" / "dashboard" / "app.py"
TEMPLATE_DIR = REPO / "tools" / "dashboard" / "templates"
TEMPLATE_DIR_MIRROR = REPO / "icdev" / "tools" / "dashboard" / "templates"
TEMPLATE_ROOTS = (TEMPLATE_DIR, TEMPLATE_DIR_MIRROR)
TEMPLATE = TEMPLATE_DIR / "boundary_canvas" / "compliance_hub.html"
TEMPLATE_MIRROR = TEMPLATE_DIR_MIRROR / "boundary_canvas" / "compliance_hub.html"
SIBLING_TEMPLATE = TEMPLATE_DIR / "boundary_canvas" / "compliance.html"
OLD_TEMPLATE = TEMPLATE_DIR / "compliance.html"
OLD_TEMPLATE_MIRROR = TEMPLATE_DIR_MIRROR / "compliance.html"
BASE_HTML_COPIES = (
    TEMPLATE_DIR / "base.html",
    TEMPLATE_DIR_MIRROR / "base.html",
)
START_MD = REPO / ".claude" / "commands" / "start.md"

# The siblings that already migrated and whose card the hub links. Each pair is
# (old ungoverned URL, governed home); the hub must link the second and never
# the first. Keyed by the old URL so a card added here cannot be added without
# naming what it replaced.
MIGRATED_SIBLINGS = {
    "/ato-compliance": "/boundary/ato-compliance",   # rmf-ui-01
    "/oscal": "/boundary/oscal",                     # rmf-ui-04
    "/cato": "/boundary/cato-health",                # rmf-ui-05
    "/stig-manager": "/security/stig-manager",       # rmf-ui-12
    "/sbd": "/security/sbd",                         # rmf-ui-13
    "/prod-audit": "/security/prod-audit",           # rmf-ui-14
    "/ai-transparency": "/security/ai-transparency", # rmf-ui-15
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ── app fixture ─────────────────────────────────────────────────────────────

def _isolated_app(blueprint, url_prefix):
    """A FRESH Flask app carrying the dashboard's rendering context.

    Same shape as tests/cortex/test_blueprint_routes.py: registering onto the
    shared ``tools.dashboard.app`` singleton is order-dependent (and 404s on CI
    for a default-off canvas -- ``bdc`` is one), so copy the singleton's
    template folder, filters, globals and context processors onto a fresh app
    and register the blueprint there.
    """
    from flask import Flask

    from tools.dashboard.app import app as _dashboard_app

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
    app.register_blueprint(blueprint, url_prefix=url_prefix)
    return app


@pytest.fixture
def bdc_blueprint(monkeypatch):
    monkeypatch.setenv("ICDEV_BOUNDARY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_BDC_ENABLED", "true")
    monkeypatch.setenv("BDC_STORAGE_BACKEND", "sqlite")
    from tools.boundary_canvas.blueprint import create_boundary_blueprint

    bp = create_boundary_blueprint()
    assert bp is not None, "Boundary canvas blueprint did not build"
    return bp


@pytest.fixture
def bdc_app(bdc_blueprint):
    return _isolated_app(bdc_blueprint, "/boundary")


def _login(client):
    with client.session_transaction() as sess:
        sess["user_id"] = "tester"
        sess["username"] = "tester"
        sess["bdc_user"] = "tester"


# ── 1. The governed home renders, with the page's own API and the IQE widget ─

def test_governed_page_renders_for_an_authenticated_session(bdc_app):
    with bdc_app.test_client() as client:
        _login(client)
        resp = client.get(NEW_PATH)
    assert resp.status_code == 200, resp.data[:400]
    body = resp.data.decode("utf-8", errors="replace")
    assert "Unified Compliance Posture" in body
    # The page still drives its own API -- only the PAGE route moved.
    for api in ("/api/compliance/posture", "/api/compliance/unified-posture", "/api/compliance/evidence-chain"):
        assert api in body, f"{api} no longer driven from the page"
    # IQE-dispatchable: the widget is on the page (8-point gate, point 8).
    assert 'class="iqe-widget"' in body
    # Breadcrumb back to the canvas the page now lives on.
    assert 'href="/boundary/"' in body


def test_route_is_declared_on_the_boundary_blueprint(bdc_app):
    rules = {r.rule for r in bdc_app.url_map.iter_rules()}
    assert NEW_PATH in rules, sorted(r for r in rules if "boundary" in r)
    # BDC's own per-design compliance view stays: this card ADDS a route.
    assert SIBLING_PATH in rules


def test_the_two_compliance_pages_carry_distinct_titles():
    """Do NOT ship two pages both titled Compliance under one canvas.

    /boundary/compliance/<design_id> is BDC's per-design compliance view over its own
    tables; /boundary/compliance-hub is the cross-module posture hub over
    /api/compliance/*. A reader must be able to tell them apart from the tab
    title alone, and the two must not share a template file.
    """
    def _title(path: Path) -> str:
        m = re.search(r"{%\s*block title\s*%}(.*?){%\s*endblock\s*%}", _read(path))
        assert m, f"{path.relative_to(REPO)} declares no title block"
        return m.group(1).strip()

    assert SIBLING_TEMPLATE.exists(), "BDC's own compliance.html went missing -- a dropped page"
    assert TEMPLATE != SIBLING_TEMPLATE
    assert _title(TEMPLATE) != _title(SIBLING_TEMPLATE)
    assert "Compliance Hub" in _title(TEMPLATE)


# ── 2. RBAC: refused anonymous, both by the canvas's own wrapper and the
#       registry-level guard app.py attaches to every canvas blueprint ─────────

def test_anonymous_request_is_refused_by_the_canvas_wrapper(bdc_app):
    with bdc_app.test_client() as client:
        resp = client.get(NEW_PATH)
    assert resp.status_code in (301, 302, 401), resp.status_code
    if resp.status_code in (301, 302):
        assert "/login" in resp.headers.get("Location", "")


def test_component_guard_refuses_anonymous_when_enforced(bdc_blueprint, monkeypatch):
    """The SAME guard app.py installs -- guard_component_access("bdc", min_il).

    An isolated app has no ``login_page`` endpoint, so the guard answers 401
    (its documented fallback) rather than redirecting. A 404 here would mean
    the route is not on the guarded blueprint at all.
    """
    monkeypatch.setenv("ICDEV_ENFORCE_CANVAS_ACCESS", "true")
    from tools.config.component_registry import get_registry
    from tools.security.canvas_access import guard_component_access

    comp = get_registry().get("bdc")
    assert comp is not None, "bdc is not in the component registry"
    bdc_blueprint.before_request(guard_component_access("bdc", comp.min_il))
    app = _isolated_app(bdc_blueprint, "/boundary")
    with app.test_client() as client:
        resp = client.get(NEW_PATH)
    assert resp.status_code in (302, 401), resp.status_code
    assert resp.status_code != 404


# ── 3. The old URL redirects to the governed home -- never a dropped page ───

def _app_route_handler(source: str, rule: str) -> ast.FunctionDef | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and deco.func.attr == "route"
                and deco.args
                and isinstance(deco.args[0], ast.Constant)
                and deco.args[0].value == rule
            ):
                return node
    return None


@pytest.mark.parametrize("app_py", [APP_PY, APP_PY_MIRROR], ids=["tools", "icdev"])
def test_old_url_redirects_to_the_governed_home(app_py):
    handler = _app_route_handler(_read(app_py), OLD_PATH)
    assert handler is not None, f"{OLD_PATH} handler is gone from {app_py.name} -- a dropped page"
    calls = [n for n in ast.walk(handler) if isinstance(n, ast.Call)]
    redirects = [
        c for c in calls
        if isinstance(c.func, ast.Name) and c.func.id == "redirect"
        and c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == NEW_PATH
    ]
    assert redirects, f"{OLD_PATH} must redirect to {NEW_PATH}"
    renders = [
        c for c in calls
        if isinstance(c.func, ast.Name) and c.func.id == "render_template"
    ]
    assert not renders, f"{OLD_PATH} still renders the page itself -- two ungoverned homes"


@pytest.mark.parametrize("app_py", [APP_PY, APP_PY_MIRROR], ids=["tools", "icdev"])
def test_the_hubs_api_handlers_stayed_in_app_py(app_py):
    """Only the PAGE route moved. Its data still answers from app.py."""
    src = _read(app_py)
    for rule in ("/api/compliance/posture", "/api/compliance/unified-posture", "/api/compliance/evidence-chain"):
        assert _app_route_handler(src, rule) is not None, f"{rule} left app.py -- out of this card's scope"


# ── 4. Nav: BOTH base.html copies link the new path and highlight it ────────

@pytest.mark.parametrize("base_html", BASE_HTML_COPIES, ids=["tools", "icdev"])
def test_nav_links_the_governed_home_in_both_base_html_copies(base_html):
    text = _read(base_html)
    assert f'href="{NEW_PATH}"' in text, f"{base_html}: no link to {NEW_PATH}"
    assert f'href="{OLD_PATH}"' not in text, f"{base_html}: still links the ungoverned {OLD_PATH}"
    trigger = next(
        (line for line in text.splitlines() if "Compliance ▾" in line or "Compliance &#9662;" in line),
        "",
    )
    assert trigger, f"{base_html}: Compliance dropdown trigger not found"
    assert f"'{NEW_PATH}'" in trigger, f"{base_html}: {NEW_PATH} missing from the Compliance active-path list"
    # The old path stays in the active list: it is still a live redirect.
    assert f"'{OLD_PATH}'" in trigger


def test_both_base_html_copies_agree():
    assert _read(BASE_HTML_COPIES[0]) == _read(BASE_HTML_COPIES[1]), "base.html copies diverged"


@pytest.mark.parametrize("root", TEMPLATE_ROOTS, ids=["tools", "icdev"])
def test_no_template_links_the_ungoverned_url(root):
    """aiify/posture.html and base.html linked /compliance too -- every href is repointed."""
    offenders = sorted(
        str(p.relative_to(REPO))
        for p in root.rglob("*.html")
        if f'href="{OLD_PATH}"' in _read(p)
    )
    assert not offenders, f"templates still link {OLD_PATH}: {offenders}"


# ── 5. The template lives on the canvas and is mirrored; the old one is gone ─

def test_template_lives_on_the_canvas_and_is_mirrored():
    assert TEMPLATE.exists(), f"{TEMPLATE.relative_to(REPO)} missing"
    assert TEMPLATE_MIRROR.exists(), f"{TEMPLATE_MIRROR.relative_to(REPO)} missing (icdev/ mirror)"
    assert _read(TEMPLATE) == _read(TEMPLATE_MIRROR), "template and its icdev/ mirror diverged"
    assert "iqe_query_widget" in _read(TEMPLATE), "template must include the IQE widget"
    assert not OLD_TEMPLATE.exists(), "the ungoverned top-level template still exists"
    assert not OLD_TEMPLATE_MIRROR.exists(), "the ungoverned top-level template mirror still exists"


def test_blueprint_route_references_the_canvas_template():
    src = _read(BLUEPRINT)
    assert 'render_template("boundary_canvas/compliance_hub.html"' in src or (
        "render_template(\n" in src and '"boundary_canvas/compliance_hub.html"' in src
    ), "blueprint does not render boundary_canvas/compliance_hub.html"
    assert src == _read(BLUEPRINT_MIRROR), "blueprint and its icdev/ mirror diverged"


# ── 6. The hub links every migrated sibling at its GOVERNED home ────────────

@pytest.mark.parametrize("hub_html", (TEMPLATE, TEMPLATE_MIRROR), ids=["tools", "icdev"])
def test_hub_links_every_migrated_sibling_at_its_governed_home(hub_html):
    """The hub is the page that links the others -- it moved last so no href churns.

    A stale href here is the one place an ungoverned URL would stay reachable
    from the nav after its own card had closed.
    """
    text = _read(hub_html)
    for old, home in MIGRATED_SIBLINGS.items():
        assert f'href="{home}"' in text, f"{hub_html.name}: does not link {home}"
        assert f'href="{old}"' not in text, f"{hub_html.name}: still links the ungoverned {old}"


# ── 7. IQE dispatch: the path lands on the bdc canvas ───────────────────────

def test_path_canvas_map_dispatches_the_page_to_bdc():
    from tools.config.component_registry import get_registry

    for regex_src, canvas in get_registry().get_iqe_path_canvas():
        if re.search(regex_src, NEW_PATH):
            assert canvas == "bdc", f"{NEW_PATH} dispatches to {canvas!r}, not bdc"
            break
    else:
        pytest.fail(f"{NEW_PATH} matches no entry of the IQE path->canvas map")


# ── 8. The page is documented where the start command lists pages ───────────

def test_start_command_lists_the_governed_page():
    assert f"`{NEW_PATH}`" in _read(START_MD), (
        f"{NEW_PATH} missing from the Pages: line in .claude/commands/start.md"
    )
