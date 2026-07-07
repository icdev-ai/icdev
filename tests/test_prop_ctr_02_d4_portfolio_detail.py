# CUI // SP-CTI
"""Tests for prop-ctr-02-d4: GET /api/cpmp/portfolio/<id> detail view.

Verifies the endpoint nests an obligation_summary section (obligated_value,
funded_value, burn_rate_pct) alongside the base/option period breakdown for a
single contract, on top of the base contract fields.
"""
from __future__ import annotations

import json
import uuid

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """In-memory SQLite DB with CPMP tables needed for contract + period lookups."""
    import sqlite3

    db_file = str(tmp_path / "test_cpmp_portfolio_detail.db")
    monkeypatch.setenv("ICDEV_DB_PATH", db_file)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_trail "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, actor TEXT, "
        "action TEXT, details TEXT, session_id TEXT, created_at TEXT DEFAULT (datetime('now')))"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cpmp_contracts (
            id TEXT PRIMARY KEY,
            contract_number TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            agency TEXT NOT NULL DEFAULT '',
            contract_type TEXT NOT NULL DEFAULT 'FFP',
            total_value REAL DEFAULT 0.0,
            funded_value REAL DEFAULT 0.0,
            obligated_value REAL DEFAULT 0.0,
            ceiling_value REAL,
            billed_value REAL DEFAULT 0.0,
            pop_start TEXT,
            pop_end TEXT,
            period_type TEXT DEFAULT 'base',
            option_number INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            health TEXT DEFAULT 'green',
            health_score REAL,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cpmp_clins (
            id TEXT PRIMARY KEY,
            contract_id TEXT NOT NULL,
            clin_number TEXT NOT NULL DEFAULT '',
            billed_value REAL DEFAULT 0.0,
            funded_value REAL DEFAULT 0.0,
            total_value REAL DEFAULT 0.0,
            classification TEXT DEFAULT 'CUI'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cpmp_contract_periods (
            id TEXT PRIMARY KEY,
            contract_id TEXT NOT NULL,
            period_type TEXT NOT NULL DEFAULT 'base',
            option_number INTEGER DEFAULT 0,
            pop_start TEXT,
            pop_end TEXT,
            obligated_value REAL DEFAULT 0.0,
            funded_value REAL DEFAULT 0.0,
            ceiling_value REAL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'unexercised',
            exercised_at TEXT,
            exercised_by TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            classification TEXT DEFAULT 'CUI',
            tenant_id TEXT
        )
    """)

    # Tables get_contract() enriches with (empty is fine)
    conn.execute("CREATE TABLE IF NOT EXISTS cpmp_wbs (id TEXT, contract_id TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS cpmp_deliverables (id TEXT, contract_id TEXT, status TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS cpmp_subcontractors (id TEXT, contract_id TEXT)")

    conn.commit()
    conn.close()
    return db_file


@pytest.fixture()
def contract_id(db):
    """Insert a sample active contract with billed CLINs, return its id."""
    import sqlite3

    cid = str(uuid.uuid4())
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, agency, status, total_value, funded_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, "W911QX-25-C-0042", "Test Services Contract", "Army", "active", 5_000_000, 2_000_000),
    )
    conn.execute(
        "INSERT INTO cpmp_clins (id, contract_id, clin_number, billed_value, funded_value, total_value) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), cid, "0001", 500_000, 2_000_000, 2_000_000),
    )
    conn.commit()
    conn.close()
    return cid


@pytest.fixture()
def flask_app(db, monkeypatch):
    """Minimal Flask test client with cpmp_api blueprint."""
    monkeypatch.setenv("ICDEV_DB_PATH", db)
    from tools.dashboard.api.cpmp import cpmp_api
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _inject_user():
        from flask import g
        g.current_user = {"username": "test_co", "role": "admin", "email": "co@test.mil", "classification": "CUI"}

    app.register_blueprint(cpmp_api)
    return app.test_client()


def test_portfolio_detail_not_found(flask_app):
    r = flask_app.get("/api/cpmp/portfolio/nonexistent-id")
    assert r.status_code == 404
    data = json.loads(r.data)
    assert data["status"] == "error"


def test_portfolio_detail_no_periods_falls_back_to_contract_values(flask_app, contract_id):
    """No cpmp_contract_periods rows yet — obligation summary falls back to funded_value."""
    r = flask_app.get(f"/api/cpmp/portfolio/{contract_id}")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["status"] == "ok"
    assert data["contract"]["id"] == contract_id

    summary = data["obligation_summary"]
    assert summary["funded_value"] == 2_000_000
    assert summary["obligated_value"] == pytest.approx(2_000_000)
    assert summary["billed_value"] == pytest.approx(500_000)
    assert summary["burn_rate_pct"] == pytest.approx(25.0)
    assert summary["periods"] == []


def test_portfolio_detail_includes_base_and_option_periods(flask_app, contract_id):
    """Periods created via the API show up in the nested obligation_summary.periods list."""
    flask_app.post(
        f"/api/cpmp/contracts/{contract_id}/periods",
        data=json.dumps({"period_type": "base", "obligated_value": 2_000_000}),
        content_type="application/json",
    )
    r_opt = flask_app.post(
        f"/api/cpmp/contracts/{contract_id}/periods",
        data=json.dumps({"period_type": "option_1", "ceiling_value": 2_000_000}),
        content_type="application/json",
    )
    period_id = json.loads(r_opt.data)["period_id"]
    flask_app.put(
        f"/api/cpmp/periods/{period_id}/exercise",
        data=json.dumps({"obligated_value": 1_500_000}),
        content_type="application/json",
    )

    r = flask_app.get(f"/api/cpmp/portfolio/{contract_id}")
    assert r.status_code == 200
    data = json.loads(r.data)

    summary = data["obligation_summary"]
    assert summary["obligated_value"] == pytest.approx(3_500_000)  # base 2M + exercised option 1.5M
    assert summary["billed_value"] == pytest.approx(500_000)
    assert summary["remaining_obligation"] == pytest.approx(3_000_000)

    period_types = {p["period_type"] for p in summary["periods"]}
    assert period_types == {"base", "option_1"}
