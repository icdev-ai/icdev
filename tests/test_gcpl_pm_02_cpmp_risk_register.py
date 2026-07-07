# CUI // SP-CTI
"""Tests for prop-pm-02: CPMP program-level risk register.

Coverage:
    - risk_manager: create_risk, update_risk, delete_risk, list_risks, get_risk
    - exposure = probability x impact, recomputed on update
    - get_risk_matrix: 5x5 grid + summary counts
    - get_portfolio_risk_summary: cross-contract aggregate
    - milestone / negative_event linkage joins
"""
from __future__ import annotations

import uuid

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """In-memory SQLite DB with the tables risk_manager touches."""
    import sqlite3

    db_file = str(tmp_path / "test_cpmp_risks.db")
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
            status TEXT DEFAULT 'active',
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cpmp_milestones (
            id TEXT PRIMARY KEY,
            contract_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            classification TEXT DEFAULT 'CUI'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cpmp_negative_events (
            id TEXT PRIMARY KEY,
            contract_id TEXT NOT NULL,
            event_type TEXT DEFAULT '',
            classification TEXT DEFAULT 'CUI'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cpmp_risks (
            id TEXT PRIMARY KEY,
            contract_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'other',
            probability INTEGER NOT NULL DEFAULT 3,
            impact INTEGER NOT NULL DEFAULT 3,
            exposure INTEGER NOT NULL DEFAULT 9,
            mitigation TEXT,
            owner TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            milestone_id TEXT,
            negative_event_id TEXT,
            classification TEXT NOT NULL DEFAULT 'CUI',
            tenant_id TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    return db_file


@pytest.fixture()
def contract_id(db):
    import sqlite3

    cid = str(uuid.uuid4())
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, status) VALUES (?, ?, ?, ?)",
        (cid, "W911QX-25-C-0099", "Test Risk Contract", "active"),
    )
    conn.commit()
    conn.close()
    return cid


@pytest.fixture()
def milestone_id(db, contract_id):
    import sqlite3

    mid = str(uuid.uuid4())
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cpmp_milestones (id, contract_id, title, status) VALUES (?, ?, ?, ?)",
        (mid, contract_id, "PDR Complete", "pending"),
    )
    conn.commit()
    conn.close()
    return mid


# ── Unit: create_risk ─────────────────────────────────────────────────


def test_create_risk_defaults(db, contract_id):
    from tools.govcon.risk_manager import create_risk

    result = create_risk({"contract_id": contract_id, "title": "Key personnel attrition"})
    assert result["status"] == "ok"
    assert "risk_id" in result


def test_create_risk_computes_exposure(db, contract_id):
    from tools.govcon.risk_manager import create_risk, get_risk

    r = create_risk({
        "contract_id": contract_id, "title": "Supply chain delay",
        "category": "supply_chain", "probability": 4, "impact": 5,
    })
    assert r["status"] == "ok"
    risk = get_risk(r["risk_id"])["risk"]
    assert risk["exposure"] == 20


def test_create_risk_missing_contract_id(db):
    from tools.govcon.risk_manager import create_risk

    result = create_risk({"title": "No contract"})
    assert result["status"] == "error"
    assert "contract_id" in result["message"]


def test_create_risk_missing_title(db, contract_id):
    from tools.govcon.risk_manager import create_risk

    result = create_risk({"contract_id": contract_id})
    assert result["status"] == "error"
    assert "title" in result["message"]


def test_create_risk_invalid_category(db, contract_id):
    from tools.govcon.risk_manager import create_risk

    result = create_risk({"contract_id": contract_id, "title": "Bad cat", "category": "bogus"})
    assert result["status"] == "error"
    assert "category" in result["message"]


def test_create_risk_invalid_status(db, contract_id):
    from tools.govcon.risk_manager import create_risk

    result = create_risk({"contract_id": contract_id, "title": "Bad status", "status": "bogus"})
    assert result["status"] == "error"
    assert "status" in result["message"]


@pytest.mark.parametrize("probability,impact", [(0, 3), (6, 3), (3, 0), (3, 6)])
def test_create_risk_probability_impact_out_of_range(db, contract_id, probability, impact):
    from tools.govcon.risk_manager import create_risk

    result = create_risk({
        "contract_id": contract_id, "title": "Out of range",
        "probability": probability, "impact": impact,
    })
    assert result["status"] == "error"
    assert "between 1 and 5" in result["message"]


def test_create_risk_linked_to_milestone(db, contract_id, milestone_id):
    from tools.govcon.risk_manager import create_risk, list_risks

    create_risk({"contract_id": contract_id, "title": "Slip risk", "milestone_id": milestone_id})
    risks = list_risks(contract_id)["risks"]
    assert risks[0]["milestone_title"] == "PDR Complete"


# ── Unit: update_risk ─────────────────────────────────────────────────


def test_update_risk_recomputes_exposure(db, contract_id):
    from tools.govcon.risk_manager import create_risk, update_risk, get_risk

    r = create_risk({"contract_id": contract_id, "title": "Cost growth", "probability": 2, "impact": 2})
    assert get_risk(r["risk_id"])["risk"]["exposure"] == 4

    update_risk(r["risk_id"], {"probability": 5, "impact": 5})
    assert get_risk(r["risk_id"])["risk"]["exposure"] == 25


def test_update_risk_status(db, contract_id):
    from tools.govcon.risk_manager import create_risk, update_risk, get_risk

    r = create_risk({"contract_id": contract_id, "title": "Staffing gap", "category": "staffing"})
    update_risk(r["risk_id"], {"status": "mitigating", "mitigation": "Backfill req opened"})
    risk = get_risk(r["risk_id"])["risk"]
    assert risk["status"] == "mitigating"
    assert risk["mitigation"] == "Backfill req opened"


