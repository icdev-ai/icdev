#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression tests for the traces / provenance / XAI dashboard API (obx-trc-03).

Covers three defects fixed in tools/dashboard/api/traces.py and
tools/dashboard/pages/provenance.py:

(a) _get_db() must detach the auto-attached Flask request security context so the
    RLS injector does not append `tenant_id = ?` to queries on otel_spans /
    shap_attributions / prov_* — those platform-observability tables have a
    classification column but NO tenant_id column, so an authenticated request
    previously produced UndefinedColumn (PG) / "no such column" (SQLite) 500s.

(b) The endpoint error handlers must catch backend-appropriate DB errors
    (sqlite3.Error AND psycopg2.Error) so a PostgreSQL failure surfaces as the
    intended JSON {"error": ...}, 500 payload instead of unhandled Flask 500 HTML.

(c) The W3C PROV-AGENT provenance_api (/api/provenance/*) and the GovChain
    blockchain provenance blueprint (now /api/govchain-provenance/*) must
    register together without a Flask blueprint-name collision, and each key
    endpoint must resolve to the correct module.

Conftest forces ICDEV_STORAGE_BACKEND=sqlite. Patching is shim-aware: modules are
resolved with importlib.import_module and patched with setattr on the same object
the Flask app imports.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
import types
from pathlib import Path

import pytest
from flask import Flask, g

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

TRACES_MOD = "tools.dashboard.api.traces"
PAGES_MOD = "tools.dashboard.pages.provenance"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx():
    """A minimal SecurityContext-shaped object that triggers RLS injection."""
    return types.SimpleNamespace(
        tenant_id="tenant-test",
        classification="CUI",
        compartments=frozenset(),
        role="",
    )


def _seed_otel_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS otel_spans (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            parent_span_id TEXT,
            name TEXT NOT NULL,
            kind TEXT DEFAULT 'INTERNAL',
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_ms INTEGER DEFAULT 0,
            status_code TEXT DEFAULT 'UNSET',
            status_message TEXT,
            attributes TEXT,
            events TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        "INSERT INTO otel_spans (id, trace_id, name, start_time, end_time, duration_ms) "
        "VALUES ('span-1', 'trace-abc', 'mcp.tool_call', '2026-07-18T00:00:00', "
        "'2026-07-18T00:00:01', 1000)"
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def traces_app(tmp_path, monkeypatch):
    """Flask app with traces_api mounted, backed by a seeded temp otel_spans DB.

    ICDEV_DB_PATH is pointed at the temp DB so get_connection() treats it as the
    *main* DB and therefore auto-attaches the Flask security context (an auxiliary
    .db path would skip RLS and mask the very bug under test — see storage.py
    get_connection()).
    """
    db_path = tmp_path / "icdev.db"
    _seed_otel_db(db_path)
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    traces_mod = importlib.import_module(TRACES_MOD)
    monkeypatch.setattr(traces_mod, "DB_PATH", db_path, raising=True)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(traces_mod.traces_api)
    return app, traces_mod, db_path


# ---------------------------------------------------------------------------
# (a) RLS bypass — authenticated request must not 500
# ---------------------------------------------------------------------------

def test_rls_injection_hazard_is_real(traces_app):
    """Sanity: with a security context attached, a raw RLS-enabled connection
    DOES fail on otel_spans (no tenant_id column). Proves the bug premise so the
    'does not 500' test below is meaningful and not vacuous."""
    _, _, db_path = traces_app
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path))
    conn.set_security_context(_make_ctx())
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT * FROM otel_spans").fetchall()
    conn.close()


def test_authenticated_list_traces_does_not_500(traces_app):
    """(a) GET /api/traces/ under an authenticated security context returns 200,
    not a 500 from RLS predicate injection on the tenant-less otel_spans table."""
    app, _, _ = traces_app

    @app.before_request
    def _attach_ctx():  # noqa: ANN202
        g.security_context = _make_ctx()

    client = app.test_client()
    resp = client.get("/api/traces/")
    assert resp.status_code == 200, (
        f"expected 200, got {resp.status_code}: {resp.get_data(as_text=True)[:300]}"
    )
    body = resp.get_json()
    assert body is not None
    assert "traces" in body
    trace_ids = {t["trace_id"] for t in body["traces"]}
    assert "trace-abc" in trace_ids


# ---------------------------------------------------------------------------
# (b) DB error surfaces as JSON, not unhandled HTML 500
# ---------------------------------------------------------------------------

def test_db_errors_tuple_includes_psycopg2():
    """(b) _DB_ERRORS must include psycopg2.Error when psycopg2 is importable."""
    psycopg2 = pytest.importorskip("psycopg2")
    traces_mod = importlib.import_module(TRACES_MOD)
    assert psycopg2.Error in traces_mod._DB_ERRORS
    assert sqlite3.Error in traces_mod._DB_ERRORS


def test_pg_db_error_surfaces_as_json(traces_app):
    """(b) A psycopg2.Error raised during the query is caught and returned as a
    JSON {"error": ...}, 500 — not an unhandled Flask 500 HTML page."""
    psycopg2 = pytest.importorskip("psycopg2")
    app, traces_mod, _ = traces_app

    class _BoomConn:
        def execute(self, *_a, **_k):
            raise psycopg2.OperationalError("simulated PG failure")

        def close(self):
            pass

    monkeypatch_target = "_get_db"
    original = getattr(traces_mod, monkeypatch_target)
    setattr(traces_mod, monkeypatch_target, lambda: _BoomConn())
    try:
        client = app.test_client()
        resp = client.get("/api/traces/")
        assert resp.status_code == 500
        body = resp.get_json()
        assert body is not None, "PG error escaped as HTML instead of JSON"
        assert "error" in body
        assert "simulated PG failure" in body["error"]
    finally:
        setattr(traces_mod, monkeypatch_target, original)


# ---------------------------------------------------------------------------
# (c) Blueprint collision regression
# ---------------------------------------------------------------------------

def _rule_endpoint(app: Flask, path: str) -> str:
    for rule in app.url_map.iter_rules():
        if rule.rule == path:
            return rule.endpoint
    raise AssertionError(f"no rule registered for {path!r}; rules={sorted(r.rule for r in app.url_map.iter_rules())}")


def test_provenance_blueprints_register_without_collision():
    """(c) The W3C PROV-AGENT blueprint and the GovChain blueprint must both
    register on one app without a Flask name collision, with distinct names."""
    traces_mod = importlib.import_module(TRACES_MOD)
    pages_mod = importlib.import_module(PAGES_MOD)

    prov_agent_bp = traces_mod.provenance_api
    govchain_bp = pages_mod.govchain_provenance_api

    assert prov_agent_bp.name == "provenance_api"
    assert govchain_bp.name == "govchain_provenance_api"
    assert prov_agent_bp.name != govchain_bp.name

    app = Flask(__name__)
    app.config["TESTING"] = True
    # Must not raise "blueprint name ... already registered".
    app.register_blueprint(prov_agent_bp)
    app.register_blueprint(govchain_bp)


def test_provenance_endpoints_resolve_to_correct_module():
    """(c) /api/provenance/* resolves to the W3C PROV-AGENT blueprint and
    /api/govchain-provenance/* resolves to the GovChain blueprint."""
    traces_mod = importlib.import_module(TRACES_MOD)
    pages_mod = importlib.import_module(PAGES_MOD)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(traces_mod.provenance_api)
    app.register_blueprint(pages_mod.govchain_provenance_api)

    # W3C PROV-AGENT lineage endpoints stay on /api/provenance
    assert _rule_endpoint(app, "/api/provenance/entities").startswith("provenance_api.")
    assert _rule_endpoint(app, "/api/provenance/activities").startswith("provenance_api.")

    # GovChain blockchain endpoints move to /api/govchain-provenance
    assert _rule_endpoint(app, "/api/govchain-provenance/blockchain-status").startswith(
        "govchain_provenance_api."
    )
    assert _rule_endpoint(app, "/api/govchain-provenance/verify").startswith(
        "govchain_provenance_api."
    )

    # The two surfaces do not share any concrete route rule.
    prov_rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/api/provenance")}
    gov_rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/api/govchain-provenance")}
    assert prov_rules and gov_rules
    assert prov_rules.isdisjoint(gov_rules)
