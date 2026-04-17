"""8 tests for IQE parser and AST node classes (dt-iqe-03)."""
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
