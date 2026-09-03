# CUI // SP-CTI
"""rmf-fab-02 — the roll-up is actually REACHABLE from BDC, and read-only there.

WHAT THESE PIN, AND WHY EACH COULD REGRESS SILENTLY

1. THE SURFACE EXISTS AND RENDERS. A module nothing calls is this platform's
   signature defect. ``roll_up`` is consumed by the BDC cATO page and by one
   GET route; both are exercised here through a real Flask test client rather
   than by asserting the route is registered, because a registered route that
   raises in its template renders a 500 nobody's route-coverage check sees.

2. IT IS READ-ONLY BY CONSTRUCTION. GET with no POST sibling — asserted over
   the url_map, so a future edit adding a "re-evaluate" button has to break
   this test to land. The two cATO writers evaluate on call and would let a
   page render evidence it had just manufactured.

3. AN UNMEASURED PANEL SAYS SO. With no fabric registry the panel must render
   the word "Unmeasurable" and the disclaimer — never an empty section, which
   is indistinguishable from a clean board.

4. NO COMPOSITE REACHES THE WIRE. ``assert_no_blended_score`` runs inside the
   route before the payload is serialised, so a passthrough reintroduced
   upstream fails loudly instead of shipping a number with no denominator.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_BLUEPRINT = REPO_ROOT / "tools" / "boundary_canvas" / "blueprint.py"
_TEMPLATE = REPO_ROOT / "tools" / "dashboard" / "templates" / "boundary_canvas" / "cato.html"
_MIRROR_TEMPLATE = (
    REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates" / "boundary_canvas" / "cato.html"
)

_ROUTE = "/boundary/api/fabric-posture"


@pytest.fixture()
def bdc_app(tmp_path, monkeypatch):
    """A Flask app with only the BDC blueprint mounted.

    Built per-test rather than against the shared ``tools.dashboard.app``
    singleton: that singleton registers blueprints behind an
    ``if "x" not in app.blueprints`` guard, which makes a page test pass or fail
    on run order.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_BOUNDARY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.chdir(tmp_path)

    from flask import Flask

    from tools.boundary_canvas.blueprint import create_boundary_blueprint

    bp = create_boundary_blueprint()
    assert bp is not None, "BDC blueprint refused to build"

    app = Flask(__name__, template_folder=str(REPO_ROOT / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True
    app.secret_key = "rmf-fab-02"
    app.register_blueprint(bp, url_prefix="/boundary")

    # base.html renders its Canvases dropdown from the component registry's nav
    # context, which the dashboard app injects. Supplied here from the REAL
    # registry rather than stubbed, so a page that only renders under a fake nav
    # cannot pass this test.
    from tools.config.component_registry import ComponentRegistry

    nav_context = ComponentRegistry().get_nav_context()

    @app.context_processor
    def _inject_nav():
        return {"nav_tree": nav_context}

    return app


# ── 1 & 2. The route exists, answers, and cannot mutate ──────────────────────

def test_route_is_get_only_with_no_post_sibling(bdc_app):
    rules = [r for r in bdc_app.url_map.iter_rules() if str(r) == _ROUTE]
    assert rules, f"{_ROUTE} is not registered"
    assert sorted(rules[0].methods - {"HEAD", "OPTIONS"}) == ["GET"]
    mutating = [
        str(r) for r in bdc_app.url_map.iter_rules()
        if "fabric-posture" in str(r) and r.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert mutating == [], f"the roll-up must not be mutable: {mutating}"


def test_route_answers_and_emits_no_blended_score(bdc_app):
    with bdc_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["bdc_user"] = "tester"
            sess["user_id"] = "tester"
            sess["username"] = "tester"
        resp = client.get(_ROUTE)

    assert resp.status_code == 200, resp.data[:400]
    payload = json.loads(resp.data)

    from tools.fabric.posture import FORBIDDEN_BLEND_KEYS, assert_no_blended_score

    assert_no_blended_score(payload)
    body = resp.data.decode("utf-8")
    for forbidden in FORBIDDEN_BLEND_KEYS:
        assert f'"{forbidden}"' not in body, forbidden


def test_route_calls_the_guard_before_it_serialises():
    """Sweep the route's own AST — the guard must be inside the handler.

    Verified structurally because the payload on a deployment with no fabrics
    contains no composite whatever the route does, so a behavioural test alone
    would pass with the guard deleted.
    """
    tree = ast.parse(_BLUEPRINT.read_text(encoding="utf-8"))
    handler = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "bdc_api_fabric_posture"),
        None,
    )
    assert handler is not None, "bdc_api_fabric_posture is gone"
    called = {
        n.func.id for n in ast.walk(handler)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "roll_up" in called, "the route must consume the roll-up"
    assert "assert_no_blended_score" in called, "the guard must run before serialisation"


# ── 3. The page renders the panel, and an unmeasured panel says so ───────────

def test_cato_page_route_passes_the_rollup_into_the_template(bdc_app, monkeypatch):
    """The page route must actually CONSUME ``roll_up`` and hand it to the panel.

    The template context is captured rather than the finished HTML: base.html
    needs the dashboard app's full context processor (nav_tree, ROLE_VIEWS,
    canvas flags), so a full-page render belongs in ``e2e_boundary_cato.py``
    where a real server supplies it. What is owned HERE is the seam — that the
    route computes the roll-up and passes it under the name the panel reads.
    """
    captured = {}

    import tools.boundary_canvas.blueprint as bp_mod

    def _capture(template, **ctx):
        captured["template"] = template
        captured["ctx"] = ctx
        return "OK"

    monkeypatch.setattr(bp_mod, "render_template", _capture)

    with bdc_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["bdc_user"] = "tester"
            sess["user_id"] = "tester"
            sess["username"] = "tester"
        resp = client.get("/boundary/cato")

    assert resp.status_code == 200, resp.data[:400]
    assert captured["template"] == "boundary_canvas/cato.html"
    posture = captured["ctx"].get("fabric_posture")
    assert posture is not None, "the page does not consume the cross-fabric roll-up"

    from tools.fabric.posture import assert_no_blended_score

    assert_no_blended_score(posture)


def test_panel_declares_an_unmeasured_board_rather_than_rendering_nothing():
    """With no rmf-fab-01 registry the panel must SAY so.

    An empty section is indistinguishable from a clean board, which is the
    defect this whole card exists to refuse. The panel fragment is rendered
    against the REAL ``roll_up`` output, not a fixture of it.
    """
    from jinja2 import Environment

    from tools.fabric.posture import roll_up

    html = _TEMPLATE.read_text(encoding="utf-8")
    panel = html[html.index('<div class="fab-panel">'):html.index('<div id="control-detail"')]
    rendered = Environment().from_string(panel).render(fabric_posture=roll_up())

    assert "Cross-Fabric Posture" in rendered
    assert "Unmeasurable" in rendered
    assert "nothing here is a verdict about posture" in rendered
    # The standing disclaimer is on the panel whether or not fabrics exist.
    assert "no overall number" in rendered


def test_panel_renders_a_fabric_as_not_assessed_with_both_scopes_labelled():
    from jinja2 import Environment

    from tools.fabric.posture import roll_up

    html = _TEMPLATE.read_text(encoding="utf-8")
    panel = html[html.index('<div class="fab-panel">'):html.index('<div id="control-detail"')]
    result = roll_up([{
        "key": "fab-alpha",
        "display_name": "Alpha Enclave",
        "classification": "CUI",
        "impact_level": "IL5",
    }])
    rendered = Environment().from_string(panel).render(fabric_posture=result)

    assert "Alpha Enclave" in rendered
    assert "not assessed" in rendered
    # Both cATO sources, each with its scope named and labelled.
    assert "system scope" in rendered and "application scope" in rendered
    assert "System-level" in rendered and "Per-application" in rendered
    assert "tools/compliance/cato_monitor.py" in rendered
    assert "tools/security_canvas/continuous_authorization.py" in rendered
    # A fabric nobody assessed renders no number for any measure.
    assert "<strong>0</strong>" not in rendered
    assert "<strong>100" not in rendered


def test_panel_never_hardcodes_a_percentage_or_a_composite():
    """The template must render values it was GIVEN, never invent one."""
    html = _TEMPLATE.read_text(encoding="utf-8")
    panel = html[html.index('<div class="fab-panel">'):html.index('<div id="control-detail"')]
    for banned in ("100.0", "readiness_score", "posture_score", "|round(0)|int", "/100"):
        assert banned not in panel, f"{banned!r} in the cross-fabric panel"
    # It must state a denominator beside every measured value.
    assert "denominator_of" in panel
    assert "scope_label" in panel


def test_template_is_mirrored_to_the_icdev_package():
    """A dashboard template ships in BOTH trees or the wheel renders the old one."""
    assert _MIRROR_TEMPLATE.exists()
    assert (
        _MIRROR_TEMPLATE.read_text(encoding="utf-8")
        == _TEMPLATE.read_text(encoding="utf-8")
    )


def test_posture_module_is_mirrored_to_the_icdev_package():
    src = REPO_ROOT / "tools" / "fabric" / "posture.py"
    mirror = REPO_ROOT / "icdev" / "tools" / "fabric" / "posture.py"
    assert mirror.exists()
    assert mirror.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
