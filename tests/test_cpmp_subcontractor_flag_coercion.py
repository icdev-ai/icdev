# CUI // SP-CTI
"""The 0/1 compliance flags must reach the DB as ints on BOTH write paths.

`flow_down_complete`, `flowdown_verified`, `cybersecurity_compliant` and
`isr_ssr_current` are INTEGER columns. PostgreSQL — the primary backend —
refuses a boolean for an integer column outright ("column is of type integer
but expression is of type boolean"); SQLite accepts one silently because
`bool` is an `int` subclass.

`create_subcontractor` has coerced with `int(bool(...))` since #1520.
`update_subcontractor` appended `data[field]` raw, so
``PUT /api/cpmp/subcontractors/<id>`` with ``{"flow_down_complete": true}`` —
a JSON boolean, i.e. exactly the shape of the remediation the CPMP monitor's
"Subcontractor Compliance" card asks the PM to record — raised on PG and the
route returned 500. The gap survived because tests/conftest.py forces SQLite,
where the raw bool works, and because no test exercises the PUT at all.

These tests therefore assert on the PARAMS handed to `execute`, not on what a
backend stores: the type is the defect, and the backend that rejects it is the
one the suite does not run against.

Verifies:
1. update_subcontractor coerces every compliance flag to int, from bool and
   from the other shapes an HTTP caller can produce.
2. Coercion is fail-closed — an unrecognised value reads as non-compliant, so
   a parsing mistake keeps reporting the FAR 52.219-9 gap rather than retiring
   one nobody closed. ("0" is the case that matters: bool("0") is True.)
3. create_subcontractor coerces identically, so the two paths cannot drift.
4. Non-flag fields are still passed through untouched.
5. The flag list matches the columns update_subcontractor accepts.
"""
from unittest.mock import MagicMock, patch

import pytest

_SUB_ID = "sub-flag-0001"
_CONTRACT_ID = "ctr-flag-0001"

_FLAGS = (
    "flow_down_complete",
    "flowdown_verified",
    "cybersecurity_compliant",
    "isr_ssr_current",
)


# ---------------------------------------------------------------------------
# Fake connection — captures the params of each statement
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self):
        self.statements = []

    def set_security_context(self, _ctx):
        pass

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        cursor = MagicMock()
        if "FROM cpmp_subcontractors" in sql:
            cursor.fetchone.return_value = {"id": _SUB_ID, "status": "active"}
        elif "FROM cpmp_contracts" in sql:
            cursor.fetchone.return_value = {"id": _CONTRACT_ID}
        else:
            cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        return cursor

    def commit(self):
        pass

    def close(self):
        pass

    def statement_containing(self, needle):
        for sql, params in self.statements:
            if needle in sql:
                return sql, params
        raise AssertionError(f"no statement containing {needle!r} was recorded")


def _update(data):
    from tools.govcon import subcontractor_tracker

    conn = _FakeConn()
    with patch.object(subcontractor_tracker, "_get_db", return_value=conn):
        result = subcontractor_tracker.update_subcontractor(_SUB_ID, data)
    return result, conn


def _create(data):
    from tools.govcon import subcontractor_tracker

    conn = _FakeConn()
    with patch.object(subcontractor_tracker, "_get_db", return_value=conn):
        result = subcontractor_tracker.create_subcontractor(_CONTRACT_ID, data)
    return result, conn


def _update_flag_param(conn, field):
    """The value bound to `field` in the recorded UPDATE."""
    sql, params = conn.statement_containing("UPDATE cpmp_subcontractors")
    assignments = sql.split("SET ", 1)[1].split(" WHERE ", 1)[0].split(", ")
    for index, assignment in enumerate(assignments):
        if assignment.strip().startswith(f"{field} ="):
            return params[index]
    raise AssertionError(f"{field} not assigned in {sql!r}")


def _create_flag_param(conn, field):
    """The value bound to `field` in the recorded INSERT."""
    sql, params = conn.statement_containing("INSERT INTO cpmp_subcontractors")
    columns = sql.split("(", 1)[1].split(")", 1)[0]
    names = [c.strip() for c in columns.split(",")]
    return params[names.index(field)]


# ---------------------------------------------------------------------------
# Tests: update coerces to int
# ---------------------------------------------------------------------------


class TestUpdateCoercesFlagsToInt:
    """A JSON boolean must never reach an INTEGER column as a Python bool."""

    @pytest.mark.parametrize("field", _FLAGS)
    def test_true_becomes_int_one(self, field):
        _, conn = _update({field: True})
        value = _update_flag_param(conn, field)
        assert value == 1
        assert type(value) is int

    @pytest.mark.parametrize("field", _FLAGS)
    def test_false_becomes_int_zero(self, field):
        _, conn = _update({field: False})
        value = _update_flag_param(conn, field)
        assert value == 0
        assert type(value) is int

    @pytest.mark.parametrize("field", _FLAGS)
    def test_no_flag_param_is_a_bool(self, field):
        """`isinstance(True, int)` is True — assert the exact type, not int-ness."""
        _, conn = _update({field: True})
        assert not isinstance(_update_flag_param(conn, field), bool)

    def test_all_flags_in_one_body_are_all_coerced(self):
        _, conn = _update({f: True for f in _FLAGS})
        for field in _FLAGS:
            assert type(_update_flag_param(conn, field)) is int

    def test_update_still_succeeds(self):
        result, _ = _update({"flow_down_complete": True})
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Tests: fail-closed coercion
# ---------------------------------------------------------------------------


