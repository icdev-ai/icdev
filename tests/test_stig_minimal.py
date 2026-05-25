"""Minimal reproduction of the STIG test fixture issue."""
import sqlite3
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _patch_get_connection(tmp_path):
    db_path = tmp_path / "test_stig.db"

    def make_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE IF NOT EXISTS govlift_stig_checks (id TEXT PRIMARY KEY, workload_id TEXT)")
        conn.commit()
        return conn

    def _get_connection(**_kwargs):
        return make_conn()

    def _translate_sql(sql, *args, **kwargs):
        return sql

    with patch("tools.govlift.stig_checker.get_connection", _get_connection), \
         patch("tools.govlift.stig_checker.translate_sql", _translate_sql):
        yield


def test_validate_workload_checklist(_patch_get_connection):
    from tools.govlift.stig_checker import validate_workload_checklist
    result = validate_workload_checklist("wl-brand-new-workload")
    print("check_count:", result["check_count"])
    assert result["check_count"] > 0
