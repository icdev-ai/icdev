# CUI // SP-CTI
"""rmf-ui-17: the bare ``/api/iqe-query`` rule belongs to the app-level canvas
dispatcher, and NO blueprint may claim it -- or any other rule -- twice.

THE DEFECT. ``tools/ops_hub/blueprint.py`` built ``Blueprint("ohc", url_prefix="")``
and declared ``@bp.route("/api/iqe-query")``; so did govlift and ai_observatory,
both prefix-less. Flask raises nothing when a rule is registered twice -- the
first registration wins and the rest are silently unreachable -- and the ohc
winner imported ``tools.iqe.engine``, a module that exists nowhere in the tree,
so a canvas-keyed ``POST /api/iqe-query`` 500'd on every page (observed
2026-09-03 while browser-verifying rmf-ui-11). Nothing on the board asks that
question on a schedule, so only a human typing into the bar ever saw it.

Two tests for the instance and one for the class:

* the bare rule resolves to ``iqe_dispatch`` in tools/dashboard/app.py and a
  canvas-keyed question answers 200 through it;
* each formerly colliding blueprint answers on a route under a prefix it owns,
  and a fresh app carrying all three no longer binds ``/api/iqe-query`` at all;
* a structural guard over the real dashboard's url_map: a rule under two
  endpoints whose view functions DIFFER is a collision. The six pre-existing
  chat/studio legacy pairs are grandfathered BY NAME below; a seventh fails.
"""
from __future__ import annotations

import pytest
from flask import Flask

# ── the structural predicate ────────────────────────────────────────────────


def distinct_code_collisions(app) -> dict[tuple[str, tuple[str, ...]], list[str]]:
    """(rule, methods) -> endpoints, for every rule two DIFFERENT view functions claim.

    A rule registered twice for the SAME function object (the ``wf`` /
    ``wf_legacy`` alias pairs, 43 of them on the live app) is an alias, not a
    collision: whichever registration wins runs the same code. A rule two
    different bodies claim is the defect -- one of them is unreachable, and
    nothing reports which.
    """
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for rule in app.url_map.iter_rules():
        methods = tuple(sorted(m for m in (rule.methods or ()) if m not in ("HEAD", "OPTIONS")))
        groups.setdefault((rule.rule, methods), []).append(rule.endpoint)
    out: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for key, endpoints in groups.items():
        if len(endpoints) < 2:
            continue
        codes = {app.view_functions[ep].__code__ for ep in endpoints}
        if len(codes) > 1:
            out[key] = sorted(endpoints)
    return out


# Pre-existing distinct-body collisions on the live app, measured 2026-09-03.
# Each is an app-level handler in tools/dashboard/app.py beside a *_api_legacy
# blueprint that re-declares the same rule with its own body. They predate this
# card and are enumerated BY NAME (a count can be held constant while the set
# churns). This set may only SHRINK: fix one, delete its line. Never add one.
GRANDFATHERED_COLLISIONS: dict[tuple[str, tuple[str, ...]], list[str]] = {
    ("/api/chat/use-cases", ("GET",)): ["api_chat_use_cases", "chat_api_legacy.list_use_cases"],
    ("/api/chat/use-cases/<use_case_id>", ("GET",)): ["api_chat_use_case_detail", "chat_api_legacy.get_use_case"],
    ("/api/chat/use-cases/<use_case_id>", ("PUT",)): ["api_chat_use_case_update", "chat_api_legacy.update_use_case"],
    ("/api/chat/use-cases/<use_case_id>/override", ("DELETE",)): ["api_chat_use_case_reset", "chat_api_legacy.reset_use_case"],
    ("/api/studio/workflows/runs", ("DELETE",)): ["studio_api_legacy.api_delete_all_runs", "studio_delete_all_runs"],
    ("/api/studio/workflows/runs/<run_id>", ("DELETE",)): ["studio_api_legacy.api_delete_run", "studio_delete_run"],
}


@pytest.fixture(scope="module")
def dashboard_app():
    from tools.dashboard.app import app

    return app


# ── the instance: /api/iqe-query is the app-level dispatcher's ──────────────


def test_bare_iqe_query_rule_resolves_to_the_app_level_dispatcher(dashboard_app):
    adapter = dashboard_app.url_map.bind("localhost")
    endpoint, _ = adapter.match("/api/iqe-query", method="POST")
    assert endpoint == "iqe_dispatch", (
        f"/api/iqe-query resolves to {endpoint!r}; a blueprint with url_prefix='' "
        "is shadowing the app-level canvas dispatcher"
    )
    # The rule base.html has posted to since the mini-bar shipped is the same endpoint.
    endpoint2, _ = adapter.match("/api/iqe/dispatch", method="POST")
    assert endpoint2 == "iqe_dispatch"


