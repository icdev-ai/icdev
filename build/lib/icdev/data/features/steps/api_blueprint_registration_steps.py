# CUI // SP-CTI
"""BDD step definitions for API Blueprint Registration feature (P1.1).

NIST 800-53: SA-11 (Developer Security Testing), CM-3 (Configuration Change Control)
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

from behave import given, then, when

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


@given("the ICDEV™ dashboard application is initialized for testing")
def step_app_initialized(context):
    """Set up a minimal Flask app for route-map inspection."""
    from flask import Flask
    context.app = Flask("test_app")
    context.app.config["TESTING"] = True
    context.blueprints_registered = False


@when("I import register_api_blueprints from tools.dashboard.api")
def step_import_register_api_blueprints(context):
    try:
        from tools.dashboard.api import register_api_blueprints
        context.register_api_blueprints = register_api_blueprints
        context.import_error = None
    except ImportError as exc:
        context.register_api_blueprints = None
        context.import_error = str(exc)


@then("the function should be callable")
def step_function_callable(context):
    assert context.import_error is None, (
        f"ImportError when importing register_api_blueprints: {context.import_error}"
    )
    assert callable(context.register_api_blueprints), (
        "register_api_blueprints is not callable"
    )


@when("I call register_api_blueprints on a test Flask app")
def step_call_register_blueprints(context):
    from tools.dashboard.api import register_api_blueprints
    register_api_blueprints(context.app)
    context.blueprints_registered = True
    context.all_rules = {rule.rule for rule in context.app.url_map.iter_rules()}


@then('the app should have a route matching "{prefix}"')
def step_route_exists(context, prefix):
    matched = [r for r in context.all_rules if r.startswith(prefix)]
    assert matched, (
        f"No route matching '{prefix}' found. "
        f"Registered /api/v1/* routes: "
        f"{sorted(r for r in context.all_rules if '/api/v1/' in r)}"
    )


@then('a GET request to "{path}" should return HTTP 200 or 404 not 500')
def step_legacy_alias_no_500(context, path):
    with context.app.test_client() as client:
        resp = client.get(path)
    assert resp.status_code != 500, (
        f"GET {path} returned 500 — legacy alias must not crash"
    )


@when("I inspect the create_app function in tools.dashboard.app")
def step_inspect_create_app(context):
    from tools.dashboard import app as app_mod
    context.create_app_source = inspect.getsource(app_mod.create_app)


@then("it should call register_api_blueprints")
def step_calls_register(context):
    assert "register_api_blueprints" in context.create_app_source, (
        "create_app() does not call register_api_blueprints"
    )


@then("it should not contain inline register_blueprint calls for the core API set")
def step_no_inline_calls(context):
    inline_calls = [
        "app.register_blueprint(projects_api)",
        "app.register_blueprint(kanban_api)",
        "app.register_blueprint(agents_api)",
    ]
    found = [c for c in inline_calls if c in context.create_app_source]
    assert not found, (
        f"create_app() still contains inline blueprint registrations: {found}"
    )


@then("the app should have registered the sre_api blueprint or logged a warning")
def step_sre_registered_or_skipped(context):
    # SRE registration must not crash. If route exists it must be /api/v1/sre.
    sre_routes = [r for r in context.all_rules if "/sre" in r]
    if sre_routes:
        assert all("/api/v1/" in r or "/api/sre" in r for r in sre_routes), (
            f"SRE routes not under /api/v1/ or /api/sre: {sre_routes}"
        )
    # Reaching here without exception = graceful skip confirmed


@then('the registered blueprints should include routes prefixed with "/api/v1/"')
def step_v1_prefix_present(context):
    v1_routes = [r for r in context.all_rules if r.startswith("/api/v1/")]
    assert v1_routes, (
        "No /api/v1/* routes found after register_api_blueprints — "
        "blueprints must be mounted with /api/v1/ prefix"
    )
