#!/usr/bin/env python3
# CUI // SP-CTI
"""nav-misc-03 — route-inventory regression test for the app.py decomposition.

The pulse / research / clawhub route groups were extracted verbatim from the
~11.3k-line ``tools/dashboard/app.py`` create_app() closure into dedicated
inline-route blueprints:

  * tools/dashboard/api/pulse.py     -> pulse_api      (/pulse, /api/pulse/*)
  * tools/dashboard/api/research.py  -> research_api   (/api/research/*)
  * tools/dashboard/api/clawhub.py   -> clawhub_api    (/api/clawhub/*)

The extraction MUST be behaviour-preserving. This test pins the (rule, methods)
inventory of those groups against a baseline captured from ``origin/main`` BEFORE
the move (``tests/fixtures/nav_misc_03_extracted_routes.json``) and asserts:

  1. The current app serves EXACTLY the same (rule, methods) set for each group
     (nothing lost, nothing added) — i.e. identical rule+methods sets.
  2. Each of those rules is now served by the new blueprint endpoint
     (endpoint name gains a ``<blueprint>.`` prefix — documented, expected).

NIST 800-53: CM-3 (Configuration Change Control), SA-11 (Developer Testing).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "nav_misc_03_extracted_routes.json"

# group key in the fixture -> blueprint name that must now own the routes
GROUP_BLUEPRINT = {
    "pulse": "pulse_api",
    "research": "research_api",
    "clawhub": "clawhub_api",
}


def _load_baseline() -> dict[str, set[tuple[str, tuple[str, ...]]]]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {
        grp: {(rule, tuple(methods)) for rule, methods in rules}
        for grp, rules in raw.items()
    }


@pytest.fixture(scope="module")
def registered_app():
    """Bare Flask app with all API blueprints registered (no DB / no full app factory)."""
    from flask import Flask

    from tools.dashboard.api import register_api_blueprints

    app = Flask(__name__)
    app.config["TESTING"] = True
    register_api_blueprints(app)
    return app


def _current_by_endpoint_prefix(app, prefix: str) -> set[tuple[str, tuple[str, ...]]]:
    out = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith(prefix + "."):
            methods = tuple(sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS")))
            out.add((rule.rule, methods))
    return out


def test_fixture_present():
    assert FIXTURE.exists(), f"baseline fixture missing: {FIXTURE}"
    baseline = _load_baseline()
    assert set(baseline) == set(GROUP_BLUEPRINT), baseline.keys()
    # Sanity: the baseline captured 41 + 12 + 12 = 65 routes from origin/main.
    assert len(baseline["pulse"]) == 41
    assert len(baseline["research"]) == 12
    assert len(baseline["clawhub"]) == 12


@pytest.mark.parametrize("group", sorted(GROUP_BLUEPRINT))
def test_extracted_route_set_identical(registered_app, group):
    """The new blueprint serves EXACTLY the pre-extraction (rule, methods) set."""
    baseline = _load_baseline()[group]
    current = _current_by_endpoint_prefix(registered_app, GROUP_BLUEPRINT[group])

    lost = baseline - current
    added = current - baseline
    assert not lost, f"{group}: routes lost in extraction: {sorted(lost)}"
    assert not added, f"{group}: unexpected routes added to blueprint: {sorted(added)}"
    assert current == baseline


def test_all_extracted_routes_owned_by_blueprints(registered_app):
    """Every pulse/research/clawhub URL is served by its blueprint, not a bare app route.

    Guards against a route being accidentally left behind in app.py (which would
    show a bare endpoint name with no ``<blueprint>.`` prefix).
    """
    baseline = _load_baseline()
    all_rules = {rule.rule: rule.endpoint for rule in registered_app.url_map.iter_rules()}
    offenders = []
    for group, rules in baseline.items():
        bp = GROUP_BLUEPRINT[group]
        for rule, _methods in rules:
            ep = all_rules.get(rule)
            if ep is None or not ep.startswith(bp + "."):
                offenders.append((rule, ep))
    assert not offenders, f"routes not owned by expected blueprint: {offenders}"
