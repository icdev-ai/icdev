# CUI // SP-CTI
"""Graceful-degradation tests for Observability Design Canvas (ODC) page routes.

Task obx-hyg-01: the five ODC page routes (oc_index, oc_templates, oc_assessments,
oc_coverage_page, oc_remediation_page) previously issued raw SELECTs with no
try/except, so any request 500'd forever when the observability store was
unreachable at startup. These tests assert that:

  * each page now returns 200 (not 500) with a visible "store unavailable" banner
    when the DB connection factory raises;
  * normal seeded operation renders 200 WITHOUT the banner;
  * the lazy-retry flag (blueprint._init_failed) clears after a successful init.

Self-contained: no conftest edits. base.html and the heavy include partials are
stubbed via a Jinja ChoiceLoader so only the ODC template bodies are exercised.
The DB factory is monkeypatched shim-aware (importlib.import_module + setattr on
the init_db module the blueprint imports get_connection from).
"""
import importlib
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

from flask import Flask
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

TEMPLATE_DIR = REPO / "tools" / "dashboard" / "templates"

BANNER_TEXT = b"Observability store unavailable"

# Minimal stubs so we render only the ODC template bodies, not the 1000-line
# base.html (which needs brand/current_user/g.tenant_name/... context) or the
# workflow / IQE / classification partials.
_STUBS = {
    "base.html": (
        "<!doctype html><html><head><title>{% block title %}{% endblock %}</title>"
        "</head><body>{% block content %}{% endblock %}"
        "{% block scripts %}{% endblock %}</body></html>"
    ),
    "includes/classification_macros.html": (
        "{% macro design_classification_badge(item=None) %}{% endmacro %}"
    ),
    "includes/workflow_trigger_btn.html": "",
    "includes/iqe_query_widget.html": "",
}


class _FakeConn:
    """Thin wrapper over a real sqlite3 connection that translates %s -> ?.

    The blueprint and init_db author SQL with PostgreSQL %s placeholders; this
    lets us exercise the real code paths against an on-disk sqlite file.
    Supports both context-manager use (`with get_connection() as conn`) and the
    explicit `conn = get_connection(); ... conn.close()` pattern.
    """

    def __init__(self, path):
        self._c = sqlite3.connect(path)
        self._c.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._c.commit()

    def close(self):
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._c.commit()
        else:
            self._c.rollback()
        self._c.close()
        return False


def _working_factory(db_path):
    def factory():
        return _FakeConn(db_path)

    return factory


def _raising_factory():
    def factory():
        raise RuntimeError("observability store unavailable (simulated)")

    return factory


def _build_app(monkeypatch, factory):
    """Patch the DB factory shim-aware, build the blueprint, mount on a Flask app."""
    monkeypatch.setenv("ICDEV_OBSERVABILITY_ENABLED", "true")

    init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")
    monkeypatch.setattr(init_db_mod, "get_connection", factory, raising=True)

    bp_mod = importlib.import_module("tools.observability_canvas.blueprint")
    # Ensure a clean starting flag; monkeypatch auto-restores after the test.
    monkeypatch.setattr(bp_mod, "_init_failed", False, raising=False)

    bp = bp_mod.create_observability_blueprint()
    assert bp is not None, "blueprint should build when ICDEV_OBSERVABILITY_ENABLED=true"

    app = Flask(__name__)
    app.secret_key = "obx-hyg-01-test"
    app.jinja_loader = ChoiceLoader(
        [DictLoader(_STUBS), FileSystemLoader(str(TEMPLATE_DIR))]
    )
    app.register_blueprint(bp, url_prefix="/observability")
    return app, bp_mod


def _authed_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "tester"
    return client


def _seed_design(db_path):
    """Insert one design row so coverage/remediation render 200 (not redirect)."""
    did = "odc-" + uuid.uuid4().hex[:8]
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO observability_designs "
        "(id, name, description, graph_json, classification, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (did, "Test Design", "d", '{"nodes":[],"edges":[]}', "CUI", "now", "now"),
    )
    conn.commit()
    conn.close()
    return did


# ── Store-unavailable: every page returns 200 with the banner ──────────────────

def test_pages_render_banner_when_store_unavailable(monkeypatch):
    app, bp_mod = _build_app(monkeypatch, _raising_factory())
    # init_db failed at creation because the factory raises.
    assert bp_mod._init_failed is True
    client = _authed_client(app)

    paths = [
        "/observability/",
        "/observability/templates",
        "/observability/assessments",
        "/observability/coverage/anything",
        "/observability/remediation/anything",
    ]
    for path in paths:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} should degrade to 200, got {resp.status_code}"
        assert BANNER_TEXT in resp.data, f"{path} missing unavailable banner"


# ── Normal seeded operation: 200, no banner ────────────────────────────────────

def test_pages_render_without_banner_when_store_available(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "oc_test.db")
        app, bp_mod = _build_app(monkeypatch, _working_factory(db_path))
        # Real init_db ran through the working factory and created the schema.
        assert bp_mod._init_failed is False
        did = _seed_design(db_path)
        client = _authed_client(app)

        paths = [
            "/observability/",
            "/observability/templates",
            "/observability/assessments",
            f"/observability/coverage/{did}",
            f"/observability/remediation/{did}",
        ]
        for path in paths:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} should render 200, got {resp.status_code}"
            assert BANNER_TEXT not in resp.data, f"{path} showed banner despite healthy store"


# ── Lazy retry: a stuck failure flag clears after a successful init ────────────

def test_lazy_retry_clears_flag_after_successful_init(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "oc_retry.db")
        app, bp_mod = _build_app(monkeypatch, _working_factory(db_path))
        # Simulate a startup that failed while the store was down: force the flag
        # true even though the factory now works.
        monkeypatch.setattr(bp_mod, "_init_failed", True, raising=False)
        client = _authed_client(app)

        resp = client.get("/observability/")
        assert resp.status_code == 200
        # First request retried init_db() successfully and cleared the flag.
        assert bp_mod._init_failed is False, "lazy retry should clear _init_failed on success"
        # Store is healthy again -> no banner.
        assert BANNER_TEXT not in resp.data


# ── Retry stays failed when the store is still down ────────────────────────────

def test_lazy_retry_keeps_flag_when_still_unavailable(monkeypatch):
    app, bp_mod = _build_app(monkeypatch, _raising_factory())
    assert bp_mod._init_failed is True
    client = _authed_client(app)

    resp = client.get("/observability/")
    assert resp.status_code == 200
    # Retry failed again -> flag remains set, banner shown.
    assert bp_mod._init_failed is True
    assert BANNER_TEXT in resp.data
