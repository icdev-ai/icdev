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


# ── `*` and `/` operands (parsed from grammar.lark's mul_expr) ───────────────

_CENTERS = [
    {"name": "hot", "load": 80.0, "cap": 100.0},
    {"name": "cool", "load": 10.0, "cap": 100.0},
    {"name": "no-cap", "load": 5.0, "cap": 0},
    {"name": "unknown", "load": None, "cap": 100.0},
    {"name": "text", "load": "80", "cap": 100.0},
    {"name": "flag", "load": True, "cap": 1},
]


def test_division_filters_on_the_ratio() -> None:
    from tools.iqe.parser import parse

    ex = Executor()
    ex.register_collection("dsoc.scrubbing_centers", lambda _c: list(_CENTERS))
    ast = parse("foreach s in dsoc.scrubbing_centers where s.load / s.cap > 0.7 select s.name")
    assert ex.run(ast, conn=None) == [{"name": "hot"}]


def test_multiplication_filters_on_the_product() -> None:
    from tools.iqe.parser import parse

    ex = Executor()
    # Numeric loads only: the bare `s.load` operand is compared as-is, and
    # comparing a str with a float is _compare's concern, not arithmetic's.
    numeric = [c for c in _CENTERS if isinstance(c["load"], float)]
    ex.register_collection("dsoc.scrubbing_centers", lambda _c: list(numeric))
    ast = parse("foreach s in dsoc.scrubbing_centers where s.load > s.cap * 0.7 select s.name")
    # 80 > 70 and 5 > 0 hold; 10 > 70 does not.
    assert ex.run(ast, conn=None) == [{"name": "hot"}, {"name": "no-cap"}]


def test_arithmetic_on_null_zero_text_or_bool_is_null_never_a_match() -> None:
    """Zero divisors, NULLs, strings and booleans give NULL, which matches no comparison."""
    from tools.iqe.parser import parse

    ex = Executor()
    ex.register_collection("dsoc.scrubbing_centers", lambda _c: list(_CENTERS))
    for op in (">", "<", ">=", "<=", "=="):
        ast = parse(
            f"foreach s in dsoc.scrubbing_centers where s.load / s.cap {op} 0.5 select s.name"
        )
        names = {r["name"] for r in ex.run(ast, conn=None)}
        assert names.isdisjoint({"no-cap", "unknown", "text", "flag"}), (op, names)


def test_arithmetic_overflow_is_null() -> None:
    from tools.iqe.parser import parse

    ex = Executor()
    ex.register_collection("t.x", lambda _c: [{"name": "big", "a": 1e308}])
    ast = parse("foreach s in t.x where s.a * 10 > 1 select s.name")
    assert ex.run(ast, conn=None) == []
