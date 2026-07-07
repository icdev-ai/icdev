# CUI // SP-CTI
"""Tests for cpmp_contracts obligation tracking + base/option period fields (prop-ctr-02).

Verifies:
1. cpmp_contracts schema includes obligated_value, funded_value,
   remaining_obligation, period_type, option_number, pop_start, pop_end.
2. create_contract persists obligated_value/period_type/option_number and
   derives remaining_obligation from obligated_value - billed_value.
3. update_contract recomputes remaining_obligation when obligated_value or
   billed_value changes, without requiring the caller to pass it explicitly.
"""
import os
import uuid

import pytest

from tools.db.storage import get_connection
from tools.govcon import contract_manager as cm
from tools.govcon.init_db import init_cpmp_tables

pytestmark = pytest.mark.usefixtures("tmp_path")


@pytest.fixture()
def cpmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / f"cpmp_ctr_02_{uuid.uuid4().hex}.db")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", db_path)
    conn = get_connection(db_path=db_path)
    init_cpmp_tables(conn)
    conn.close()
    return db_path


class TestSchema:
    def test_cpmp_contracts_has_all_seven_obligation_fields(self, cpmp_db):
        conn = get_connection(db_path=cpmp_db)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cpmp_contracts)").fetchall()}
        conn.close()
        required = {
            "obligated_value",
            "funded_value",
            "remaining_obligation",
            "period_type",
            "option_number",
            "pop_start",
            "pop_end",
        }
        assert required.issubset(cols)


class TestCreateContract:
    def test_persists_obligation_and_period_fields(self, cpmp_db):
        result = cm.create_contract(
            {
                "contract_number": "W91CRB-26-C-0001",
                "title": "Test Contract",
                "agency": "DoD",
                "obligated_value": 500000.0,
                "billed_value": 100000.0,
                "period_type": "option_1",
                "option_number": 1,
                "funded_value": 400000.0,
                "pop_start": "2026-01-01",
                "pop_end": "2026-12-31",
            }
        )
        assert result["status"] == "ok"
        contract = cm.get_contract(result["contract_id"])["contract"]
        assert contract["obligated_value"] == 500000.0
        assert contract["funded_value"] == 400000.0
        assert contract["remaining_obligation"] == 400000.0
        assert contract["period_type"] == "option_1"
        assert contract["option_number"] == 1
        assert contract["pop_start"] == "2026-01-01"
        assert contract["pop_end"] == "2026-12-31"

    def test_defaults_when_omitted(self, cpmp_db):
        result = cm.create_contract({"contract_number": "W91CRB-26-C-0002", "title": "Base Contract"})
        contract = cm.get_contract(result["contract_id"])["contract"]
        assert contract["obligated_value"] == 0.0
        assert contract["remaining_obligation"] == 0.0
        assert contract["period_type"] == "base"
        assert contract["option_number"] == 0


class TestUpdateContract:
    def test_recomputes_remaining_obligation_on_billed_value_change(self, cpmp_db):
        created = cm.create_contract(
            {"contract_number": "W91CRB-26-C-0003", "title": "T", "obligated_value": 500000.0, "billed_value": 100000.0}
        )
        contract_id = created["contract_id"]

        cm.update_contract(contract_id, {"billed_value": 200000.0})
        contract = cm.get_contract(contract_id)["contract"]
        assert contract["remaining_obligation"] == 300000.0

    def test_recomputes_remaining_obligation_on_obligated_value_change(self, cpmp_db):
        created = cm.create_contract(
            {"contract_number": "W91CRB-26-C-0004", "title": "T", "obligated_value": 500000.0, "billed_value": 100000.0}
        )
        contract_id = created["contract_id"]

        cm.update_contract(contract_id, {"obligated_value": 600000.0})
        contract = cm.get_contract(contract_id)["contract"]
        assert contract["remaining_obligation"] == 500000.0

    def test_updates_period_type_and_option_number(self, cpmp_db):
        created = cm.create_contract({"contract_number": "W91CRB-26-C-0005", "title": "T"})
        contract_id = created["contract_id"]

        cm.update_contract(contract_id, {"period_type": "option_2", "option_number": 2})
        contract = cm.get_contract(contract_id)["contract"]
        assert contract["period_type"] == "option_2"
        assert contract["option_number"] == 2
