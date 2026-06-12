"""IQE query parser — hand-rolled recursive-descent, zero external dependencies."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Union

from tools.iqe.ast_nodes import (
    AttrRef,
    BinOp,
    CollectionCall,
    ForeachNode,
    Literal,
    SelectNode,
    WhereNode,
)

# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

_KEYWORDS: frozenset[str] = frozenset(
    {
        "foreach", "in", "where", "select",
        "and", "or", "not", "contains", "startswith",
        "true", "false", "null",
    }
)

_TOKEN_SPEC = [
    ("STRING",   r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\''),
    ("NUMBER",   r"-?\d+(?:\.\d+)?"),
    ("OP",       r"==|!=|>=|<=|>|<"),
    ("COMMA",    r","),
    ("DOT",      r"\."),
    ("STAR",     r"\*"),
    ("LPAREN",   r"\("),
    ("RPAREN",   r"\)"),
    ("IDENT",    r"[A-Za-z_]\w*"),
    ("NL",       r"\n"),
    ("SKIP",     r"[ \t\r]+|#[^\n]*"),
    ("MISMATCH", r"."),
]

_TOKEN_RE = re.compile(
    "|".join(f"(?P<{name}>{pat})" for name, pat in _TOKEN_SPEC)
)


@dataclass
class _Tok:
    type: str
    val: str
    line: int
    col: int


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class IQESyntaxError(SyntaxError):
    """Raised for IQE parse errors; carries line + col."""

    def __init__(self, msg: str, line: int = 0, col: int = 0) -> None:
        super().__init__(msg)
        self.msg = msg
        self.line = line
        self.col = col

    def __str__(self) -> str:
        return f"{self.msg} (line {self.line}, col {self.col})"


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


def _tokenize(src: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    line = 1
    line_start = 0
    for m in _TOKEN_RE.finditer(src):
        kind = m.lastgroup
        val = m.group()
        col = m.start() - line_start + 1
        if kind == "NL":
            line += 1
            line_start = m.end()
            continue
        if kind == "SKIP":
            continue
        if kind == "MISMATCH":
            raise IQESyntaxError(f"Unexpected character: {val!r}", line, col)
        if kind == "IDENT" and val in _KEYWORDS:
            kind = "KEYWORD"
        tokens.append(_Tok(kind, val, line, col))
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[_Tok]) -> None:
        self._toks = tokens
        self._pos = 0

    def _peek(self) -> Optional[_Tok]:
        return self._toks[self._pos] if self._pos < len(self._toks) else None

    def _advance(self) -> _Tok:
        tok = self._toks[self._pos]
        self._pos += 1
        return tok

    def _expect_kw(self, word: str) -> _Tok:
        tok = self._peek()
        if tok is None or tok.type != "KEYWORD" or tok.val != word:
            line, col = (tok.line, tok.col) if tok else (0, 0)
            raise IQESyntaxError(f"Expected '{word}'", line, col)
        return self._advance()

    def _expect_type(self, tp: str) -> _Tok:
        tok = self._peek()
        if tok is None or tok.type != tp:
            line, col = (tok.line, tok.col) if tok else (0, 0)
            raise IQESyntaxError(f"Expected {tp}", line, col)
        return self._advance()

    def _match_kw(self, *words: str) -> Optional[_Tok]:
        tok = self._peek()
        if tok and tok.type == "KEYWORD" and tok.val in words:
            return self._advance()
        return None

    # --- grammar rules ---

    def parse_query(self) -> ForeachNode:
        self._expect_kw("foreach")
        var_tok = self._expect_type("IDENT")
        self._expect_kw("in")
        collection_ref = self._parse_attr_ref()
        if self._peek() and self._peek().type == "LPAREN":
            self._advance()  # consume '('
            call_args: list[Literal] = []
            if not (self._peek() and self._peek().type == "RPAREN"):
                val = self._parse_value()
                if not isinstance(val, Literal):
                    raise IQESyntaxError("Collection call args must be literals", 0, 0)
                call_args.append(val)
                while self._peek() and self._peek().type == "COMMA":
                    self._advance()
                    val = self._parse_value()
                    if not isinstance(val, Literal):
                        raise IQESyntaxError("Collection call args must be literals", 0, 0)
                    call_args.append(val)
            self._expect_type("RPAREN")
            collection: AttrRef | CollectionCall = CollectionCall(name=collection_ref, args=call_args)
        else:
            collection = collection_ref
        where_clauses: list[WhereNode] = []
        while (
            self._peek()
            and self._peek().type == "KEYWORD"
            and self._peek().val == "where"
        ):
            self._advance()  # consume 'where'
            pred = self._parse_predicate()
            where_clauses.append(WhereNode(predicate=pred))
        self._expect_kw("select")
        select_node = self._parse_select()
        if self._peek() is not None:
            tok = self._peek()
            raise IQESyntaxError(f"Unexpected token: {tok.val!r}", tok.line, tok.col)
        return ForeachNode(
            var=var_tok.val,
            collection=collection,
            where_clauses=where_clauses,
            select=select_node,
        )

    def _parse_predicate(self) -> Any:
        return self._parse_or()

    def _parse_or(self) -> Any:
        left = self._parse_and()
        while self._match_kw("or"):
            right = self._parse_and()
            left = BinOp(op="or", left=left, right=right)
        return left

    def _parse_and(self) -> Any:
        left = self._parse_not()
        while self._match_kw("and"):
            right = self._parse_not()
            left = BinOp(op="and", left=left, right=right)
        return left

    def _parse_not(self) -> Any:
        if self._match_kw("not"):
            operand = self._parse_not()
            return BinOp(op="not", left=None, right=operand)
        return self._parse_comparison()

    def _parse_comparison(self) -> Any:
        left = self._parse_attr_ref()
        tok = self._peek()
        if tok is None:
            raise IQESyntaxError("Expected comparison operator", 0, 0)
        if tok.type == "OP":
            op_tok = self._advance()
            right = self._parse_value()
            return BinOp(op=op_tok.val, left=left, right=right)
        if tok.type == "KEYWORD" and tok.val in ("contains", "startswith"):
            op_tok = self._advance()
            right = self._parse_value()
            return BinOp(op=op_tok.val, left=left, right=right)
        raise IQESyntaxError(
            f"Expected comparison operator, got {tok.val!r}", tok.line, tok.col
        )

    def _parse_attr_ref(self) -> AttrRef:
        tok = self._expect_type("IDENT")
        parts = [tok.val]
        while self._peek() and self._peek().type == "DOT":
            self._advance()  # consume '.'
            next_tok = self._peek()
            if next_tok is None or next_tok.type not in ("IDENT", "KEYWORD"):
                line, col = (next_tok.line, next_tok.col) if next_tok else (0, 0)
                raise IQESyntaxError("Expected identifier after '.'", line, col)
            self._advance()  # consume the identifier
            parts.append(next_tok.val)
        return AttrRef(parts=parts)

    def _parse_value(self) -> Union[Literal, AttrRef]:
        tok = self._peek()
        if tok is None:
            raise IQESyntaxError("Expected value", 0, 0)
        if tok.type == "STRING":
            self._advance()
            raw = tok.val[1:-1]  # strip surrounding quotes
            return Literal(value=raw)
        if tok.type == "NUMBER":
            self._advance()
            try:
                return Literal(value=int(tok.val))
            except ValueError:
                return Literal(value=float(tok.val))
        if tok.type == "KEYWORD" and tok.val == "true":
            self._advance()
            return Literal(value=True)
        if tok.type == "KEYWORD" and tok.val == "false":
            self._advance()
            return Literal(value=False)
        if tok.type == "KEYWORD" and tok.val == "null":
            self._advance()
            return Literal(value=None)
        if tok.type == "IDENT":
            return self._parse_attr_ref()
        raise IQESyntaxError(f"Expected value, got {tok.val!r}", tok.line, tok.col)

    def _parse_select(self) -> SelectNode:
        tok = self._peek()
        if tok and tok.type == "STAR":
            self._advance()
            return SelectNode(fields=[], wildcard=True)
        fields: list[AttrRef] = [self._parse_attr_ref()]
        while self._peek() and self._peek().type == "COMMA":
            self._advance()  # consume ','
            fields.append(self._parse_attr_ref())
        return SelectNode(fields=fields, wildcard=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse(query_str: str) -> ForeachNode:
    """Parse an IQE query string and return the root ForeachNode AST."""
    tokens = _tokenize(query_str.strip())
    return _Parser(tokens).parse_query()
