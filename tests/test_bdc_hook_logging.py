# CUI // SP-CTI
"""Tests for silent side-effect hook logging in tools/boundary_canvas/blueprint.py — task bdr-hyg-1.

The boundary canvas blueprint fires several best-effort side-effect hooks
(security-canvas validation, event bus publish, KG rebuild, blockchain
provenance, audit write, graph-diff summary). Each hook is non-fatal, but a
failure must no longer be swallowed silently — it must emit a ``logger.warning``
so operators can see it. Coverage:

  * A representative hook (security-canvas ``on_bdc_design_saved``) forced to
    raise emits a WARNING record AND the PUT route still returns 200.
  * Static guard: blueprint.py contains zero ``except Exception:`` handlers whose
    body is a bare ``pass`` (parsed with ast).
"""
from __future__ import annotations

import ast
import logging


class _FakeConn:
    """Minimal context-manager connection: canned truthy row, no-op writes."""

    def __init__(self, design_id):
        self._design_id = design_id
        self._sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self._sql = sql
        return self

    def fetchone(self):
        if "boundary_designs" in self._sql:
            return {"id": self._design_id}
        return None

    def close(self):
        pass


def _build_client(monkeypatch, conn):
    monkeypatch.setenv("ICDEV_BOUNDARY_ENABLED", "true")
    # Ensure the submodule is imported so its attribute exists for patching.
    import tools.boundary_canvas.db.init_db  # noqa: F401
    monkeypatch.setattr(
        "tools.boundary_canvas.db.init_db.get_connection", lambda *a, **k: conn
    )
    from flask import Flask
    from tools.boundary_canvas.blueprint import create_boundary_blueprint

    bp = create_boundary_blueprint()
    assert bp is not None
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(bp, url_prefix="/boundary")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-admin"
    return client


def test_security_canvas_hook_failure_logs_warning_and_route_succeeds(monkeypatch, caplog):
    """When the security-canvas hook raises, the route stays 200 and a warning fires."""
    from tools.logging.icdev_logger import get_logger

    design_id = "design-1"
    client = _build_client(monkeypatch, _FakeConn(design_id))

    # The route imports these hooks lazily via `from tools.x import y`. Under the
    # tools.*->icdev.tools.* shim, string-path monkeypatch can't walk the not-yet-
    # imported submodule, so import each module and patch the object directly.
    import importlib

    def _patch(module_path, attr, value):
        mod = importlib.import_module(module_path)
        monkeypatch.setattr(mod, attr, value)

    # Unguarded post-update hook — neutralize so it can't 500 the route.
    _patch("tools.knowledge_graph.canvas_ask", "reindex_canvas_on_save", lambda *a, **k: None)
    # Neutralize the other best-effort hooks so only the representative one raises.
    _patch("tools.canvas.event_bus", "publish", lambda *a, **k: None)
    _patch("tools.canvas.kg_builder", "rebuild_canvas_kg", lambda *a, **k: None)
    _patch("tools.canvas.provenance", "register_canvas_provenance", lambda *a, **k: None)

    # Representative hook: force it to raise.
    def _boom(*a, **k):
        raise RuntimeError("boundary validation exploded")

    _patch("tools.security_canvas.agent", "on_bdc_design_saved", _boom)

    # The component logger disables propagation; re-enable so caplog (root handler)
    # captures the record. monkeypatch auto-restores it after the test.
    bdc_logger = get_logger("icdev.boundary_canvas")
    monkeypatch.setattr(bdc_logger, "propagate", True)

    with caplog.at_level(logging.WARNING, logger="icdev.boundary_canvas"):
        resp = client.put(
            f"/boundary/api/designs/{design_id}",
            json={"name": "Renamed"},
        )

    assert resp.status_code == 200
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and "security-canvas boundary validation hook failed" in r.getMessage()
    ]
    assert warnings, "expected a WARNING record for the failed security-canvas hook"
    # Non-fatal: exception detail is attached via exc_info for triage.
    assert warnings[0].exc_info is not None


def test_no_silent_exception_pass_blocks():
    """blueprint.py must contain zero ``except Exception:`` handlers with a bare pass body."""
    import tools.boundary_canvas.blueprint as bp_mod

    source = open(bp_mod.__file__, "r", encoding="utf-8").read()
    tree = ast.parse(source)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # Only care about handlers that catch Exception (or bare except).
        caught = node.type
        is_exception = (
            caught is None
            or (isinstance(caught, ast.Name) and caught.id == "Exception")
        )
        if not is_exception:
            continue
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            offenders.append(node.lineno)

    assert not offenders, (
        f"silent `except Exception: pass` blocks remain at lines {offenders}"
    )
