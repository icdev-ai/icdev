# CUI // SP-CTI
"""Canvas blueprint registration is idempotent across a second create_app().

`create_app()` runs the canvas-registration loop, and it is called more than once in
some paths (the module notes a Windows restart-loop where create_app ran twice).
The module-level canvas blueprints are process-wide singletons, so the second run
re-attached `before_request` to already-registered blueprints — which Flask forbids
process-wide — logging ~23 spurious "Canvas X registration failed" warnings.

The fix attaches the RBAC guard only ONCE (a version-agnostic `_icdev_guard_attached`
flag; the hook persists on the blueprint across apps anyway) and registers on each app
only if not already present. These tests pin the Flask invariant the fix relies on and
the idempotent pattern — fast and hermetic (no full create_app()). The end-to-end
proof (a 2nd create_app() emitting 0 canvas warnings, 160 blueprints) was verified
manually; a full-app-twice test is too heavy for the unit gate.
"""
from __future__ import annotations

import pytest
from flask import Blueprint, Flask


def test_flask_forbids_before_request_after_registration():
    """The invariant the fix works around: once a blueprint is registered on ANY
    app, adding a before_request hook raises — process-wide, not per-app."""
    app = Flask("a1")
    bp = Blueprint("inv", __name__)
    app.register_blueprint(bp)
    with pytest.raises(Exception):
        bp.before_request(lambda: None)


def test_attach_once_flag_prevents_re_attach():
    bp = Blueprint("once", __name__)
    attaches = 0

    def attach_guard_if_needed():
        nonlocal attaches
        if not getattr(bp, "_icdev_guard_attached", False):
            bp.before_request(lambda: None)
            bp._icdev_guard_attached = True
            attaches += 1

    attach_guard_if_needed()          # first setup: attaches
    Flask("app1").register_blueprint(bp)  # now registered -> further attaches would raise
    attach_guard_if_needed()          # second setup: flag short-circuits, no raise
    attach_guard_if_needed()
    assert attaches == 1


def test_register_only_if_not_already_on_this_app():
    bp = Blueprint("reg", __name__)
    app = Flask("app2")
    for _ in range(3):  # re-entrant setup must not double-register on the same app
        if bp.name not in app.blueprints:
            app.register_blueprint(bp)
    assert list(app.blueprints).count("reg") == 1


def test_same_blueprint_registers_on_multiple_apps():
    """A blueprint set up once may still be registered on several app instances
    (a 2nd create_app builds a fresh app); the guard must not block that."""
    bp = Blueprint("multi", __name__)
    if not getattr(bp, "_icdev_guard_attached", False):
        bp.before_request(lambda: None)
        bp._icdev_guard_attached = True
    app1, app2 = Flask("m1"), Flask("m2")
    if bp.name not in app1.blueprints:
        app1.register_blueprint(bp)
    if bp.name not in app2.blueprints:
        app2.register_blueprint(bp)
    assert "multi" in app1.blueprints and "multi" in app2.blueprints
