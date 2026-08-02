# CUI // SP-CTI
"""Route + integration tests for the MITRE signal-source coverage twin (obx-cov-01).

Un-orphans ``tools.observability_canvas.mitre_coverage_twin``: before this task,
``compute_gap_score`` / ``generate_gap_report`` had no runtime callers, so the
only writer of ``odc_gap_scores`` never fired and the ODC compliance card
(``tools.canvas_compliance.compliance.get_odc_card``) stayed empty in production.

These tests prove the wiring end to end:

  * ``POST /api/coverage/<id>/compute`` persists ``odc_gap_scores`` — readable
    both through the ODC canvas connection AND through ``get_odc_card`` (the card
    finally lights up: available flips True with a real coverage %).
  * ``GET  /api/coverage/<id>/report`` returns the gap-report shape
    (remediation_steps + quick_wins).
  * The assess route runs coverage as a GUARDED side effect: when
    ``compute_gap_score`` raises, ``POST /api/designs/<id>/assess`` still 200s.
  * Both coverage routes require auth (401 unauthenticated).

Harness mirrors tests/test_odc_graceful_pages.py: a translating _FakeConn over an
on-disk sqlite file, the DB factory monkeypatched shim-aware
(importlib.import_module + setattr on the init_db module every caller imports
get_connection from), so both the blueprint routes and get_odc_card share one DB.
No conftest edit: the ODC canvas owns its own DB and init_db() creates the
odc_gap_scores / odc_technique_coverage schema.
"""
import importlib
import json
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from flask import Flask
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

