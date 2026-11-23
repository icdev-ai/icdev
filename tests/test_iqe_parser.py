"""10 tests for IQE parser and AST node classes (dt-iqe-04)."""
import pytest

from tools.iqe.ast_nodes import AttrRef, BinOp, ForeachNode, Literal, SelectNode, WhereNode
from tools.iqe.parser import IQESyntaxError, parse


def test_simple_foreach_select():
    """Minimal query with no where clause returns a ForeachNode."""
    q = parse("foreach device in network.devices select device.label")
    assert isinstance(q, ForeachNode)
    assert q.var == "device"
    assert q.collection == AttrRef(["network", "devices"])
    assert q.where_clauses == []
    assert isinstance(q.select, SelectNode)
    assert q.select.fields == [AttrRef(["device", "label"])]
    assert q.select.wildcard is False


def test_where_eq_string():
    """WHERE clause with string equality produces correct BinOp."""
    q = parse('foreach d in network.devices where d.vendor == "cisco" select d.hostname')
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == "=="
    assert pred.left == AttrRef(["d", "vendor"])
    assert pred.right == Literal("cisco")


def test_where_gt_number():
    """WHERE clause with numeric greater-than comparison."""
    q = parse("foreach c in network.circuits where c.cost > 5000 select c.circuit_id")
    pred = q.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == ">"
    assert pred.left == AttrRef(["c", "cost"])
    assert pred.right == Literal(5000)


def test_where_ne_operator():
    """WHERE clause with != operator and string value."""
    q = parse('foreach s in network.sites where s.country_code != "US" select s.site_id')
    pred = q.where_clauses[0].predicate
    assert pred.op == "!="
    assert pred.left == AttrRef(["s", "country_code"])
    assert pred.right == Literal("US")


def test_multiple_where_clauses():
    """Multiple 'where' keywords produce multiple WhereNode entries."""
    q = parse(
        'foreach f in network.findings '
        'where f.severity == "CAT1" '
        'where f.status == "open" '
        'select f.rule_id'
    )
    assert len(q.where_clauses) == 2
    assert all(isinstance(w, WhereNode) for w in q.where_clauses)
    assert q.where_clauses[0].predicate.right == Literal("CAT1")
    assert q.where_clauses[1].predicate.right == Literal("open")


def test_select_multiple_fields():
    """SELECT with comma-separated fields populates SelectNode.fields."""
    q = parse(
        "foreach c in network.circuits select c.circuit_id, c.carrier, c.bandwidth"
    )
    assert q.select.wildcard is False
    assert len(q.select.fields) == 3
    assert q.select.fields[0] == AttrRef(["c", "circuit_id"])
    assert q.select.fields[1] == AttrRef(["c", "carrier"])
    assert q.select.fields[2] == AttrRef(["c", "bandwidth"])


def test_syntax_error_missing_select():
    """Query without SELECT clause raises IQESyntaxError."""
    with pytest.raises(IQESyntaxError):
        parse("foreach d in network.devices")


def test_syntax_error_has_line_col():
    """IQESyntaxError exposes integer .line and .col attributes."""
    # 'in' where a variable name (IDENT) is required
    with pytest.raises(IQESyntaxError) as exc_info:
        parse("foreach in network.devices select d.label")
    err = exc_info.value
    assert hasattr(err, "line")
    assert hasattr(err, "col")
    assert isinstance(err.line, int)
    assert isinstance(err.col, int)


def test_where_and_connective():
    """Single WHERE clause with 'and' produces a nested BinOp AST."""
    q = parse(
        'foreach d in network.devices '
        'where d.vendor == "cisco" and d.active == true '
        'select d.hostname'
    )
    assert len(q.where_clauses) == 1
    top = q.where_clauses[0].predicate
    assert isinstance(top, BinOp)
    assert top.op == "and"
    assert isinstance(top.left, BinOp)
    assert top.left.op == "=="
    assert top.left.left == AttrRef(["d", "vendor"])
    assert top.left.right == Literal("cisco")
    assert isinstance(top.right, BinOp)
    assert top.right.op == "=="
    assert top.right.right == Literal(True)


def test_select_wildcard():
    """'select *' produces a SelectNode with wildcard=True and no fields."""
    q = parse("foreach d in network.devices select *")
    assert isinstance(q.select, SelectNode)
    assert q.select.wildcard is True
    assert q.select.fields == []
    assert q.where_clauses == []


# ── `//` line comments (tools/iqe/grammar.lark: %ignore /\/\/.*/) ─────────────

def test_double_slash_line_comment_is_ignored():
    """A `//` comment line (grammar.lark ignores them) parses like a `#` one."""
    q = parse(
        "// IQE-DR-002: executive runs\n"
        "foreach r in demo_runner.runs\n"
        'where r.audience == "exec"  // trailing comment\n'
        "select r.run_id\n"
    )
    assert q.var == "r"
    assert q.where_clauses[0].predicate == BinOp("==", AttrRef(["r", "audience"]), Literal("exec"))
    assert [str(f) for f in q.select.fields] == ["r.run_id"]


def test_double_slash_inside_a_string_is_not_a_comment():
    q = parse('foreach s in ext.sites where s.url startswith "https://x" select s.url')
    assert q.where_clauses[0].predicate.right == Literal("https://x")


def test_single_slash_outside_an_expression_is_still_an_error():
    with pytest.raises(IQESyntaxError):
        parse("/ foreach d in network.devices select d.name")


# ── `*` and `/` in comparison operands (grammar.lark: mul_expr) ──────────────

def test_division_in_comparison_left_operand():
    q = parse(
        "foreach s in dsoc.scrubbing_centers\n"
        "where s.current_load_gbps / s.capacity_gbps > 0.7\n"
        "select s.name"
    )
    pred = q.where_clauses[0].predicate
    assert pred == BinOp(
        ">",
        BinOp("/", AttrRef(["s", "current_load_gbps"]), AttrRef(["s", "capacity_gbps"])),
        Literal(0.7),
    )


def test_multiplication_on_the_right_is_left_associative():
    q = parse("foreach s in t.x where s.load > s.cap * 0.7 / 2 select s.name")
    pred = q.where_clauses[0].predicate
    assert pred.op == ">"
    assert pred.right == BinOp(
        "/", BinOp("*", AttrRef(["s", "cap"]), Literal(0.7)), Literal(2)
    )


def test_select_star_still_parses_as_wildcard_with_arithmetic_available():
    q = parse("foreach s in t.x where s.a * 2 > 1 select *")
    assert q.select.wildcard is True


def test_dangling_arithmetic_operator_is_an_error():
    with pytest.raises(IQESyntaxError):
        parse("foreach s in t.x where s.a / > 1 select s.a")
