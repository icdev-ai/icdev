"""IQE AST node dataclasses — ForeachNode, WhereNode, SelectNode, AttrRef, BinOp, Literal."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union


@dataclass
class AttrRef:
    """Dotted attribute reference: ``device.vendor``, ``c.monthly_cost_usd``."""
    parts: list[str]

    def __str__(self) -> str:
        return ".".join(self.parts)


@dataclass
class CollectionCall:
    """Parameterised collection call: ``attack.paths("src_id", "goal_id")``."""
    name: AttrRef
    args: list["Literal"]

    def __str__(self) -> str:
        return str(self.name)


@dataclass
class Literal:
    """Scalar literal value: str, int, float, bool, or None."""
    value: Union[str, int, float, bool, None]


@dataclass
class BinOp:
    """Binary comparison or logical operation (and/or/not)."""
    op: str  # ==, !=, >, <, >=, <=, contains, startswith, and, or, not
    left: Any  # None for unary 'not'
    right: Any


@dataclass
class SelectNode:
    """SELECT projection list."""
    fields: list[AttrRef]
    wildcard: bool = False


@dataclass
class WhereNode:
    """Single WHERE predicate (one 'where' keyword + expression)."""
    predicate: Any  # BinOp | AttrRef | Literal


@dataclass
class ForeachNode:
    """Top-level foreach query node."""
    var: str
    collection: Union[AttrRef, CollectionCall]
    where_clauses: list[WhereNode] = field(default_factory=list)
    select: Optional[SelectNode] = None
