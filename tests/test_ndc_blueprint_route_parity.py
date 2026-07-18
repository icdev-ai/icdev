# [TEMPLATE: CUI // SP-CTI]
"""Route-parity guard for the Network Design Canvas blueprint split (cvx-net-01).

``tools/network/blueprint.py`` was a ~15k-line monolith whose 391 inline routes
were relocated into cohesive ``tools/network/routes/<group>.py`` modules that all
register onto the SAME shared blueprint. This test freezes the full public URL
surface (path + methods + endpoint) captured from the pre-split blueprint into
``tests/fixtures/ndc_route_inventory.json`` and asserts the assembled blueprint
still produces exactly that set — no route added, dropped, renamed, or
re-methoded by the refactor (or any future edit to the route groups).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "ndc_route_inventory.json"


def _current_inventory():
    os.environ.setdefault("ICDEV_NETWORK_ENABLED", "true")
    from flask import Flask

    from tools.network.blueprint import create_network_blueprint

    bp = create_network_blueprint()
    assert bp is not None, "create_network_blueprint returned None (disabled?)"
    app = Flask(__name__)
    app.register_blueprint(bp, url_prefix="/network")
    inv = []
    for r in app.url_map.iter_rules():
        methods = sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS"))
        inv.append({"rule": str(r.rule), "methods": methods, "endpoint": r.endpoint})
    return inv


def _key(item):
    return (item["rule"], item["endpoint"])


def test_route_inventory_matches_frozen_fixture():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = _current_inventory()

    exp_set = {(i["rule"], tuple(i["methods"]), i["endpoint"]) for i in expected}
    act_set = {(i["rule"], tuple(i["methods"]), i["endpoint"]) for i in actual}

    missing = exp_set - act_set
    added = act_set - exp_set
    assert not missing, f"routes lost by the split: {sorted(missing)}"
    assert not added, f"routes introduced by the split: {sorted(added)}"
    assert len(act_set) == len(exp_set) == len(expected)


def test_endpoints_are_unique():
    actual = _current_inventory()
    endpoints = [i["endpoint"] for i in actual]
    dupes = {e for e in endpoints if endpoints.count(e) > 1}
    assert not dupes, f"duplicate endpoints registered: {sorted(dupes)}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