def test_canvas_keyed_question_answers_200_through_the_dispatcher(
    dashboard_app, icdev_db, monkeypatch
):
    import tools.iqe.executor as executor
    import tools.iqe.nl_to_iqe as nl_to_iqe_mod
    import tools.iqe.parser as parser_mod
    from tools.dashboard import app as app_mod

    assert "compliance" in app_mod._IQE_CANVAS_MAP, sorted(app_mod._IQE_CANVAS_MAP)

    # Same wiring as tests/test_app.py: point auth at the temp DB carrying the
    # seeded user and log the session in, so the request reaches the view.
    import tools.dashboard.auth as _auth

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))
    dashboard_app.config["TESTING"] = True

    seen: dict[str, object] = {}

    def _stub_nl(question, collections):
        seen["question"], seen["collections"] = question, list(collections)
        return {"iqe": "foreach v in compliance.violations select v.id", "explanation": "stub"}

    monkeypatch.setattr(nl_to_iqe_mod, "nl_to_iqe", _stub_nl)
    monkeypatch.setattr(parser_mod, "parse", lambda _s: object())
    monkeypatch.setattr(executor, "execute_query", lambda _ast, conn=None: [{"id": "v-1"}])

    with dashboard_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        resp = client.post(
            "/api/iqe-query", json={"question": "open violations", "canvas": "compliance"}
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    body = resp.get_json()
    assert body["ok"] is True
    assert body["canvas"] == "compliance"
    assert body["row_count"] == 1
    assert seen["question"] == "open violations"
    assert seen["collections"] == list(app_mod._IQE_CANVAS_MAP["compliance"][1])


# ── the formerly colliding blueprints, on a FRESH app ───────────────────────


@pytest.fixture
def three_blueprints_app(monkeypatch):
    """ohc + govlift + ai_observatory on a fresh Flask app, no app-level routes.

    Registering onto the shared singleton is order-dependent; a fresh app is the
    only place "nobody binds /api/iqe-query" can be asserted, because on the
    singleton the dispatcher now binds it.
    """
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "1")
    from tools.ai_observatory.blueprint import bp as ao_bp
    from tools.govlift.blueprint import create_govlift_blueprint
    from tools.ops_hub.blueprint import create_ops_hub_blueprint

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_ops_hub_blueprint())
    flask_app.register_blueprint(create_govlift_blueprint())
    flask_app.register_blueprint(ao_bp)
    return flask_app


def test_no_blueprint_claims_the_bare_rule(three_blueprints_app):
    adapter = three_blueprints_app.url_map.bind("localhost")
    from werkzeug.exceptions import NotFound

    with pytest.raises(NotFound):
        adapter.match("/api/iqe-query", method="POST")

    owned = {
        "/api/ops/iqe-query": "ohc.ohc_iqe_query",
        "/govlift/api/iqe-query": "govlift.govlift_iqe_query",
        "/ai-observatory/api/iqe-query": "ai_observatory.ao_api_iqe_query",
    }
    for path, endpoint in owned.items():
        assert adapter.match(path, method="POST")[0] == endpoint, path

    assert not distinct_code_collisions(three_blueprints_app)


def test_ops_hub_widget_route_answers_on_its_own_prefix(three_blueprints_app, monkeypatch):
    import tools.iqe.executor as executor
    import tools.iqe.nl_to_iqe as nl_to_iqe_mod
    import tools.iqe.parser as parser_mod
    from tools.ops_hub import blueprint as ohc_mod

    seen: dict[str, object] = {}

    def _stub_nl(question, collections):
        seen["collections"] = list(collections)
        return {"iqe": "foreach a in ohc.adapters select a.adapter_name", "explanation": "stub"}

    monkeypatch.setattr(nl_to_iqe_mod, "nl_to_iqe", _stub_nl)
    monkeypatch.setattr(parser_mod, "parse", lambda _s: object())
    monkeypatch.setattr(executor, "execute_query", lambda _ast, conn=None: [{"adapter_name": "ollama"}])

    client = three_blueprints_app.test_client()
    resp = client.post("/api/ops/iqe-query", json={"question": "adapter health"})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    body = resp.get_json()
    assert body["ok"] is True
    assert body["row_count"] == 1
    assert seen["collections"] == ohc_mod.IQE_COLLECTIONS

    assert client.post("/api/ops/iqe-query", json={}).status_code == 400
    # The module the old handler imported does not exist, and must not be asked for.
    import importlib.util

    assert importlib.util.find_spec("tools.iqe.engine") is None


def test_ops_hub_pages_hand_the_widget_its_route(three_blueprints_app):
    """Every OHC page includes the IQE widget; each render must pass it the route."""
    from tools.ops_hub import blueprint as ohc_mod

    assert ohc_mod._IQE_CTX["iqe_api_route"] == ohc_mod.IQE_API_ROUTE
    assert ohc_mod._IQE_CTX["iqe_canvas"] == "ohc"
    assert len(ohc_mod._IQE_CTX["iqe_examples"]) >= 3


# ── the class: no rule under two different bodies, anywhere ─────────────────


def test_no_rule_is_claimed_by_two_different_view_functions(dashboard_app):
    collisions = distinct_code_collisions(dashboard_app)

    new = {k: v for k, v in collisions.items() if k not in GRANDFATHERED_COLLISIONS}
    assert not new, (
        "NEW route collision(s) -- a rule two different view functions claim, so one "
        "is silently unreachable. Move the blueprint's rule under a prefix it owns; "
        f"never add to GRANDFATHERED_COLLISIONS: {new}"
    )

    # The bare rule is the case this card exists for: assert it by name too.
    assert ("/api/iqe-query", ("POST",)) not in collisions

    # A grandfathered entry whose endpoints are BOTH still registered and no longer
    # collide has been fixed -- prune the line so the set only ever shrinks.
    stale = {
        k: v for k, v in GRANDFATHERED_COLLISIONS.items()
        if k not in collisions and all(ep in dashboard_app.view_functions for ep in v)
    }
    assert not stale, f"fixed collision(s) still grandfathered -- delete the line: {stale}"
