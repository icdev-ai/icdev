"""Unit tests for tools/iqe/executor.py — 6 tests with stub adapter."""
from __future__ import annotations

from tools.iqe.ast_nodes import AttrRef, BinOp, ForeachNode, Literal, SelectNode, WhereNode
from tools.iqe.executor import Executor, execute_query, register_collection

_DEVICES = [
    {"name": "rtr-01", "vendor": "Cisco", "bandwidth_gbps": 10},
    {"name": "rtr-02", "vendor": "Juniper", "bandwidth_gbps": 40},
    {"name": "sw-01", "vendor": "Cisco", "bandwidth_gbps": 1},
]


def _stub_adapter(_conn: object) -> list[dict]:
    return list(_DEVICES)


def _ast(
    var: str = "device",
    collection: str = "network.devices",
    where: list[WhereNode] | None = None,
    select: SelectNode | None = None,
) -> ForeachNode:
    return ForeachNode(
        var=var,
        collection=AttrRef(collection.split(".")),
        where_clauses=where or [],
        select=select,
    )


# 1 — register_collection + run returns all rows via adapter -----------------

def test_register_collection_returns_all_rows() -> None:
    ex = Executor()
    ex.register_collection("network.devices", _stub_adapter)
    result = ex.run(_ast(), conn=None)
    assert len(result) == 3
    assert result[0]["name"] == "rtr-01"


# 2 — WHERE == string filters correctly -------------------------------------

def test_where_eq_string_filter() -> None:
    ex = Executor()
    ex.register_collection("network.devices", _stub_adapter)
    ast = _ast(
        where=[WhereNode(BinOp("==", AttrRef(["device", "vendor"]), Literal("Cisco")))]
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert all(r["vendor"] == "Cisco" for r in result)


# 3 — WHERE > numeric filters correctly -------------------------------------

def test_where_gt_number_filter() -> None:
    ex = Executor()
    ex.register_collection("network.devices", _stub_adapter)
    ast = _ast(
        where=[WhereNode(BinOp(">", AttrRef(["device", "bandwidth_gbps"]), Literal(5)))]
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert all(r["bandwidth_gbps"] > 5 for r in result)


# 4 — SELECT projection keeps only named fields -----------------------------

def test_select_projection() -> None:
    ex = Executor()
    ex.register_collection("network.devices", _stub_adapter)
    ast = _ast(
        select=SelectNode(fields=[AttrRef(["device", "name"]), AttrRef(["device", "vendor"])])
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 3
    assert set(result[0].keys()) == {"name", "vendor"}
    assert "bandwidth_gbps" not in result[0]


# 5 — module-level execute_query convenience function ----------------------

def test_execute_query_convenience() -> None:
    register_collection("network.devices", _stub_adapter)
    result = execute_query(_ast(), conn=None)
    assert isinstance(result, list)
    assert len(result) == 3


# 6 — AND predicate narrows to single match ---------------------------------

def test_where_and_predicate() -> None:
    ex = Executor()
    ex.register_collection("network.devices", _stub_adapter)
    ast = _ast(
        where=[
            WhereNode(
                BinOp(
                    "and",
                    BinOp("==", AttrRef(["device", "vendor"]), Literal("Cisco")),
                    BinOp(">", AttrRef(["device", "bandwidth_gbps"]), Literal(5)),
                )
            )
        ]
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 1
    assert result[0]["name"] == "rtr-01"


# 7 — WHERE > against a NULL field excludes the row instead of raising ------
# (prop-vv-02: IQE smoke surfaced "'>' not supported between instances of
# 'NoneType' and 'int'" on real data with an unset numeric column.)

_DEVICES_WITH_NULL = _DEVICES + [{"name": "rtr-03", "vendor": "Cisco", "bandwidth_gbps": None}]


def _stub_adapter_with_null(_conn: object) -> list[dict]:
    return list(_DEVICES_WITH_NULL)


def test_where_gt_excludes_null_field_without_raising() -> None:
    ex = Executor()
    ex.register_collection("network.devices", _stub_adapter_with_null)
    ast = _ast(
        where=[WhereNode(BinOp(">", AttrRef(["device", "bandwidth_gbps"]), Literal(5)))]
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert all(r["bandwidth_gbps"] is not None and r["bandwidth_gbps"] > 5 for r in result)
