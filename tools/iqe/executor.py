"""IQE executor — walks an IQE AST and produces structured results."""

from __future__ import annotations

from typing import Any

import lark

from tools.iqe.ast import IQENode, QueryNode, TermNode


class IQEExecutor:
    """Execute a parsed IQE Lark tree and return a result dict."""

    def execute(self, tree: lark.Tree) -> dict[str, Any]:
        root = self._visit(tree)
        return {"kind": root.kind, "children": [c.value for c in root.children if isinstance(c, TermNode)]}

    def _visit(self, node: lark.Tree | lark.Token) -> IQENode:
        if isinstance(node, lark.Token):
            return TermNode(value=str(node))
        children = [self._visit(child) for child in node.children]
        query = QueryNode(raw=str(node))
        query.children = children
        return query
