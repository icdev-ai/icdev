# CUI // SP-CTI
"""Unit tests for IQE executor Phase II fan-out: union() and join() built-ins.

Tests cover:
  - union: merge, order, empty source, full parse+execute round-trip
  - join:  inner join, no-match, field conflict, full parse+execute round-trip
  - module-level helpers: _python_join, _duckdb_join (if duckdb installed)
"""
from __future__ import annotations

import pytest

from tools.iqe.executor import Executor, _python_join
from tools.iqe.parser import parse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_executor_with_stubs() -> tuple[Executor, list[dict], list[dict]]:
    """Return an Executor pre-loaded with two stub collections."""
    incidents = [
        {"id": "INC001", "hostname": "web-01", "severity": "high"},
        {"id": "INC002", "hostname": "db-01",  "severity": "low"},
    ]
    assets = [
        {"asset_id": "A1", "hostname": "web-01", "os": "RHEL8"},
        {"asset_id": "A2", "hostname": "api-02", "os": "Ubuntu"},
    ]

    ex = Executor()
    ex.register_collection("stub.incidents", lambda conn: incidents)
    ex.register_collection("stub.assets",    lambda conn: assets)
    return ex, incidents, assets


# ---------------------------------------------------------------------------
# UNION tests
# ---------------------------------------------------------------------------

def test_union_merges_two_collections() -> None:
    ex, incidents, assets = _make_executor_with_stubs()
    ast = parse('foreach x in union("stub.incidents", "stub.assets") select *')
    rows = ex.run(ast, conn=None)
    assert len(rows) == 4
    ids = {r.get("id") or r.get("asset_id") for r in rows}
    assert "INC001" in ids
    assert "A1" in ids


def test_union_preserves_source_order() -> None:
    """Rows from the first collection must precede rows from the second."""
    ex, incidents, assets = _make_executor_with_stubs()
    ast = parse('foreach x in union("stub.incidents", "stub.assets") select *')
    rows = ex.run(ast, conn=None)
    # First two rows are from incidents (have 'id'), next two from assets (have 'asset_id')
    assert "id" in rows[0]
    assert "id" in rows[1]
    assert "asset_id" in rows[2]
    assert "asset_id" in rows[3]


def test_union_handles_empty_source() -> None:
    """A union where one collection is empty should still return the other's rows."""
    ex = Executor()
    ex.register_collection("stub.empty",    lambda conn: [])
    ex.register_collection("stub.nonempty", lambda conn: [{"x": 1}, {"x": 2}])

    ast = parse('foreach r in union("stub.empty", "stub.nonempty") select *')
    rows = ex.run(ast, conn=None)
    assert len(rows) == 2


def test_union_with_where_filter() -> None:
    """WHERE predicate is applied after merging all sources."""
    ex, _incidents, _assets = _make_executor_with_stubs()
    ast = parse(
        'foreach x in union("stub.incidents", "stub.assets") '
        'where x.hostname == "web-01" '
        'select x.hostname'
    )
    rows = ex.run(ast, conn=None)
    # web-01 appears in both incidents AND assets
    assert len(rows) == 2
    assert all(r["hostname"] == "web-01" for r in rows)


def test_union_iqe_syntax_roundtrip() -> None:
    """Full parse → execute cycle over union() collection call."""
    ex = Executor()
    ex.register_collection("col.a", lambda conn: [{"v": 1}, {"v": 2}])
    ex.register_collection("col.b", lambda conn: [{"v": 3}])
    ast = parse('foreach r in union("col.a", "col.b") select r.v')
    rows = ex.run(ast, conn=None)
    assert {r["v"] for r in rows} == {1, 2, 3}


# ---------------------------------------------------------------------------
# JOIN tests
# ---------------------------------------------------------------------------

def test_join_inner_join_on_shared_key() -> None:
    ex, _incidents, _assets = _make_executor_with_stubs()
    ast = parse('foreach r in join("stub.incidents", "stub.assets", "hostname") select *')
    rows = ex.run(ast, conn=None)
    # Only web-01 is in both collections
    assert len(rows) == 1
    assert rows[0]["hostname"] == "web-01"
    # Fields from both sides are present
    assert "id" in rows[0]       # from incidents
    assert "asset_id" in rows[0]  # from assets


def test_join_no_matching_keys_returns_empty() -> None:
    ex = Executor()
    ex.register_collection("left.col",  lambda conn: [{"k": "aaa", "v": 1}])
    ex.register_collection("right.col", lambda conn: [{"k": "bbb", "v": 2}])
    ast = parse('foreach r in join("left.col", "right.col", "k") select *')
    rows = ex.run(ast, conn=None)
    assert rows == []


def test_join_merges_unique_fields_from_both_sides() -> None:
    """Unique (non-overlapping) fields from both sides appear in the merged row."""
    ex = Executor()
    ex.register_collection("left.col",  lambda conn: [{"k": "x", "left_field": "L"}])
    ex.register_collection("right.col", lambda conn: [{"k": "x", "right_field": "R", "extra": "E"}])
    ast = parse('foreach r in join("left.col", "right.col", "k") select *')
    rows = ex.run(ast, conn=None)
    assert len(rows) == 1
    assert rows[0]["k"] == "x"
    assert rows[0]["left_field"] == "L"
    assert rows[0]["right_field"] == "R"
    assert rows[0]["extra"] == "E"


def test_join_iqe_syntax_roundtrip() -> None:
    """Full parse → execute cycle over join() collection call."""
    ex, _incidents, _assets = _make_executor_with_stubs()
    ast = parse(
        'foreach r in join("stub.incidents", "stub.assets", "hostname") '
        'select r.id, r.asset_id, r.hostname, r.os'
    )
    rows = ex.run(ast, conn=None)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "INC001"
    assert row["asset_id"] == "A1"
    assert row["os"] == "RHEL8"


# ---------------------------------------------------------------------------
# _python_join unit tests
# ---------------------------------------------------------------------------

def test_python_join_basic() -> None:
    left  = [{"k": 1, "lv": "a"}, {"k": 2, "lv": "b"}]
    right = [{"k": 1, "rv": "x"}, {"k": 3, "rv": "z"}]
    result = _python_join(left, right, "k")
    assert len(result) == 1
    assert result[0]["lv"] == "a"
    assert result[0]["rv"] == "x"


def test_python_join_one_to_many() -> None:
    left  = [{"k": "A", "side": "left"}]
    right = [{"k": "A", "n": 1}, {"k": "A", "n": 2}]
    result = _python_join(left, right, "k")
    assert len(result) == 2
    assert {r["n"] for r in result} == {1, 2}
    assert all(r["side"] == "left" for r in result)


# ---------------------------------------------------------------------------
# _duckdb_join (optional — skipped when duckdb not installed)
# ---------------------------------------------------------------------------

duckdb = pytest.importorskip("duckdb", reason="duckdb not installed")


def test_duckdb_join_basic() -> None:
    from tools.iqe.executor import _duckdb_join

    left  = [{"hostname": "web-01", "severity": "high"}]
    right = [{"hostname": "web-01", "os": "RHEL8"}]
    result = _duckdb_join(left, right, "hostname")
    assert len(result) == 1
    assert result[0]["os"] == "RHEL8"