class TestCoercionIsFailClosed:
    """An unrecognised value must read as non-compliant, never as compliant."""

    @pytest.mark.parametrize(
        "value",
        [False, 0, "0", "false", "False", "no", "off", "", "   ", None, [], {}, "maybe"],
        ids=[
            "bool_false", "int_zero", "str_zero", "str_false", "str_False_cased",
            "str_no", "str_off", "empty", "whitespace", "none", "list", "dict",
            "unrecognised_word",
        ],
    )
    def test_non_affirmative_values_read_as_zero(self, value):
        _, conn = _update({"flow_down_complete": value})
        assert _update_flag_param(conn, "flow_down_complete") == 0

    @pytest.mark.parametrize(
        "value",
        [True, 1, 2, "1", "true", "TRUE", "yes", "on"],
        ids=["bool", "one", "two", "str_one", "str_true", "str_TRUE", "yes", "on"],
    )
    def test_affirmative_values_read_as_one(self, value):
        _, conn = _update({"flow_down_complete": value})
        assert _update_flag_param(conn, "flow_down_complete") == 1

    def test_string_zero_is_not_compliant(self):
        """The whole reason this is not `int(bool(v))`: bool("0") is True.

        A form-encoded "0" reading as compliant retires a FAR 52.219-9 gap
        nobody closed — the one direction a coercion bug must not fail in.
        """
        _, conn = _update({"cybersecurity_compliant": "0"})
        assert _update_flag_param(conn, "cybersecurity_compliant") == 0


# ---------------------------------------------------------------------------
# Tests: create coerces the same way
# ---------------------------------------------------------------------------


class TestCreateCoercesIdentically:
    """Both write paths share one helper, so they cannot drift apart again."""

    @pytest.mark.parametrize("field", _FLAGS)
    def test_true_becomes_int_one(self, field):
        _, conn = _create({"company_name": "Acme LLC", field: True})
        value = _create_flag_param(conn, field)
        assert value == 1
        assert type(value) is int

    @pytest.mark.parametrize("field", _FLAGS)
    def test_omitted_flag_defaults_to_zero(self, field):
        _, conn = _create({"company_name": "Acme LLC"})
        assert _create_flag_param(conn, field) == 0

    def test_string_zero_is_not_compliant(self):
        _, conn = _create({"company_name": "Acme LLC", "flow_down_complete": "0"})
        assert _create_flag_param(conn, "flow_down_complete") == 0

    @pytest.mark.parametrize("value", [True, False, "0", "true", None, 1, 0])
    def test_create_and_update_agree(self, value):
        _, create_conn = _create({"company_name": "Acme LLC", "flow_down_complete": value})
        _, update_conn = _update({"flow_down_complete": value})
        assert _create_flag_param(create_conn, "flow_down_complete") == _update_flag_param(
            update_conn, "flow_down_complete"
        )


# ---------------------------------------------------------------------------
# Tests: non-flag fields are untouched, and the flag list is complete
# ---------------------------------------------------------------------------


class TestNonFlagFieldsAndFlagList:
    def test_company_name_passes_through(self):
        _, conn = _update({"company_name": "Acme LLC"})
        assert _update_flag_param(conn, "company_name") == "Acme LLC"

    def test_cmmc_level_passes_through_unchanged(self):
        """cmmc_level is 1-3, not a 0/1 flag — coercing it would clamp it to 1."""
        _, conn = _update({"cmmc_level": 3})
        assert _update_flag_param(conn, "cmmc_level") == 3

    def test_subcontract_value_passes_through_unchanged(self):
        _, conn = _update({"subcontract_value": 250000.0})
        assert _update_flag_param(conn, "subcontract_value") == 250000.0

    def test_status_passes_through(self):
        _, conn = _update({"status": "inactive"})
        assert _update_flag_param(conn, "status") == "inactive"

    def test_declared_flag_list_matches_this_test(self):
        from tools.govcon.subcontractor_tracker import COMPLIANCE_FLAGS

        assert set(COMPLIANCE_FLAGS) == set(_FLAGS)

    def test_every_declared_flag_is_updatable(self):
        """A flag the update path does not accept would be coerced by nobody."""
        import inspect

        from tools.govcon import subcontractor_tracker

        src = inspect.getsource(subcontractor_tracker.update_subcontractor)
        updatable = src.split("updatable = [", 1)[1].split("]", 1)[0]
        for field in subcontractor_tracker.COMPLIANCE_FLAGS:
            assert f'"{field}"' in updatable