def test_update_risk_not_found(db):
    from tools.govcon.risk_manager import update_risk

    result = update_risk("nonexistent-id", {"status": "closed"})
    assert result["status"] == "error"
    assert "not found" in result["message"]


def test_update_risk_invalid_probability(db, contract_id):
    from tools.govcon.risk_manager import create_risk, update_risk

    r = create_risk({"contract_id": contract_id, "title": "Edge case"})
    result = update_risk(r["risk_id"], {"probability": 9})
    assert result["status"] == "error"


# ── Unit: delete_risk ─────────────────────────────────────────────────


def test_delete_risk(db, contract_id):
    from tools.govcon.risk_manager import create_risk, delete_risk, list_risks

    r = create_risk({"contract_id": contract_id, "title": "Transient risk"})
    result = delete_risk(r["risk_id"])
    assert result["status"] == "ok"
    assert list_risks(contract_id)["risks"] == []


def test_delete_risk_not_found(db):
    from tools.govcon.risk_manager import delete_risk

    result = delete_risk("nonexistent-id")
    assert result["status"] == "error"


# ── Unit: list_risks ──────────────────────────────────────────────────


def test_list_risks_empty(db, contract_id):
    from tools.govcon.risk_manager import list_risks

    result = list_risks(contract_id)
    assert result["status"] == "ok"
    assert result["risks"] == []


def test_list_risks_filters_by_status_and_category(db, contract_id):
    from tools.govcon.risk_manager import create_risk, list_risks

    create_risk({"contract_id": contract_id, "title": "A", "category": "cyber", "status": "open"})
    r2 = create_risk({"contract_id": contract_id, "title": "B", "category": "cost", "status": "closed"})
    update_result_id = r2["risk_id"]

    assert len(list_risks(contract_id)["risks"]) == 2
    assert len(list_risks(contract_id, status="open")["risks"]) == 1
    assert len(list_risks(contract_id, category="cyber")["risks"]) == 1
    assert list_risks(contract_id, status="closed")["risks"][0]["id"] == update_result_id


def test_list_risks_ordered_by_exposure_desc(db, contract_id):
    from tools.govcon.risk_manager import create_risk, list_risks

    create_risk({"contract_id": contract_id, "title": "Low", "probability": 1, "impact": 1})
    create_risk({"contract_id": contract_id, "title": "Critical", "probability": 5, "impact": 5})

    risks = list_risks(contract_id)["risks"]
    assert risks[0]["title"] == "Critical"
    assert risks[-1]["title"] == "Low"


# ── Unit: get_risk_matrix ──────────────────────────────────────────────


def test_risk_matrix_empty(db, contract_id):
    from tools.govcon.risk_manager import get_risk_matrix

    result = get_risk_matrix(contract_id)
    assert result["status"] == "ok"
    assert result["summary"]["total"] == 0


def test_risk_matrix_buckets_and_summary(db, contract_id):
    from tools.govcon.risk_manager import create_risk, get_risk_matrix

    create_risk({"contract_id": contract_id, "title": "Low", "probability": 1, "impact": 1})       # exposure 1 -> low
    create_risk({"contract_id": contract_id, "title": "Medium", "probability": 2, "impact": 4})     # exposure 8 -> medium
    create_risk({"contract_id": contract_id, "title": "High", "probability": 3, "impact": 4})       # exposure 12 -> high
    create_risk({"contract_id": contract_id, "title": "Critical", "probability": 5, "impact": 5})   # exposure 25 -> critical

    result = get_risk_matrix(contract_id)
    assert result["status"] == "ok"
    summary = result["summary"]
    assert summary["total"] == 4
    assert summary["low"] == 1
    assert summary["medium"] == 1
    assert summary["high"] == 1
    assert summary["critical"] == 1
    assert result["matrix"][1][1][0]["title"] == "Low"
    assert result["matrix"][5][5][0]["title"] == "Critical"


# ── Unit: get_portfolio_risk_summary ────────────────────────────────────


def test_portfolio_risk_summary_across_contracts(db, contract_id):
    import sqlite3

    from tools.govcon.risk_manager import create_risk, get_portfolio_risk_summary

    other_cid = str(uuid.uuid4())
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, status) VALUES (?, ?, ?, ?)",
        (other_cid, "W911QX-25-C-0100", "Second Contract", "active"),
    )
    conn.commit()
    conn.close()

    create_risk({"contract_id": contract_id, "title": "R1", "probability": 5, "impact": 5})  # critical
    create_risk({"contract_id": contract_id, "title": "R2", "probability": 3, "impact": 4})   # high
    create_risk({"contract_id": other_cid, "title": "R3", "status": "closed"})

    result = get_portfolio_risk_summary()
    assert result["status"] == "ok"
    summary = result["summary"]
    assert summary["total"] == 3
    assert summary["open"] == 2
    assert summary["critical"] == 1
    assert summary["high"] == 1
    assert len(summary["by_contract"]) == 2


# ── Unit: exposure_label ────────────────────────────────────────────────


@pytest.mark.parametrize("score,label", [(1, "low"), (5, "low"), (6, "medium"), (10, "medium"),
                                          (11, "high"), (15, "high"), (16, "critical"), (25, "critical")])
def test_exposure_label_bands(score, label):
    from tools.govcon.risk_manager import exposure_label

    assert exposure_label(score) == label
