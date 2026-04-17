from __future__ import annotations

from pathlib import Path

from lark import Lark, Transformer, UnexpectedInput

from tools.iqe.ast_nodes import (
    AttrRef,
    BinOp,
    ForeachNode,
    Literal,
    SelectNode,
    WhereNode,
)


class IQESyntaxError(SyntaxError):
    def __init__(self, msg: str, line: int | None = None, col: int | None = None) -> None:
        super().__init__(msg)
        self.line = line
        self.col = col


_GRAMMAR = (Path(__file__).parent / "grammar.lark").read_text(encoding="utf-8")
_lark = Lark(_GRAMMAR, parser="lalr", start="query")


class _IQETransformer(Transformer):
    def query(self, items):
        var = str(items[0])
        collection = items[1]
        where_clauses = [i for i in items[2:-1] if isinstance(i, WhereNode)]
        select = items[-1]
        return ForeachNode(var=var, collection=collection, where_clauses=where_clauses, select=select)

    def collection(self, items):
        return AttrRef(parts=[str(t) for t in items])

    def where_clause(self, items):
        return WhereNode(predicate=items[0])

    def select_list(self, items):
        return SelectNode(fields=list(items))

    def select_expr(self, items):
        return AttrRef(parts=[str(t) for t in items])

    def and_op(self, items):
        return BinOp(left=items[0], op="and", right=items[1])

    def or_op(self, items):
        return BinOp(left=items[0], op="or", right=items[1])

    def atom_pred(self, items):
        return BinOp(left=items[0], op=str(items[1]), right=items[2])

    def attr_ref(self, items):
        return AttrRef(parts=[str(t) for t in items])

    def string_lit(self, items):
        raw = str(items[0])
        return Literal(value=raw[1:-1])

    def number_lit(self, items):
        raw = str(items[0])
        return Literal(value=float(raw) if "." in raw else int(raw))


_transformer = _IQETransformer()


def parse(query_str: str) -> ForeachNode:
    """Parse an IQE query string and return a ForeachNode AST.

    Raises IQESyntaxError with line/col for invalid input.
    """
    try:
        tree = _lark.parse(query_str)
        return _transformer.transform(tree)
    except UnexpectedInput as exc:
        raise IQESyntaxError(
            str(exc),
            line=getattr(exc, "line", None),
            col=getattr(exc, "column", None),
        ) from exc
