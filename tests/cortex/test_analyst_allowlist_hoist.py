# CUI // SP-CTI
"""``_allowed_tables()`` is evaluated once per SQL-safety validation (#ctx-perf-02).

The allowlist call used to sit in the comprehension's *condition*
(``[t for t in tables if t not in _allowed_tables()]``), so Python re-evaluated
it once per referenced table. Each evaluation walks ``list_collections()`` and
loads the component registry via ``get_iqe_mapping()`` — a 5-table query paid
for five full registry scans on the safety path of every analyst query.

These tests assert the call *count*, not elapsed time: a timing assertion would
be flaky on a loaded runner and would still pass if the hoist were reverted on a
machine fast enough. The behavioural tests alongside pin that hoisting the call
did not change what the allowlist accepts or rejects.
"""
from __future__ import annotations

import pytest

from tools.cortex import analyst
from tools.cortex.analyst import CortexQueryBlocked
from tools.cortex.schemas import CortexContext, GovernanceReport

_ALLOWED = {"satellites", "stations", "anomalies", "passes", "telemetry"}

_FIVE_TABLE_SQL = (
    "SELECT s.id FROM satellites s "
    "JOIN stations st ON st.id = s.station_id "
    "JOIN anomalies a ON a.sat_id = s.id "
    "JOIN passes p ON p.sat_id = s.id "
    "JOIN telemetry t ON t.sat_id = s.id"
)


@pytest.fixture
def counting_allowlist(monkeypatch):
    """Replace ``_allowed_tables`` with a counting stub; yields the counter."""
    calls = {"n": 0}

    def _counted() -> set:
        calls["n"] += 1
        return set(_ALLOWED)

    monkeypatch.setattr(analyst, "_allowed_tables", _counted)
    return calls


def _validate(sql: str) -> GovernanceReport:
    governance = GovernanceReport()
    analyst._validate_sql_safety("q", sql, CortexContext(), governance, 0.0)
    return governance


def test_allowlist_built_once_for_a_five_table_query(counting_allowlist):
    assert len(analyst._extract_sql_tables(_FIVE_TABLE_SQL)) == 5
    _validate(_FIVE_TABLE_SQL)
    assert counting_allowlist["n"] == 1


def test_allowlist_built_once_for_a_single_table_query(counting_allowlist):
    _validate("SELECT id FROM satellites")
    assert counting_allowlist["n"] == 1


def test_allowlist_built_once_when_a_table_is_rejected(counting_allowlist):
    """Short-circuiting on the first off-allowlist table is not what saves the calls."""
    sql = (
        "SELECT s.id FROM satellites s "
        "JOIN sqlite_master m ON 1=1 "
        "JOIN stations st ON st.id = s.station_id"
    )
    with pytest.raises(CortexQueryBlocked):
        _validate(sql)
    assert counting_allowlist["n"] == 1


def test_off_allowlist_table_is_still_rejected(counting_allowlist):
    with pytest.raises(CortexQueryBlocked) as exc:
        _validate("SELECT * FROM users JOIN satellites ON 1=1")
    assert "allowlist" in str(exc.value)
    assert "users" in str(exc.value)


def test_on_allowlist_tables_still_pass(counting_allowlist):
    governance = _validate(_FIVE_TABLE_SQL)
    assert governance.outcomes[analyst._GATE_ALLOWLIST] == "pass"


def test_non_select_is_still_rejected_before_the_allowlist(counting_allowlist):
    with pytest.raises(CortexQueryBlocked):
        _validate("DELETE FROM satellites")
    assert counting_allowlist["n"] == 0


def test_multi_statement_sql_is_still_rejected(counting_allowlist):
    with pytest.raises(CortexQueryBlocked):
        _validate("SELECT id FROM satellites; DROP TABLE satellites")
    assert counting_allowlist["n"] == 0


def test_sql_with_no_table_reference_is_still_rejected(counting_allowlist):
    with pytest.raises(CortexQueryBlocked) as exc:
        _validate("SELECT 1")
    assert "no table reference" in str(exc.value)


def test_real_allowlist_still_rejects_an_unregistered_table():
    """No stub: the production ``_allowed_tables`` still gates as it did."""
    with pytest.raises(CortexQueryBlocked):
        _validate("SELECT * FROM etc_passwd_not_a_collection")
