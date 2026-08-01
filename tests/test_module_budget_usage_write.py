"""record_module_usage must actually persist a row.

The module had no test asserting that a usage row reaches the database, and
that gap is the whole story. ``record_module_usage`` passed an explicit
``_gen_id("mbu-")`` string into ``id INTEGER PRIMARY KEY AUTOINCREMENT``
(``SERIAL`` once translated for PostgreSQL), so every insert failed on every
backend -- InvalidTextRepresentation on PG, "datatype mismatch" on SQLite.
Both production call sites wrapped it in ``except Exception: pass``, so the
failure was invisible: ``module_budget_usage`` never received a row and budget
enforcement read a table that could never fill.

Existing tests all mocked ``record_module_usage`` or asserted only on its
return value, so a function that persisted nothing passed everything. These
tests assert the row is in the database afterwards, which is the only claim
that would have caught it.
"""
import uuid

import pytest

from tools.budget import module_budget_tracker as mbt

MODULE = "generative_intelligence"


@pytest.fixture
def probe_fn():
    """A function_name unique to this test invocation.

    The test database is a persistent file shared across the session and across
    runs, so a fixed marker accumulates rows from every previous run and any
    "exactly N rows" assertion drifts. Scoping each test to its own marker
    makes these tests independent of history and of execution order.
    """
    return f"probe-{uuid.uuid4().hex[:12]}"


def _usage_rows(function_name):
    conn = mbt._get_conn()
    try:
        mbt._ensure_tables(conn)
        rows = conn.execute(
            "SELECT id, module_name, function_name, amount, tokens, operations "
            "FROM module_budget_usage WHERE function_name = %s",
            (function_name,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def test_usage_row_is_actually_persisted(probe_fn):
    """The claim no previous test made: the row reaches the database."""
    fn = probe_fn
    record_id = mbt.record_module_usage(
        MODULE, cost_usd=0.0123, tokens=77, operations=3, function=fn
    )

    rows = _usage_rows(fn)
    assert len(rows) == 1, f"expected exactly one persisted row, got {rows}"

    row = rows[0]
    assert row["module_name"] == MODULE
    assert row["tokens"] == 77
    assert row["operations"] == 3
    assert row["amount"] == pytest.approx(0.0123)

    # The returned id identifies the row the database actually assigned.
    assert record_id, "record_module_usage returned an empty id"
    assert str(row["id"]) == record_id


def test_returned_id_is_database_assigned_not_client_generated(probe_fn):
    """The id comes from the sequence, not from a client-side string.

    Pins the fix directly: a regression that reintroduces a generated
    ``mbu-...`` id would both fail the insert and produce a non-numeric id.
    """
    record_id = mbt.record_module_usage(MODULE, cost_usd=0.01, function=probe_fn)
    assert record_id.isdigit(), f"expected a sequence-assigned integer id, got {record_id!r}"
    assert not record_id.startswith("mbu-")


def test_successive_calls_persist_distinct_rows(probe_fn):
    """Two calls produce two rows with different ids."""
    fn = probe_fn
    first = mbt.record_module_usage(MODULE, cost_usd=0.01, function=fn)
    second = mbt.record_module_usage(MODULE, cost_usd=0.02, function=fn)

    assert first != second
    assert len(_usage_rows(fn)) == 2


def test_unknown_module_writes_nothing_and_returns_empty(probe_fn):
    fn = probe_fn
    assert mbt.record_module_usage("not_a_real_module", cost_usd=1.0, function=fn) == ""
    assert _usage_rows(fn) == []


def test_recorded_usage_reaches_the_period_aggregate(probe_fn):
    """End-to-end: recorded spend is what budget enforcement reads.

    ``check_module_budget`` decides block/warn/allow from the period
    aggregate, which is derived from ``module_budget_usage``. With every insert
    failing, that aggregate was permanently zero -- so this asserts the number
    the gate actually consumes moves when usage is recorded.
    """
    before = mbt.check_module_budget(MODULE)["spent_usd"]
    mbt.record_module_usage(MODULE, cost_usd=0.25, function=probe_fn)
    after = mbt.check_module_budget(MODULE)["spent_usd"]

    assert after == pytest.approx(before + 0.25), (
        f"period aggregate did not reflect recorded usage: {before} -> {after}"
    )
