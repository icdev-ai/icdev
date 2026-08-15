#!/usr/bin/env python3
"""A service-key caller must reach the Cortex API. CUI // SP-CTI

THE DEFECT. `tools/cortex/rest_v1.py::register_rest_v1` attaches
/cortex/api/v1/* to the SAME blueprint as the Cortex web canvas, deliberately
("so the machine surface and the web canvas share a single Blueprint"). The
canvas-access guard is attached blueprint-wide in dashboard/app.py, so it runs
for the machine surface too — and applies a HUMAN's authorization model (canvas
grants) to a MACHINE principal.

It failed totally, not partially. tools/dashboard/auth.py sets
``role: "service"`` with the key's tenant for every Cortex service-key caller,
and check_access() is False for that principal on every tenant — measured
2026-08-15 across service/admin/user x compass/idea_lab, all False, with
``canvas_access_grants`` holding ZERO rows. Canvas enforcement is fail-closed by
DEFAULT (cnr-plat-03). So every external call to the Cortex REST surface got a
bare HTML 403 from a before_request: it never reached the endpoint, never reached
its scope check, and never produced the JSON envelope tools/cortex/client.py
parses — while that surface exists precisely to be called by compass and
idea_lab.

WHY IT WAS INVISIBLE. The guard is only attached once tools.dashboard.app is
imported, and it then persists on the shared blueprint. So tests/cortex/test_rest_*
pass when run alone and fail in-suite behind any module that imports the
dashboard — 105 failures across 11 files, none of them in
args/ci_test_files/core.txt, so neither the Test nor the Test (PostgreSQL) job
ever ran them.

Deterministic: no DB, no network. The binding and the grant lookup are injected.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.security import canvas_access  # noqa: E402


def _app_with_guard(monkeypatch, *, binding, tenant_id="compass",
                    role="service", grant=False):
    """A bare app carrying only the canvas guard, so nothing else can 403."""
    from flask import Flask, g, jsonify

    monkeypatch.setattr(canvas_access, "check_access",
                        lambda *a, **kw: grant)
    monkeypatch.setattr(canvas_access, "_canvas_access_enforced", lambda: True)

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = {"id": "u1", "role": role, "tenant_id": tenant_id}
        if binding is not None:
            g.cortex_binding = binding

    app.before_request(canvas_access.guard_component_access("cortex", "IL2"))

    @app.route("/cortex/api/v1/agent", methods=["POST"])
    def _agent():
        return jsonify({"reached": True})

    return app.test_client()


_BINDING = {"scopes": ["cortex:agent"], "key_id": "k1", "label": "compass",
            "tenant_id": "compass"}


# --------------------------------------------------------------------------- #
# THE regression
# --------------------------------------------------------------------------- #

def test_a_service_key_caller_reaches_the_endpoint(monkeypatch):
    """The whole defect in one assertion.

    `grant=False` is not a contrived fixture — it is the measured state of the
    platform: canvas_access_grants is empty and no role resolves to a grant.
    """
    c = _app_with_guard(monkeypatch, binding=_BINDING, grant=False)
    resp = c.post("/cortex/api/v1/agent", json={"goal": "x"})
    assert resp.status_code == 200, (
        "a service key with scopes was denied by a canvas GRANT check that "
        "structurally cannot pass for a machine principal"
    )
    assert resp.get_json()["reached"] is True


def test_the_denial_was_a_bare_html_403_not_a_json_envelope(monkeypatch):
    """Why the symptom was confusing: the client parses JSON and got HTML.

    Without a binding the guard still denies, and it aborts rather than
    returning the API's error shape — so a caller sees a Werkzeug HTML page
    where its contract promises {"error", "code"}.
    """
    c = _app_with_guard(monkeypatch, binding=None, grant=False)
    resp = c.post("/cortex/api/v1/agent", json={"goal": "x"})
    assert resp.status_code == 403
    assert resp.get_json() is None
    assert b"<!doctype html>" in resp.data.lower()


# --------------------------------------------------------------------------- #
# The exemption must not become a hole
# --------------------------------------------------------------------------- #

def test_no_binding_still_goes_through_the_grant_check(monkeypatch):
    """The exemption keys on the BINDING, so everything else is unchanged."""
    denied = _app_with_guard(monkeypatch, binding=None, grant=False)
    assert denied.post("/cortex/api/v1/agent", json={}).status_code == 403

    allowed = _app_with_guard(monkeypatch, binding=None, grant=True)
    assert allowed.post("/cortex/api/v1/agent", json={}).status_code == 200


def test_the_exemption_requires_an_actual_binding(monkeypatch):
    """A falsy-but-present attribute must not be mistaken for a binding.

    getattr(...) is not None, not a truthiness test: an empty dict is still a
    resolved binding, whereas absence means auth.py never matched a service key.
    """
    c = _app_with_guard(monkeypatch, binding={}, grant=False)
    assert c.post("/cortex/api/v1/agent", json={}).status_code == 200


def test_an_unauthenticated_caller_is_still_refused(monkeypatch):
    """No principal at all must keep failing closed, binding or not."""
    from flask import Flask, g, jsonify

    monkeypatch.setattr(canvas_access, "check_access", lambda *a, **kw: False)
    monkeypatch.setattr(canvas_access, "_canvas_access_enforced", lambda: True)

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _no_auth():
        g.current_user = {}

    app.before_request(canvas_access.guard_component_access("cortex", "IL2"))

    @app.route("/cortex/api/v1/agent", methods=["POST"])
    def _agent():
        return jsonify({"reached": True})

    resp = app.test_client().post("/cortex/api/v1/agent", json={})
    assert resp.status_code in (401, 403)


def test_the_exemption_is_keyed_on_the_binding_not_a_path_prefix():
    """A path allowlist is the 'enumerate the paths' antipattern.

    The binding is the security FACT — it exists only when auth.py resolved a
    service key, and rest_v1._scope_denied then requires cortex:<operation> from
    that key's row. A URL prefix is a guess about which routes are machine
    routes, and goes stale the moment one is added.
    """
    import inspect

    src = inspect.getsource(canvas_access.guard_component_access)
    assert 'getattr(g, "cortex_binding", None) is not None' in src
    # Strip comments first: the rationale above legitimately NAMES the path it
    # is deliberately not matching on, and asserting over prose would forbid
    # explaining the decision.
    code = " ".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "/cortex/api/v1" not in code, "must not hard-code the API prefix in the LOGIC"
    # The guard LOGS request.path, which is fine and useful. What must not
    # happen is DECIDING on it — a URL test is the guess this exemption avoids.
    assert "request.path.startswith" not in code, "must not branch on the URL"
    assert "request.path ==" not in code, "must not branch on the URL"


def test_scope_enforcement_still_exists_for_the_exempted_principal():
    """Removing the grant check is only safe because scopes are enforced.

    If _scope_denied ever stopped running, this exemption would become a real
    hole — so the two are asserted together.
    """
    import inspect

    from tools.cortex import rest_v1

    src = inspect.getsource(rest_v1._cortex_api)
    assert "_scope_denied(operation)" in src
    scope_src = inspect.getsource(rest_v1._scope_denied)
    assert 'f"cortex:{operation}"' in scope_src
    assert "binding.get(\"scopes\")" in scope_src


@pytest.mark.parametrize("min_il", ["IL2", "IL4", "IL5"])
def test_the_tier_gate_still_applies_to_machine_callers(min_il):
    """The exemption sits AFTER the deployment-wide tier gate, not before it.

    A licensing/tier gate is not a per-principal grant and must keep applying.
    """
    import inspect

    src = inspect.getsource(canvas_access.guard_component_access)
    tier_at = src.find("Tier gate")
    exempt_at = src.find('getattr(g, "cortex_binding", None) is not None')
    assert tier_at != -1 and exempt_at != -1
    assert tier_at < exempt_at
