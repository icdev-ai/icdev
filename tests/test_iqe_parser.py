"""Tests for IQE parser — AST node classes and grammar (dt-iqe-03)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.iqe.parser import IQESyntaxError, parse
from tools.iqe.ast_nodes import AttrRef, BinOp, ForeachNode, Literal, SelectNode, WhereNode


def test_parse_returns_foreach_node():
    result = parse("foreach x in devices select x.name")
    assert isinstance(result, ForeachNode)


def test_var_name_extracted():
    result = parse("foreach device in network.devices select device.hostname")
    assert result.var == "device"


def test_dotted_collection_is_attr_ref():
    result = parse("foreach d in network.devices select d.hostname")
    assert isinstance(result.collection, AttrRef)
    assert result.collection.parts == ["network", "devices"]


def test_no_where_clause_gives_empty_list():
    result = parse("foreach x in resources select x.id")
    assert result.where_clauses == []


def test_where_clause_is_where_node():
    result = parse('foreach d in devices where d.vendor == "cisco" select d.name')
    assert len(result.where_clauses) == 1
    assert isinstance(result.where_clauses[0], WhereNode)


def test_where_binop_operator_and_string_literal():
    result = parse('foreach d in devices where d.vendor == "cisco" select d.name')
    pred = result.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == "=="
    assert isinstance(pred.right, Literal)
    assert pred.right.value == "cisco"


def test_select_multiple_fields_is_select_node():
    result = parse("foreach d in devices select d.name, d.version")
    assert isinstance(result.select, SelectNode)
    assert len(result.select.fields) == 2
    assert all(isinstance(f, AttrRef) for f in result.select.fields)


def test_invalid_syntax_raises_iqe_syntax_error_with_line_col():
    with pytest.raises(IQESyntaxError) as exc_info:
        parse("foreach x devices select x.name")  # missing "in"
    err = exc_info.value
    assert err.line is not None
    assert err.col is not None
