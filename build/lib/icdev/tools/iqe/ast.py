"""IQE AST — typed node definitions for the intent query tree."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IQENode:
    """Base node for all IQE AST elements."""

    kind: str
    children: list[IQENode] = field(default_factory=list)


@dataclass
class QueryNode(IQENode):
    """Root node representing a full IQE query."""

    raw: str = ""

    def __post_init__(self) -> None:
        self.kind = "query"


@dataclass
class TermNode(IQENode):
    """Leaf node representing a single intent term."""

    value: str = ""

    def __post_init__(self) -> None:
        self.kind = "term"
