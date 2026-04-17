"""IQE parser — converts intent query strings into an AST using Lark."""

from __future__ import annotations

import lark

# Minimal grammar stub — expanded in a later phase
_GRAMMAR = r"""
    start: statement+
    statement: WORD+
    %import common.WORD
    %import common.WS
    %ignore WS
"""

_parser = lark.Lark(_GRAMMAR, parser="earley")


class IQEParser:
    """Parse an IQE intent string and return a Lark Tree."""

    def parse(self, query: str) -> lark.Tree:
        return _parser.parse(query)
