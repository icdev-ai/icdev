# CUI // SP-CTI
"""Tests for contract_manager.get_obligation_summary and
portfolio_manager.get_burn_rate_summary (prop-ctr-02-d2).

Verifies:
1. get_obligation_summary aggregates CLIN funded/billed totals into
   spent_so_far, total_owed, and burn_rate_pct.
2. Falls back to contract-level funded_value/billed_value when a contract
   has no CLINs.
3. Degrades gracefully (current_option = None) when cpmp_option_periods
   does not exist in the environment.
4. Returns an error for an unknown contract_id.
5. portfolio_manager.get_burn_rate_summary aggregates obligation data across
   contracts, grouped by contract ID, and honors the status filter.
"""
import sqlite3
import uuid

import pytest

from tests.conftest import MINIMAL_ICDEV_SCHEMA


@pytest.fixture()
def cpmp_db(tmp_path, monkeypatch):
    """Temporary SQLite DB (cpmp_contracts/cpmp_clins) wired to ICDEV_DB_PATH."""
    db_file = tmp_path / "cpmp_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    return db_file


def _insert_contract(db_file, **overrides):
    contract_id = overrides.pop("id", str(uuid.uuid4()))
    fields = {
        "contract_number": "N00014-26-C-0001",
        "title": "Test Contract",
        "agency": "DoD",
        "status": "active",
        "funded_value": 100000.0,
        "billed_value": 0.0,
    }
    fields.update(overrides)
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, agency, status, funded_value, billed_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (contract_id, fields["contract_number"], fields["title"], fields["agency"],
         fields["status"], fields["funded_value"], fields["billed_value"]),
    )
    conn.commit()
    conn.close()
    return contract_id


def _insert_clin(db_file, contract_id, funded_value, billed_value):
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "INSERT INTO cpmp_clins (id, contract_id, clin_number, funded_value, billed_value) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), contract_id, "0001", funded_value, billed_value),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# contract_manager.get_obligation_summary
# ---------------------------------------------------------------------------


class TestGetObligationSummary:
    def test_aggregates_clin_totals(self, cpmp_db):
        from tools.govcon.contract_manager import get_obligation_summary

        contract_id = _insert_contract(cpmp_db, funded_value=0.0, billed_value=0.0)
        _insert_clin(cpmp_db, contract_id, funded_value=200000.0, billed_value=50000.0)
        _insert_clin(cpmp_db, contract_id, funded_value=100000.0, billed_value=25000.0)

        result = get_obligation_summary(contract_id)

        assert result["status"] == "ok"
        assert result["base_period"]["funded_value"] == 300000.0
        assert result["spent_so_far"] == 75000.0
        assert result["total_owed"] == 225000.0

    def test_burn_rate_pct_computed(self, cpmp_db):
        from tools.govcon.contract_manager import get_obligation_summary

        contract_id = _insert_contract(cpmp_db, funded_value=0.0, billed_value=0.0)
        _insert_clin(cpmp_db, contract_id, funded_value=100000.0, billed_value=25000.0)

        result = get_obligation_summary(contract_id)

        assert result["burn_rate_pct"] == 25.0

    def test_falls_back_to_contract_level_when_no_clins(self, cpmp_db):
        from tools.govcon.contract_manager import get_obligation_summary

        contract_id = _insert_contract(cpmp_db, funded_value=50000.0, billed_value=10000.0)

        result = get_obligation_summary(contract_id)

        assert result["base_period"]["funded_value"] == 50000.0
        assert result["spent_so_far"] == 10000.0
        assert result["total_owed"] == 40000.0

    def test_zero_funded_value_gives_zero_burn_rate(self, cpmp_db):
        from tools.govcon.contract_manager import get_obligation_summary

        contract_id = _insert_contract(cpmp_db, funded_value=0.0, billed_value=0.0)

        result = get_obligation_summary(contract_id)

        assert result["burn_rate_pct"] == 0.0
        assert result["total_owed"] == 0.0

    def test_current_option_none_when_table_missing(self, cpmp_db):
        """cpmp_option_periods is not part of MINIMAL_ICDEV_SCHEMA — must degrade gracefully."""
        from tools.govcon.contract_manager import get_obligation_summary

        contract_id = _insert_contract(cpmp_db)

        result = get_obligation_summary(contract_id)

        assert result["current_option"] is None

    def test_unknown_contract_returns_error(self, cpmp_db):
        from tools.govcon.contract_manager import get_obligation_summary

        result = get_obligation_summary("does-not-exist")

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# portfolio_manager.get_burn_rate_summary
# ---------------------------------------------------------------------------


class TestGetBurnRateSummary:
    def test_groups_by_contract_id(self, cpmp_db):
        from tools.govcon.portfolio_manager import get_burn_rate_summary

        c1 = _insert_contract(cpmp_db, contract_number="C-001", status="active",
                               funded_value=100000.0, billed_value=20000.0)
        c2 = _insert_contract(cpmp_db, contract_number="C-002", status="active",
                               funded_value=200000.0, billed_value=100000.0)

        result = get_burn_rate_summary()

        assert result["status"] == "ok"
        assert result["total"] == 2
        assert c1 in result["contracts"]
        assert c2 in result["contracts"]
        assert result["contracts"][c1]["burn_rate_pct"] == 20.0
        assert result["contracts"][c2]["burn_rate_pct"] == 50.0

    def test_honors_status_filter(self, cpmp_db):
        from tools.govcon.portfolio_manager import get_burn_rate_summary

        active_id = _insert_contract(cpmp_db, contract_number="C-ACTIVE", status="active")
        _insert_contract(cpmp_db, contract_number="C-CLOSED", status="closed")

        result = get_burn_rate_summary(status_filter=("active",))

        assert result["total"] == 1
        assert active_id in result["contracts"]

    def test_empty_portfolio_returns_zero_total(self, cpmp_db):
        from tools.govcon.portfolio_manager import get_burn_rate_summary

        result = get_burn_rate_summary()

        assert result["status"] == "ok"
        assert result["total"] == 0
        assert result["contracts"] == {}
