# CUI // SP-CTI
"""Tests for prop-ctr-02: CPMP funding/obligation tracking + base+option periods.

Coverage:
    - contract_periods_manager: create_period, list_periods, exercise_option, get_obligation_summary
    - portfolio_manager: obligated_value in get_portfolio_summary
    - API endpoints: /api/cpmp/contracts/<id>/periods, /api/cpmp/periods/<id>/exercise,
                     /api/cpmp/contracts/<id>/obligation-summary
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """In-memory SQLite DB with CPMP tables and minimal audit_trail."""
    import sqlite3

    db_file = str(tmp_path / "test_cpmp.db")
    monkeypatch.setenv("ICDEV_DB_PATH", db_file)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Minimal audit trail
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_trail "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, actor TEXT, "
        "action TEXT, details TEXT, session_id TEXT, created_at TEXT DEFAULT (datetime('now')))"
    )

    # cpmp_contracts with new columns
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

    # cpmp_clins for billed_value aggregation
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

    # cpmp_contract_periods
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

    conn.commit()
    conn.close()
    return db_file


@pytest.fixture()
def contract_id(db):
    """Insert a sample active contract, return its id."""
    import sqlite3

    cid = str(uuid.uuid4())
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, agency, status, total_value, funded_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, "W911QX-25-C-0042", "Test Services Contract", "Army", "active", 5_000_000, 2_000_000),
    )
    conn.commit()
    conn.close()
    return cid


@pytest.fixture()
def contract_with_clins(db, contract_id):
    """Add billed CLINs to the contract."""
    import sqlite3

    conn = sqlite3.connect(db)
    for i, billed in enumerate([300_000, 200_000]):
        conn.execute(
            "INSERT INTO cpmp_clins (id, contract_id, clin_number, billed_value, funded_value, total_value) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), contract_id, f"000{i+1}", billed, 1_000_000, 1_000_000),
        )
    conn.commit()
    conn.close()
    return contract_id


# ── Helpers ──────────────────────────────────────────────────────────


def _get_conn(db_path):
    """Return a real get_connection() using the test DB."""
    from tools.db.storage import get_connection
    return get_connection(db_path=db_path)


# ── Unit: create_period ───────────────────────────────────────────────


def test_create_base_period(db, contract_id):
    from tools.govcon.contract_periods_manager import create_period

    result = create_period(
        contract_id, "base",
        pop_start="2025-01-01", pop_end="2025-12-31",
        obligated_value=2_000_000, funded_value=2_000_000,
        ceiling_value=5_000_000,
    )
    assert result["status"] == "ok"
    assert result["period_type"] == "base"
    assert "period_id" in result


def test_create_option_period(db, contract_id):
    from tools.govcon.contract_periods_manager import create_period

    result = create_period(
        contract_id, "option_1",
        pop_start="2026-01-01", pop_end="2026-12-31",
        obligated_value=0, funded_value=0,
        ceiling_value=2_000_000,
    )
    assert result["status"] == "ok"
    assert result["period_type"] == "option_1"


def test_create_duplicate_period_rejected(db, contract_id):
    from tools.govcon.contract_periods_manager import create_period

    create_period(contract_id, "base", obligated_value=1_000_000)
    result = create_period(contract_id, "base", obligated_value=500_000)
    assert result["status"] == "error"
    assert "already exists" in result["message"]


def test_create_period_invalid_type(db, contract_id):
    from tools.govcon.contract_periods_manager import create_period

    result = create_period(contract_id, "option_99")
    assert result["status"] == "error"
    assert "period_type" in result["message"]


def test_create_period_missing_contract(db):
    from tools.govcon.contract_periods_manager import create_period

    result = create_period("nonexistent-id", "base")
    assert result["status"] == "error"
    assert "not found" in result["message"]


# ── Unit: list_periods ────────────────────────────────────────────────


def test_list_periods_empty(db, contract_id):
    from tools.govcon.contract_periods_manager import list_periods

    result = list_periods(contract_id)
    assert result["status"] == "ok"
    assert result["periods"] == []
    assert result["total"] == 0


def test_list_periods_with_data(db, contract_id):
    from tools.govcon.contract_periods_manager import create_period, list_periods

    create_period(contract_id, "base", obligated_value=2_000_000)
    create_period(contract_id, "option_1", obligated_value=0)

    result = list_periods(contract_id)
    assert result["status"] == "ok"
    assert result["total"] == 2
    periods = result["periods"]
    types = [p["period_type"] for p in periods]
    assert "base" in types
    assert "option_1" in types


def test_list_periods_computed_fields(db, contract_with_clins):
    """remaining_obligation and burn_rate_pct are computed correctly."""
    from tools.govcon.contract_periods_manager import create_period, list_periods

    cid = contract_with_clins  # total billed = 500,000
    create_period(cid, "base", obligated_value=2_000_000)

    result = list_periods(cid)
    base = next(p for p in result["periods"] if p["period_type"] == "base")
    assert base["remaining_obligation"] == 2_000_000 - 500_000
    assert base["burn_rate_pct"] == round(500_000 / 2_000_000 * 100, 1)


# ── Unit: exercise_option ─────────────────────────────────────────────


def test_exercise_option_success(db, contract_id):
    from tools.govcon.contract_periods_manager import create_period, exercise_option, list_periods

    create_period(contract_id, "base", obligated_value=2_000_000)
    r = create_period(contract_id, "option_1", pop_start="2026-01-01", pop_end="2026-12-31")
    period_id = r["period_id"]

    result = exercise_option(period_id, obligated_value=1_800_000, exercised_by="co_user")
    assert result["status"] == "ok"
    assert result["obligated_value"] == 1_800_000
    assert result["contract_obligated_value"] == 2_000_000 + 1_800_000

    # Check period is now exercised
    periods = list_periods(contract_id)["periods"]
    opt1 = next(p for p in periods if p["period_type"] == "option_1")
    assert opt1["status"] == "exercised"
    assert opt1["exercised_by"] == "co_user"


def test_exercise_base_period_rejected(db, contract_id):
    from tools.govcon.contract_periods_manager import create_period, exercise_option

    r = create_period(contract_id, "base", obligated_value=2_000_000)
    result = exercise_option(r["period_id"], 1_000_000)
    assert result["status"] == "error"
    assert "base" in result["message"]


def test_exercise_already_exercised_rejected(db, contract_id):
    from tools.govcon.contract_periods_manager import create_period, exercise_option

    r = create_period(contract_id, "option_1", obligated_value=0)
    exercise_option(r["period_id"], 1_000_000)
    result = exercise_option(r["period_id"], 500_000)
    assert result["status"] == "error"
    assert "already" in result["message"]


def test_exercise_nonexistent_period(db):
    from tools.govcon.contract_periods_manager import exercise_option

    result = exercise_option("nonexistent-id", 100_000)
    assert result["status"] == "error"
    assert "not found" in result["message"]


# ── Unit: get_obligation_summary ─────────────────────────────────────


def test_obligation_summary_no_periods(db, contract_with_clins):
    """Falls back to contract.funded_value when no periods exist."""
    from tools.govcon.contract_periods_manager import get_obligation_summary

    cid = contract_with_clins
    result = get_obligation_summary(cid)
    assert result["status"] == "ok"
    # funded_value=2,000,000; billed=500,000
    assert result["total_billed"] == 500_000
    assert result["remaining_obligation"] == pytest.approx(2_000_000 - 500_000)
    assert result["burn_rate_pct"] == pytest.approx(25.0)


def test_obligation_summary_with_periods(db, contract_with_clins):
    from tools.govcon.contract_periods_manager import create_period, get_obligation_summary

    cid = contract_with_clins
    create_period(cid, "base", obligated_value=2_000_000)
    create_period(cid, "option_1", obligated_value=0)  # unexercised

    result = get_obligation_summary(cid)
    assert result["status"] == "ok"
    assert result["total_obligated"] == pytest.approx(2_000_000)  # only base is active
    assert result["total_billed"] == 500_000
    assert result["remaining_obligation"] == pytest.approx(1_500_000)
    assert len(result["by_period"]) == 2


def test_obligation_summary_missing_contract(db):
    from tools.govcon.contract_periods_manager import get_obligation_summary

    result = get_obligation_summary("nonexistent-id")
    assert result["status"] == "error"
    assert "not found" in result["message"]


def test_obligation_summary_after_exercise(db, contract_with_clins):
    """Burn rate recalculates after exercising an option."""
    from tools.govcon.contract_periods_manager import create_period, exercise_option, get_obligation_summary

    cid = contract_with_clins  # billed=500,000
    create_period(cid, "base", obligated_value=2_000_000)
    r = create_period(cid, "option_1", obligated_value=0)
    exercise_option(r["period_id"], obligated_value=1_000_000)

    result = get_obligation_summary(cid)
    assert result["total_obligated"] == pytest.approx(3_000_000)  # 2M base + 1M option_1
    assert result["burn_rate_pct"] == pytest.approx(round(500_000 / 3_000_000 * 100, 1))


# ── Unit: portfolio_manager obligated_value ───────────────────────────


def test_portfolio_summary_includes_obligated_value(db):
    """get_portfolio_summary returns obligated_value in portfolio dict."""
    import sqlite3

    # Rebuild cpmp_contracts with all columns portfolio_manager queries
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE IF EXISTS cpmp_contracts")
    conn.execute("""
        CREATE TABLE cpmp_contracts (
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
            cpars_rating_current TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS cpmp_deliverables (id TEXT, contract_id TEXT, status TEXT, due_date TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS cpmp_evm_periods (id TEXT, contract_id TEXT, period_date TEXT, cpi REAL, spi REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS cpmp_cpars_assessments (id TEXT, contract_id TEXT, period_end TEXT, overall_rating TEXT)")
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, agency, status, "
        "total_value, funded_value, obligated_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, "TEST-001", "Test", "DoD", "active", 5_000_000, 2_000_000, 1_800_000),
    )
    conn.commit()
    conn.close()

    from tools.govcon.portfolio_manager import get_portfolio_summary

    result = get_portfolio_summary()
    assert result["status"] == "ok"
    portfolio = result["portfolio"]
    assert "obligated_value" in portfolio
    assert portfolio["obligated_value"] >= 1_800_000
    assert "remaining_obligation" in portfolio
    assert "burn_rate_pct" in portfolio


# ── API: /api/cpmp/contracts/<id>/periods ────────────────────────────


@pytest.fixture()
def flask_app(db, monkeypatch):
    """Minimal Flask test client with cpmp_api blueprint."""
    monkeypatch.setenv("ICDEV_DB_PATH", db)
    from tools.dashboard.api.cpmp import cpmp_api
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True

    # Bypass auth decorators
    @app.before_request
    def _inject_user():
        from flask import g
        g.current_user = {"username": "test_co", "role": "admin", "email": "co@test.mil", "classification": "CUI"}

    app.register_blueprint(cpmp_api)
    return app.test_client()


def test_api_list_periods_empty(flask_app, contract_id):
    r = flask_app.get(f"/api/cpmp/contracts/{contract_id}/periods")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["status"] == "ok"
    assert data["periods"] == []


def test_api_create_period(flask_app, contract_id):
    payload = {
        "period_type": "base",
        "pop_start": "2025-01-01",
        "pop_end": "2025-12-31",
        "obligated_value": 2_000_000,
        "funded_value": 2_000_000,
        "ceiling_value": 5_000_000,
    }
    r = flask_app.post(
        f"/api/cpmp/contracts/{contract_id}/periods",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert r.status_code == 201
    data = json.loads(r.data)
    assert data["status"] == "ok"
    assert data["period_type"] == "base"


def test_api_create_period_missing_type(flask_app, contract_id):
    r = flask_app.post(
        f"/api/cpmp/contracts/{contract_id}/periods",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert r.status_code == 400
    data = json.loads(r.data)
    assert data["status"] == "error"


def test_api_list_periods_after_create(flask_app, contract_id):
    flask_app.post(
        f"/api/cpmp/contracts/{contract_id}/periods",
        data=json.dumps({"period_type": "base", "obligated_value": 1_000_000}),
        content_type="application/json",
    )
    r = flask_app.get(f"/api/cpmp/contracts/{contract_id}/periods")
    data = json.loads(r.data)
    assert data["total"] == 1
    assert data["periods"][0]["period_type"] == "base"


def test_api_exercise_option(flask_app, contract_id):
    # Create base + option_1
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

    r = flask_app.put(
        f"/api/cpmp/periods/{period_id}/exercise",
        data=json.dumps({"obligated_value": 1_500_000}),
        content_type="application/json",
    )
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["status"] == "ok"
    assert data["obligated_value"] == 1_500_000


def test_api_obligation_summary(flask_app, contract_id):
    flask_app.post(
        f"/api/cpmp/contracts/{contract_id}/periods",
        data=json.dumps({"period_type": "base", "obligated_value": 2_000_000}),
        content_type="application/json",
    )
    r = flask_app.get(f"/api/cpmp/contracts/{contract_id}/obligation-summary")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["status"] == "ok"
    assert "total_obligated" in data
    assert "remaining_obligation" in data
    assert "burn_rate_pct" in data
    assert "by_period" in data


def test_api_obligation_summary_not_found(flask_app):
    r = flask_app.get("/api/cpmp/contracts/nonexistent-id/obligation-summary")
    assert r.status_code == 200  # API returns 200 with error payload
    data = json.loads(r.data)
    assert data["status"] == "error"


# ── Integration: full flow ────────────────────────────────────────────


def test_full_obligation_lifecycle(db, contract_with_clins):
    """Full lifecycle: create periods → exercise option → check summary."""
    from tools.govcon.contract_periods_manager import (
        create_period,
        exercise_option,
        get_obligation_summary,
        list_periods,
    )

    cid = contract_with_clins  # billed = 500,000

    # 1. Create base period
    r_base = create_period(cid, "base", pop_start="2025-01-01", pop_end="2025-12-31", obligated_value=2_000_000)
    assert r_base["status"] == "ok"

    # 2. Create option periods (unexercised)
    r_opt1 = create_period(cid, "option_1", pop_start="2026-01-01", pop_end="2026-12-31", ceiling_value=2_000_000)
    r_opt2 = create_period(cid, "option_2", pop_start="2027-01-01", pop_end="2027-12-31", ceiling_value=2_000_000)
    assert r_opt1["status"] == "ok"
    assert r_opt2["status"] == "ok"

    # 3. Check summary — only base is active/obligated
    summary = get_obligation_summary(cid)
    assert summary["total_obligated"] == pytest.approx(2_000_000)
    assert summary["burn_rate_pct"] == pytest.approx(25.0)

    # 4. Exercise option 1
    ex = exercise_option(r_opt1["period_id"], obligated_value=1_800_000, exercised_by="contracting_officer")
    assert ex["status"] == "ok"
    assert ex["contract_obligated_value"] == pytest.approx(3_800_000)

    # 5. Summary now includes option_1
    summary2 = get_obligation_summary(cid)
    assert summary2["total_obligated"] == pytest.approx(3_800_000)
    assert summary2["total_billed"] == pytest.approx(500_000)
    assert summary2["remaining_obligation"] == pytest.approx(3_300_000)

    # 6. list_periods returns 3 rows with correct statuses
    periods = list_periods(cid)["periods"]
    assert len(periods) == 3
    by_type = {p["period_type"]: p for p in periods}
    assert by_type["base"]["status"] == "active"
    assert by_type["option_1"]["status"] == "exercised"
    assert by_type["option_2"]["status"] == "unexercised"
