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


# ── Honest fallback: an unreadable collection is an error, never [] ──────────

def test_unregistered_collection_without_a_connection_is_an_error() -> None:
    import pytest

    from tools.iqe.executor import CollectionUnavailableError
    from tools.iqe.parser import parse

    with pytest.raises(CollectionUnavailableError) as exc:
        Executor().run(parse("foreach i in network.interfaces select i.name"), conn=None)
    assert exc.value.collection == "network.interfaces"
    assert "no adapter" in str(exc.value)


def test_unregistered_collection_with_no_table_is_an_error_not_a_db_error() -> None:
    import sqlite3

    import pytest

    from tools.iqe.executor import CollectionUnavailableError
    from tools.iqe.parser import parse

    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(CollectionUnavailableError) as exc:
            Executor().run(parse("foreach i in network.interfaces select i.name"), conn=conn)
    finally:
        conn.close()
    assert exc.value.collection == "network.interfaces"
    # Our own words, not the driver's: no raw database error text leaks out.
    assert "no such table" not in str(exc.value)


def test_unregistered_collection_with_a_table_still_falls_back() -> None:
    import sqlite3

    from tools.iqe.parser import parse

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE interfaces (name TEXT)")
        conn.execute("INSERT INTO interfaces VALUES ('eth0')")
        rows = Executor().run(parse("foreach i in network.interfaces select i.name"), conn=conn)
    finally:
        conn.close()
    assert rows == [{"name": "eth0"}]


# ── NULL arithmetic follows SQL three-valued logic ───────────────────────────

_NULLS = [
    {"name": "zero", "a": 5, "b": 0},
    {"name": "none", "a": None, "b": 2},
    {"name": "text", "a": "5", "b": 2},
    {"name": "ten", "a": 20, "b": 2},
]


def _names(query: str) -> set[str]:
    from tools.iqe.parser import parse

    ex = Executor()
    ex.register_collection("t.x", lambda _c: list(_NULLS))
    return {r["name"] for r in ex.run(parse(query), conn=None)}


def test_null_arithmetic_fails_not_equal_too() -> None:
    # 5/0, None/2 and "5"/2 are NULL; NULL != 1 is unknown, so only 20/2 matches.
    assert _names("foreach s in t.x where s.a / s.b != 1 select s.name") == {"ten"}


def test_null_arithmetic_does_not_equal_null() -> None:
    assert _names("foreach s in t.x where s.a / s.b == null select s.name") == set()


def test_not_of_a_null_arithmetic_comparison_is_still_unknown() -> None:
    assert _names("foreach s in t.x where not s.a / s.b > 100 select s.name") == {"ten"}


def test_null_arithmetic_in_or_and_and_is_three_valued() -> None:
    # unknown OR true is true; unknown AND true is unknown.
    assert _names(
        'foreach s in t.x where s.a / s.b > 1 or s.name == "zero" select s.name'
    ) == {"ten", "zero"}
    assert _names(
        'foreach s in t.x where s.a / s.b > 1 and s.name != "x" select s.name'
    ) == {"ten"}


def test_missing_attribute_semantics_are_unchanged() -> None:
    """A missing attribute is still None, and None != x still holds (pre-existing)."""
    assert _names("foreach s in t.x where s.missing != 1 select s.name") == {
        "zero", "none", "text", "ten"}
    assert _names("foreach s in t.x where s.missing == null select s.name") == {
        "zero", "none", "text", "ten"}


def test_decimal_operands_are_numbers() -> None:
    """psycopg returns NUMERIC as Decimal; it multiplies and divides like a number."""
    from decimal import Decimal

    from tools.iqe.parser import parse

    ex = Executor()
    ex.register_collection("t.x", lambda _c: [
        {"name": "dec", "load": Decimal("80.5"), "cap": Decimal(100)},
        {"name": "mixed", "load": Decimal(80), "cap": 100.0},
        {"name": "low", "load": Decimal(1), "cap": 100},
        {"name": "dzero", "load": Decimal(1), "cap": Decimal(0)},
    ])
    rows = ex.run(parse("foreach s in t.x where s.load / s.cap > 0.7 select s.name"), conn=None)
    assert [r["name"] for r in rows] == ["dec", "mixed"]