TEMPLATE_DIR = REPO / "tools" / "dashboard" / "templates"

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
    """Thin sqlite3 wrapper translating PostgreSQL %s placeholders to ?.

    Supports both `with get_connection() as conn` and the explicit
    `conn = get_connection(); ...; conn.close()` patterns the blueprint uses.
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


# Rich signal-source graph: enough src-* node types that at least one MITRE
# technique in build_coverage_catalog() is fully covered (covered_count > 0),
# so the card shows a non-zero, non-"n/a" coverage %.
_RICH_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "src-os-log", "label": "OS Logs"},
        {"id": "n2", "type": "src-endpoint", "label": "EDR"},
        {"id": "n3", "type": "src-container-log", "label": "Container Logs"},
        {"id": "n4", "type": "src-iam", "label": "IAM Logs"},
        {"id": "n5", "type": "src-cloud-log", "label": "Cloud Audit"},
        {"id": "n6", "type": "src-network-log", "label": "Network Logs"},
        {"id": "n7", "type": "src-flow", "label": "NetFlow"},
        {"id": "n8", "type": "src-app-log", "label": "App Logs"},
        {"id": "n9", "type": "plt-splunk", "label": "Splunk SIEM"},
    ],
    "edges": [],
}


def _build_app(monkeypatch, db_path):
    """Patch the DB factory shim-aware, build the blueprint, mount on a Flask app."""
    monkeypatch.setenv("ICDEV_OBSERVABILITY_ENABLED", "true")

    init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")
    monkeypatch.setattr(init_db_mod, "get_connection", _working_factory(db_path), raising=True)

    bp_mod = importlib.import_module("tools.observability_canvas.blueprint")
    monkeypatch.setattr(bp_mod, "_init_failed", False, raising=False)

    bp = bp_mod.create_observability_blueprint()
    assert bp is not None

    app = Flask(__name__)
    app.secret_key = "obx-cov-01-test"
    app.jinja_loader = ChoiceLoader([DictLoader(_STUBS), FileSystemLoader(str(TEMPLATE_DIR))])
    app.register_blueprint(bp, url_prefix="/observability")
    return app, bp_mod


def _authed_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "tester"
    return client


def _seed_design(db_path, graph=None):
    did = "odc-" + uuid.uuid4().hex[:8]
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO observability_designs "
        "(id, name, description, graph_json, classification, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (did, "Coverage Test Design", "d",
         json.dumps(graph if graph is not None else _RICH_GRAPH), "CUI", "now", "now"),
    )
    conn.commit()
    conn.close()
    return did


@pytest.fixture
def odc_env(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "oc_cov.db")
        app, bp_mod = _build_app(monkeypatch, db_path)
        yield app, db_path


# ── compute route persists + card lights up ──────────────────────────────────


def test_compute_persists_gap_scores_and_lights_up_card(odc_env):
    app, db_path = odc_env
    did = _seed_design(db_path)
    client = _authed_client(app)

    resp = client.post(f"/observability/api/coverage/{did}/compute", json={})
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["design_id"] == did
    assert body["total_techniques"] > 0
    assert "gap_score" in body
    assert body["covered_count"] > 0, "rich signal-source graph should cover >=1 technique"

    # Persisted and readable through the canvas connection.
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    rows = raw.execute(
        "SELECT total_techniques, covered_count, gap_count FROM odc_gap_scores WHERE design_id=?",
        (did,),
    ).fetchall()
    tc_rows = raw.execute(
        "SELECT COUNT(*) AS c FROM odc_technique_coverage WHERE design_id=?", (did,)
    ).fetchone()
    raw.close()
    assert len(rows) == 1
    assert rows[0]["total_techniques"] == body["total_techniques"]
    assert tc_rows["c"] == body["total_techniques"], "per-technique rows persisted too"

    # The ODC compliance card finally lights up (reads the SAME patched factory).
    compliance = importlib.import_module("tools.canvas_compliance.compliance")
    card = compliance.get_odc_card()
    assert card["available"] is True, "card must be available once odc_gap_scores has a row"
    metrics = {m["label"]: m["value"] for m in card["metrics"]}
    assert metrics["MITRE Coverage"] != "n/a"
    assert str(metrics["MITRE Coverage"]).endswith("%")


def test_compute_unknown_design_404(odc_env):
    app, _ = odc_env
    client = _authed_client(app)
    resp = client.post("/observability/api/coverage/does-not-exist/compute", json={})
    assert resp.status_code == 404


# ── report route shape ───────────────────────────────────────────────────────


def test_report_shape(odc_env):
    app, db_path = odc_env
    did = _seed_design(db_path)
    client = _authed_client(app)

    resp = client.get(f"/observability/api/coverage/{did}/report")
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    for key in ("gap_score", "coverage_by_technique", "remediation_steps",
                "quick_wins", "total_remediation_items"):
        assert key in body, f"report missing {key}"
    assert isinstance(body["remediation_steps"], list)
    assert isinstance(body["quick_wins"], list)


# ── assess integration is a GUARDED side effect ──────────────────────────────


def test_assess_writes_coverage(odc_env):
    app, db_path = odc_env
    did = _seed_design(db_path)
    client = _authed_client(app)

    resp = client.post(f"/observability/api/designs/{did}/assess", json={})
    assert resp.status_code == 200

    raw = sqlite3.connect(db_path)
    n = raw.execute("SELECT COUNT(*) FROM odc_gap_scores WHERE design_id=?", (did,)).fetchone()[0]
    raw.close()
    assert n == 1, "assess should have triggered a coverage compute"


def test_assess_survives_coverage_failure(odc_env, monkeypatch):
    app, db_path = odc_env
    did = _seed_design(db_path)
    client = _authed_client(app)

    # Force the coverage compute to blow up; the assessment must still succeed.
    twin = importlib.import_module("tools.observability_canvas.mitre_coverage_twin")

    def _boom(*a, **k):
        raise RuntimeError("coverage engine exploded")

    monkeypatch.setattr(twin, "compute_gap_score", _boom, raising=True)

    resp = client.post(f"/observability/api/designs/{did}/assess", json={})
    assert resp.status_code == 200, "assess must not fail when coverage compute raises"

    raw = sqlite3.connect(db_path)
    n = raw.execute("SELECT COUNT(*) FROM odc_gap_scores WHERE design_id=?", (did,)).fetchone()[0]
    raw.close()
    assert n == 0, "no gap score persisted when compute raised (guarded, non-fatal)"


# ── auth required ────────────────────────────────────────────────────────────


def test_compute_requires_auth(odc_env):
    app, db_path = odc_env
    did = _seed_design(db_path)
    client = app.test_client()  # no session
    resp = client.post(f"/observability/api/coverage/{did}/compute", json={})
    assert resp.status_code == 401


def test_report_requires_auth(odc_env):
    app, db_path = odc_env
    did = _seed_design(db_path)
    client = app.test_client()  # no session
    resp = client.get(f"/observability/api/coverage/{did}/report")
    assert resp.status_code == 401
