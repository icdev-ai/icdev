from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AttrRef:
    parts: list[str]


@dataclass
class Literal:
    value: str | int | float


@dataclass
class BinOp:
    left: Any
    op: str
    right: Any


@dataclass
class WhereNode:
    predicate: Any


@dataclass
class SelectNode:
    fields: list[Any]


@dataclass
class ForeachNode:
    var: str
    collection: Any
    where_clauses: list[WhereNode] = field(default_factory=list)
    select: SelectNode = field(default_factory=lambda: SelectNode(fields=[]))
