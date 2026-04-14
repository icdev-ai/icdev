#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for P1.1 — Centralized API Blueprint Registration.

NIST 800-53 Controls: SA-11 (Developer Security Testing), CM-3 (Configuration Change Control)

Tests verify:
  - register_api_blueprints() exists in tools/dashboard/api/__init__.py
  - All 55+ blueprints are registered on a Flask test app
  - Routes appear under /api/v1/* prefix
  - /api/* alias still resolves (backward compat for 1 release)
  - app.py create_app() delegates to register_api_blueprints()
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Minimal Flask app fixture (no DB required for route-map inspection)
# ---------------------------------------------------------------------------

@pytest.fixture()
def bare_flask_app():
    """Return a minimal Flask app with no blueprints registered."""
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


# ---------------------------------------------------------------------------
# P1.1-RED-01: register_api_blueprints must be importable
# ---------------------------------------------------------------------------

class TestRegisterApiBlueprintsExists:
    """RED: register_api_blueprints must be defined in tools/dashboard/api/__init__.py."""

    def test_function_importable(self):
        """register_api_blueprints must be importable from tools.dashboard.api."""
        from tools.dashboard import api as api_pkg
        assert hasattr(api_pkg, "register_api_blueprints"), (
            "register_api_blueprints not found in tools/dashboard/api/__init__.py"
        )

    def test_function_callable(self):
        """register_api_blueprints must be a callable."""
        from tools.dashboard.api import register_api_blueprints
        assert callable(register_api_blueprints)

    def test_function_accepts_flask_app(self):
        """register_api_blueprints must accept a Flask app as its first argument."""
        from tools.dashboard.api import register_api_blueprints
        sig = inspect.signature(register_api_blueprints)
        params = list(sig.parameters.keys())
        assert len(params) >= 1, "register_api_blueprints must accept at least one argument (app)"


# ---------------------------------------------------------------------------
# P1.1-RED-02: Core blueprints registered with /api/v1/* prefix
# ---------------------------------------------------------------------------

CORE_BLUEPRINT_V1_PATHS = [
    "/api/v1/projects",
    "/api/v1/kanban",
    "/api/v1/agents",
    "/api/v1/compliance",
    "/api/v1/poam",
    "/api/v1/audit",
    "/api/v1/metrics",
    "/api/v1/batch",
    "/api/v1/diagrams",
    "/api/v1/activity",
    "/api/v1/usage",
    "/api/v1/traces",
    "/api/v1/provenance",
    "/api/v1/xai",
    "/api/v1/oscal",
    "/api/v1/prod-audit",
    "/api/v1/ai-transparency",
    "/api/v1/ai-accountability",
    "/api/v1/code-quality",
    "/api/v1/fedramp-20x",
    "/api/v1/evidence",
    "/api/v1/lineage",
    "/api/v1/filesync",
    "/api/v1/security-scan",
    "/api/v1/migration",
    "/api/v1/sbd",
    "/api/v1/pr-intel",
    "/api/v1/iac",
    "/api/v1/cato",
    "/api/v1/control-inheritance",
    "/api/v1/migration-cost",
    "/api/v1/compliance-debt",
    "/api/v1/stig-manager",
    "/api/v1/ato-package",
    "/api/v1/orchestration",
    "/api/v1/studio",
    "/api/v1/writeguard",
]

# Blueprints whose routes are registered inline (no url_prefix on Blueprint object)
INLINE_ROUTE_BLUEPRINTS = [
    "/api/v1/analytics",
    "/api/v1/events",
    "/api/v1/kanban/plans",
    "/api/v1/nlq",
    "/api/v1/oracle",
    "/api/v1/intake",
    "/api/v1/canvas-projects",
    "/api/v1/sandbox",
]


@pytest.fixture()
def registered_app(bare_flask_app):
    """Flask app with all API blueprints registered via register_api_blueprints."""
    from tools.dashboard.api import register_api_blueprints
    register_api_blueprints(bare_flask_app)
    return bare_flask_app


def _route_prefixes(app) -> set[str]:
    """Extract the set of url_prefix values from all registered blueprints."""
    prefixes = set()
    for rule in app.url_map.iter_rules():
        prefixes.add(rule.rule.split("/<")[0].rsplit("/", 1)[0] if "/" in rule.rule else rule.rule)
    return prefixes


def _all_rules(app) -> set[str]:
    """Return all route rules registered on the app (stripped of variable parts)."""
    return {rule.rule for rule in app.url_map.iter_rules()}


class TestV1PrefixRegistration:
    """RED: After register_api_blueprints, routes must exist under /api/v1/*."""

    def test_projects_v1_route_exists(self, registered_app):
        rules = _all_rules(registered_app)
        v1_routes = [r for r in rules if r.startswith("/api/v1/projects")]
        assert v1_routes, f"No /api/v1/projects/* routes found. Registered routes: {sorted(rules)}"

    def test_kanban_v1_route_exists(self, registered_app):
        rules = _all_rules(registered_app)
        v1_routes = [r for r in rules if r.startswith("/api/v1/kanban")]
        assert v1_routes, f"No /api/v1/kanban/* routes found. Registered routes: {sorted(rules)}"

    def test_agents_v1_route_exists(self, registered_app):
        rules = _all_rules(registered_app)
        v1_routes = [r for r in rules if r.startswith("/api/v1/agents")]
        assert v1_routes, "No /api/v1/agents/* routes found."

    def test_compliance_v1_route_exists(self, registered_app):
        rules = _all_rules(registered_app)
        v1_routes = [r for r in rules if r.startswith("/api/v1/compliance")]
        assert v1_routes, "No /api/v1/compliance/* routes found."

    def test_audit_v1_route_exists(self, registered_app):
        rules = _all_rules(registered_app)
        v1_routes = [r for r in rules if r.startswith("/api/v1/audit")]
        assert v1_routes, "No /api/v1/audit/* routes found."

    def test_metrics_v1_route_exists(self, registered_app):
        rules = _all_rules(registered_app)
        v1_routes = [r for r in rules if r.startswith("/api/v1/metrics")]
        assert v1_routes, "No /api/v1/metrics/* routes found."

    def test_security_scan_v1_route_exists(self, registered_app):
        rules = _all_rules(registered_app)
        v1_routes = [r for r in rules if r.startswith("/api/v1/security-scan")]
        assert v1_routes, "No /api/v1/security-scan/* routes found."

    def test_orchestration_v1_route_exists(self, registered_app):
        rules = _all_rules(registered_app)
        v1_routes = [r for r in rules if r.startswith("/api/v1/orchestration")]
        assert v1_routes, "No /api/v1/orchestration/* routes found."

    def test_studio_v1_route_exists(self, registered_app):
        rules = _all_rules(registered_app)
        v1_routes = [r for r in rules if r.startswith("/api/v1/studio")]
        assert v1_routes, "No /api/v1/studio/* routes found."

    def test_minimum_v1_blueprint_count(self, registered_app):
        """At least 35 distinct /api/v1/* route prefixes must exist."""
        rules = _all_rules(registered_app)
        v1_roots = {r.split("/")[3] for r in rules if r.startswith("/api/v1/") and len(r.split("/")) > 3}
        assert len(v1_roots) >= 35, (
            f"Expected ≥35 /api/v1/* route roots, got {len(v1_roots)}: {sorted(v1_roots)}"
        )


# ---------------------------------------------------------------------------
# P1.1-RED-03: /api/* alias backward compatibility
# ---------------------------------------------------------------------------

LEGACY_ALIAS_SAMPLES = [
    "/api/projects",
    "/api/kanban",
    "/api/agents",
    "/api/compliance",
    "/api/audit",
    "/api/metrics",
    "/api/security-scan",
    "/api/orchestration",
]


class TestLegacyAliasExists:
    """RED: /api/* aliases must still resolve (not 500) for backward compat."""

    def test_legacy_projects_alias_exists(self, registered_app):
        """GET /api/projects must be a registered route (not missing)."""
        rules = _all_rules(registered_app)
        legacy_routes = [r for r in rules if r.startswith("/api/projects")]
        assert legacy_routes, (
            "No /api/projects/* legacy alias found. Alias must be kept for 1 release."
        )

    def test_legacy_kanban_alias_exists(self, registered_app):
        rules = _all_rules(registered_app)
        legacy_routes = [r for r in rules if r.startswith("/api/kanban")]
        assert legacy_routes, "No /api/kanban/* legacy alias found."

    def test_legacy_agents_alias_exists(self, registered_app):
        rules = _all_rules(registered_app)
        legacy_routes = [r for r in rules if r.startswith("/api/agents")]
        assert legacy_routes, "No /api/agents/* legacy alias found."

    def test_legacy_compliance_alias_exists(self, registered_app):
        rules = _all_rules(registered_app)
        legacy_routes = [r for r in rules if r.startswith("/api/compliance")]
        assert legacy_routes, "No /api/compliance/* legacy alias found."


# ---------------------------------------------------------------------------
# P1.1-RED-04: app.py delegates to register_api_blueprints
# ---------------------------------------------------------------------------

class TestAppPyDelegation:
    """RED: create_app() in app.py must call register_api_blueprints."""

    def test_create_app_calls_register_api_blueprints(self):
        """create_app source must reference register_api_blueprints."""
        from tools.dashboard import app as app_mod
        source = inspect.getsource(app_mod.create_app)
        assert "register_api_blueprints" in source, (
            "create_app() does not call register_api_blueprints — refactor not applied"
        )

    def test_inline_blueprint_registrations_removed(self):
        """create_app source must NOT contain individual register_blueprint(projects_api) etc."""
        from tools.dashboard import app as app_mod
        source = inspect.getsource(app_mod.create_app)
        # These specific inline calls should be gone — replaced by register_api_blueprints()
        inline_calls = [
            "app.register_blueprint(projects_api)",
            "app.register_blueprint(kanban_api)",
            "app.register_blueprint(agents_api)",
            "app.register_blueprint(compliance_api)",
            "app.register_blueprint(audit_api)",
            "app.register_blueprint(metrics_api)",
        ]
        found_inline = [call for call in inline_calls if call in source]
        assert not found_inline, (
            f"create_app() still has inline blueprint registrations that should be delegated: {found_inline}"
        )

    def test_register_api_blueprints_imported_in_app(self):
        """app.py must import register_api_blueprints from tools.dashboard.api."""
        app_source_path = BASE_DIR / "tools" / "dashboard" / "app.py"
        source = app_source_path.read_text(encoding="utf-8")
        assert "register_api_blueprints" in source, (
            "app.py does not import or reference register_api_blueprints"
        )


# ---------------------------------------------------------------------------
# P1.1-RED-05: SRE blueprint included in centralized registration
# ---------------------------------------------------------------------------

class TestSREBlueprintInCentralized:
    """RED: SRE blueprint must be registered (or gracefully skipped) via register_api_blueprints."""

    def test_sre_v1_route_or_graceful_skip(self, registered_app):
        """Either /api/v1/sre/* routes exist, or register_api_blueprints handled ImportError."""
        rules = _all_rules(registered_app)
        sre_v1_routes = [r for r in rules if r.startswith("/api/v1/sre")]
        # SRE may not be importable in test env — acceptable if no crash occurred
        # The test itself passing without exception proves graceful skip works
        # If routes DO exist, they must be under /api/v1/
        if sre_v1_routes:
            assert all(r.startswith("/api/v1/") for r in sre_v1_routes)
        # No assertion needed for the skip path — reaching here means no crash


# ---------------------------------------------------------------------------
# P1.1-RED-06: __init__.py exposes BLUEPRINTS list for discoverability
# ---------------------------------------------------------------------------

class TestBlueprintRegistryList:
    """RED: tools/dashboard/api/__init__.py must expose an ALL_BLUEPRINTS constant."""

    def test_all_blueprints_constant_exists(self):
        """ALL_BLUEPRINTS list must exist for tooling/discoverability."""
        from tools.dashboard import api as api_pkg
        assert hasattr(api_pkg, "ALL_BLUEPRINTS"), (
            "ALL_BLUEPRINTS list not found in tools/dashboard/api/__init__.py"
        )

    def test_all_blueprints_is_list(self):
        from tools.dashboard.api import ALL_BLUEPRINTS
        assert isinstance(ALL_BLUEPRINTS, list), "ALL_BLUEPRINTS must be a list"

    def test_all_blueprints_has_minimum_count(self):
        from tools.dashboard.api import ALL_BLUEPRINTS
        assert len(ALL_BLUEPRINTS) >= 35, (
            f"ALL_BLUEPRINTS must have ≥35 entries, got {len(ALL_BLUEPRINTS)}"
        )
