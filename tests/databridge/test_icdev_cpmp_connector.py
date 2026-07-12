# CUI // SP-CTI
"""ICDEV /cpmp bridge connector — MAC read-down, narrow writes, EVM derivation."""
from __future__ import annotations

import importlib
import uuid

import pytest

from tools.databridge.connector import ConnectorRequest
from tools.databridge.registry import list_registered
from tools.db.storage import get_connection

connector_module = importlib.import_module(
    "tools.databridge.connectors.icdev_cpmp_connector")


@pytest.fixture
def cpmp_db(icdev_db, monkeypatch):
    """Point BOTH the connector and its helpers at the schema-loaded tmp DB."""
    monkeypatch.setattr(connector_module, "get_connection",
                        lambda: get_connection(db_path=str(icdev_db)))
    conn = get_connection(db_path=str(icdev_db))
    contract_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, agency, "
        " contract_type, total_value, funded_value, ceiling_value, pop_start, "
        " pop_end, status, health, classification, compartments) "
        "VALUES (%s, 'W15P7T-26-C-0001', 'COSS Support', 'ARMY', 'T&M', "
        " 5000000, 3200000, 5500000, '2025-01-01', '2027-12-31', 'active', "
        " 'green', 'CUI', '[]')", (contract_id,))
    secret_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, agency, "
        " contract_type, status, classification, compartments) "
        "VALUES (%s, 'SECRET-1', 'Classified Effort', 'DIA', 'CPFF', "
        " 'active', 'SECRET', '[]')", (secret_id,))
    compartment_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, agency, "
        " contract_type, status, classification, compartments) "
        "VALUES (%s, 'COMP-1', 'Compartmented', 'NSA', 'FFP', 'active', "
        " 'CUI', '[\"LAC_X\"]')", (compartment_id,))
    deliverable_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO cpmp_deliverables (id, contract_id, cdrl_number, title, "
        " deliverable_type, frequency, due_date, status, classification) "
        "VALUES (%s, %s, 'A001', 'Monthly Status Report', 'cdrl', 'monthly', "
        " '2026-08-10', 'in_progress', 'CUI')", (deliverable_id, contract_id))
    conn.commit()
    return {"db": icdev_db, "contract_id": contract_id,
            "deliverable_id": deliverable_id}


@pytest.fixture
def connector(cpmp_db):
    c = connector_module.ICDEVCpmpConnector()
    assert c.connect({}) is True
    return c


def test_registered():
    assert list_registered().get("icdev_cpmp") == "ICDEVCpmpConnector"


def test_read_contracts_mac_read_down(connector, cpmp_db):
    # CUI caller: sees the CUI contract; SECRET and compartmented rows filtered.
    resp = connector.read(ConnectorRequest(
        table_name="contracts", filters={"_caller_classification": "CUI"}))
    assert resp.status == "ok"
    numbers = {r["contract_number"] for r in resp.data}
    assert numbers == {"W15P7T-26-C-0001"}
    assert resp.metadata["mac_filtered"] == 2
    assert "compartments" not in resp.data[0]
    assert resp.data[0]["funded_value"] == 3200000


def test_read_secret_caller_sees_read_down(connector):
    resp = connector.read(ConnectorRequest(
        table_name="contracts", filters={"_caller_classification": "SECRET"}))
    numbers = {r["contract_number"] for r in resp.data}
    # SECRET dominates CUI (read-down); compartmented still excluded.
    assert "W15P7T-26-C-0001" in numbers and "SECRET-1" in numbers
    assert "COMP-1" not in numbers


def test_read_deliverables_scoped_to_contract(connector, cpmp_db):
    resp = connector.read(ConnectorRequest(
        table_name="deliverables",
        filters={"contract_id": cpmp_db["contract_id"],
                 "_caller_classification": "CUI"}))
    assert resp.row_count == 1
    assert resp.data[0]["cdrl_number"] == "A001"


def test_write_evm_period_derives_cpi_spi(connector, cpmp_db):
    resp = connector.write(
        ConnectorRequest(table_name="evm_periods", sync_direction="write"),
        {"contract_id": cpmp_db["contract_id"], "period_date": "2026-08-31",
         "pv": 100000, "ev": 90000, "ac": 95000, "bac": 5000000})
    assert resp.status == "ok"
    assert resp.data["cpi"] == round(90000 / 95000, 4)
    assert resp.data["spi"] == 0.9
    conn = get_connection(db_path=str(cpmp_db["db"]))
    row = conn.execute("SELECT pv, ev, ac, cv FROM cpmp_evm_periods WHERE id = %s",
                       (resp.data["id"],)).fetchone()
    assert row["cv"] == -5000.0


def test_write_evm_unknown_contract(connector):
    resp = connector.write(
        ConnectorRequest(table_name="evm_periods", sync_direction="write"),
        {"contract_id": "nope", "period_date": "2026-08-31",
         "pv": 1, "ev": 1, "ac": 1})
    assert resp.status == "error"


def test_write_deliverable_status_transition(connector, cpmp_db):
    resp = connector.write(
        ConnectorRequest(table_name="deliverable_status", sync_direction="write"),
        {"deliverable_id": cpmp_db["deliverable_id"], "status": "submitted",
         "submitted_date": "2026-08-09"})
    assert resp.status == "ok"
    conn = get_connection(db_path=str(cpmp_db["db"]))
    row = conn.execute(
        "SELECT status, submitted_date FROM cpmp_deliverables WHERE id = %s",
        (cpmp_db["deliverable_id"],)).fetchone()
    assert row["status"] == "submitted" and row["submitted_date"] == "2026-08-09"


def test_write_deliverable_rejects_government_side_status(connector, cpmp_db):
    resp = connector.write(
        ConnectorRequest(table_name="deliverable_status", sync_direction="write"),
        {"deliverable_id": cpmp_db["deliverable_id"], "status": "accepted"})
    assert resp.status == "error"
    assert "government-side" in resp.errors[0]


def test_contracts_table_not_writable(connector):
    resp = connector.write(ConnectorRequest(table_name="contracts"), {"x": 1})
    assert resp.status == "error"


def test_feeds_allowlist_and_scopes():
    feeds = importlib.import_module("tools.dashboard.api.databridge_feeds")
    assert "icdev_cpmp" in feeds._CONNECTOR_ALLOWLIST
    keys = importlib.import_module("tools.cortex.service_keys")
    assert "databridge:icdev_cpmp:read" in keys.ALL_SCOPES
    assert "databridge:icdev_cpmp:write" in keys.ALL_SCOPES
