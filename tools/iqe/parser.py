"""IQE — hand-rolled tokenizer + recursive-descent parser.

Grammar (EBNF):
    query      := "foreach" IDENT "in" collection ("where" predicate)* "select" proj_list
    collection := IDENT ("." IDENT)*  |  IDENT "(" STRING ")"
    predicate  := or_expr
    or_expr    := and_expr ("or" and_expr)*
    and_expr   := not_expr ("and" not_expr)*
    not_expr   := "not" not_expr  |  comparison
    comparison := path op value  |  path "contains" value  |  path "startswith" value
    op         := "==" | "!=" | ">" | "<" | ">=" | "<="
    path       := IDENT ("." IDENT)*
    value      := STRING | NUMBER | "true" | "false" | "null" | path
    proj_list  := projection ("," projection)*
    projection := path | "*"

Air-gap safe: zero external dependencies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── AST nodes ─────────────────────────────────────────────────────────────────

@dataclass
class Path:
    parts: list[str]

    def __str__(self) -> str:
        return ".".join(self.parts)


@dataclass
class Value:
    val: Any  # str | int | float | bool | None


@dataclass
class Call:
    func: str
    args: list[Any]


@dataclass
class BinaryOp:
    op: str   # ==, !=, >, <, >=, <=, contains, startswith
    left: Any
    right: Any


@dataclass
class LogicalAnd:
    exprs: list[Any]


@dataclass
class LogicalOr:
    exprs: list[Any]


@dataclass
class LogicalNot:
    expr: Any


@dataclass
class Query:
    var: str
    collection: Any         # Path or Call
    predicates: list[Any]   # each WHERE clause is a separate predicate (AND-combined)
    projections: list[Any]  # list of Path or "*"


# ── Tokenizer ─────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r'(?P<STRING>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')'
    r'|(?P<NUMBER>-?\d+(?:\.\d+)?)'
    r'|(?P<GTE>>=)'
    r'|(?P<LTE><=)'
    r'|(?P<NEQ>!=)'
    r'|(?P<EQ>==)'
    r'|(?P<GT>>)'
    r'|(?P<LT><)'
    r'|(?P<DOT>\.)'
    r'|(?P<COMMA>,)'
    r'|(?P<LPAREN>\()'
    r'|(?P<RPAREN>\))'
    r'|(?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)'
    r'|(?P<SKIP>\s+)'
    r'|(?P<MISMATCH>.)',
    re.DOTALL,
)

_KEYWORDS = frozenset({
    "foreach", "in", "where", "select",
    "and", "or", "not",
    "contains", "startswith",
    "true", "false", "null",
})


@dataclass
class Token:
    kind: str
    value: str


def _tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        val = m.group()
        if kind == "SKIP":
            continue
        if kind == "MISMATCH":
            raise SyntaxError(f"IQE unexpected character {val!r} at pos {m.start()}")
        if kind == "IDENT" and val.lower() in _KEYWORDS:
            kind = val.upper()
            val = val.lower()
        tokens.append(Token(kind, val))
    tokens.append(Token("EOF", ""))
    return tokens


# ── Parser ────────────────────────────────────────────────────────────────────

class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._t = tokens
        self._pos = 0

    def _peek(self) -> Token:
        return self._t[self._pos]

    def _consume(self, kind: str | None = None) -> Token:
        tok = self._t[self._pos]
        if kind and tok.kind != kind:
            raise SyntaxError(
                f"IQE expected {kind!r} but got {tok.kind!r} ({tok.value!r})"
            )
        self._pos += 1
        return tok

    def _match(self, *kinds: str) -> bool:
        return self._peek().kind in kinds

    # ── Grammar rules ──────────────────────────────────────────────────────────

    def parse_query(self) -> Query:
        self._consume("FOREACH")
        var = self._consume("IDENT").value
        self._consume("IN")
        collection = self._parse_collection()

        predicates: list[Any] = []
        while self._match("WHERE"):
            self._consume("WHERE")
            predicates.append(self._parse_or())

        self._consume("SELECT")
        projections = self._parse_proj_list()
        self._consume("EOF")
        return Query(var=var, collection=collection, predicates=predicates, projections=projections)

    def _parse_collection(self) -> Any:
        name = self._consume("IDENT").value
        if self._match("LPAREN"):
            # function call: framework("FedRAMP Moderate")
            self._consume("LPAREN")
            args: list[Any] = []
            if not self._match("RPAREN"):
                args.append(self._parse_value())
                while self._match("COMMA"):
                    self._consume("COMMA")
                    args.append(self._parse_value())
            self._consume("RPAREN")
            return Call(func=name, args=args)
        parts = [name]
        while self._match("DOT"):
            self._consume("DOT")
            parts.append(self._consume("IDENT").value)
        return Path(parts=parts)

    def _parse_proj_list(self) -> list[Any]:
        projections: list[Any] = []
        projections.append(self._parse_projection())
        while self._match("COMMA"):
            self._consume("COMMA")
            projections.append(self._parse_projection())
        return projections

    def _parse_projection(self) -> Any:
        if self._match("IDENT"):
            return self._parse_path()
        raise SyntaxError(f"IQE expected projection but got {self._peek().kind!r}")

    def _parse_or(self) -> Any:
        left = self._parse_and()
        while self._match("OR"):
            self._consume("OR")
            right = self._parse_and()
            left = LogicalOr(exprs=[left, right])
        return left

    def _parse_and(self) -> Any:
        left = self._parse_not()
        while self._match("AND"):
            self._consume("AND")
            right = self._parse_not()
            left = LogicalAnd(exprs=[left, right])
        return left

    def _parse_not(self) -> Any:
        if self._match("NOT"):
            self._consume("NOT")
            return LogicalNot(expr=self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Any:
        left = self._parse_path()
        op_map = {
            "EQ": "==", "NEQ": "!=",
            "GT": ">", "LT": "<", "GTE": ">=", "LTE": "<=",
        }
        if self._peek().kind in op_map:
            op = op_map[self._consume().kind]
            right = self._parse_value()
            return BinaryOp(op=op, left=left, right=right)
        if self._match("CONTAINS"):
            self._consume("CONTAINS")
            right = self._parse_value()
            return BinaryOp(op="contains", left=left, right=right)
        if self._match("STARTSWITH"):
            self._consume("STARTSWITH")
            right = self._parse_value()
            return BinaryOp(op="startswith", left=left, right=right)
        raise SyntaxError(
            f"IQE expected comparison operator but got {self._peek().kind!r} ({self._peek().value!r})"
        )

    def _parse_path(self) -> Path:
        parts = [self._consume("IDENT").value]
        while self._match("DOT"):
            self._consume("DOT")
            parts.append(self._consume("IDENT").value)
        return Path(parts=parts)

    def _parse_value(self) -> Any:
        tok = self._peek()
        if tok.kind == "STRING":
            self._consume()
            s = tok.value[1:-1]  # strip quotes
            s = s.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
            return Value(val=s)
        if tok.kind == "NUMBER":
            self._consume()
            val: int | float = float(tok.value) if "." in tok.value else int(tok.value)
            return Value(val=val)
        if tok.kind == "TRUE":
            self._consume()
            return Value(val=True)
        if tok.kind == "FALSE":
            self._consume()
            return Value(val=False)
        if tok.kind == "NULL":
            self._consume()
            return Value(val=None)
        if tok.kind == "IDENT":
            # bare identifier treated as a path reference
            return self._parse_path()
        raise SyntaxError(f"IQE expected value but got {tok.kind!r} ({tok.value!r})")


# ── Public API ────────────────────────────────────────────────────────────────

def parse(query_text: str) -> Query:
    """Parse an IQE query string and return a Query AST node."""
    tokens = _tokenize(query_text.strip())
    return _Parser(tokens).parse_query()
