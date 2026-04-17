"""IQE — interpreter: evaluates a parsed Query against an adapter.

Execution model:
    1. Fetch all rows from the named collection via the adapter.
    2. Filter rows against each WHERE predicate (AND-combined across clauses).
    3. Project the selected fields from each surviving row.
"""
from __future__ import annotations

import json
from typing import Any

from tools.iqe.parser import (
    BinaryOp,
    Call,
    LogicalAnd,
    LogicalNot,
    LogicalOr,
    Path,
    Query,
    Value,
)
from tools.iqe.adapters import IQEAdapter


# ── Field resolution ──────────────────────────────────────────────────────────

def _resolve_path(parts: list[str], row: dict) -> Any:
    """Walk a dotted path into a row dict, auto-parsing JSON blob fields."""
    cur: Any = row
    for i, part in enumerate(parts):
        if not isinstance(cur, dict):
            return None
        if part in cur:
            val = cur[part]
            # Auto-parse JSON blobs so sub-paths work transparently
            if isinstance(val, str) and val.startswith(("{", "[")):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    pass
            cur = val
        else:
            # Try <part>_json for convenience: device.config → config_json
            json_key = f"{part}_json"
            if json_key in cur:
                blob = cur[json_key]
                try:
                    cur = json.loads(blob) if isinstance(blob, str) else blob
                except (json.JSONDecodeError, ValueError):
                    cur = blob
            else:
                return None
    return cur


# ── Predicate evaluation ──────────────────────────────────────────────────────

def _eval_value(node: Any, row: dict, var: str = "") -> Any:
    if isinstance(node, Value):
        return node.val
    if isinstance(node, Path):
        parts = node.parts
        # Strip leading loop-variable name so `device.label` → look up `label`
        if var and parts and parts[0] == var:
            parts = parts[1:]
        return _resolve_path(parts, row)
    return None


def _compare(op: str, left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    try:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">":
            return float(left) > float(right)
        if op == "<":
            return float(left) < float(right)
        if op == ">=":
            return float(left) >= float(right)
        if op == "<=":
            return float(left) <= float(right)
        if op == "contains":
            return str(right).lower() in str(left).lower()
        if op == "startswith":
            return str(left).lower().startswith(str(right).lower())
    except (TypeError, ValueError):
        return False
    return False


def _eval_predicate(node: Any, row: dict, var: str = "") -> bool:
    if isinstance(node, BinaryOp):
        left = _eval_value(node.left, row, var)
        right = _eval_value(node.right, row, var)
        return _compare(node.op, left, right)
    if isinstance(node, LogicalAnd):
        return all(_eval_predicate(e, row, var) for e in node.exprs)
    if isinstance(node, LogicalOr):
        return any(_eval_predicate(e, row, var) for e in node.exprs)
    if isinstance(node, LogicalNot):
        return not _eval_predicate(node.expr, row, var)
    return True


# ── Projection ────────────────────────────────────────────────────────────────

def _project(row: dict, projections: list, var: str) -> dict:
    result: dict = {}
    for proj in projections:
        if isinstance(proj, Path):
            parts = proj.parts
            # Strip the leading var name if present (device.label → label)
            if parts and parts[0] == var:
                parts = parts[1:]
            key = ".".join(parts) if parts else str(proj)
            result[key] = _resolve_path(parts, row) if parts else _resolve_path(proj.parts, row)
        elif proj == "*":
            result.update(row)
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def execute(query: Query, adapter: IQEAdapter, topology_id: str | None = None) -> list[dict]:
    """Execute a parsed Query against an adapter.

    Args:
        query: Parsed Query AST from tools.iqe.parser.parse().
        adapter: An IQEAdapter implementation (e.g. NDCAdapter).
        topology_id: Optional topology scope for NDC queries.

    Returns:
        List of projected row dicts matching all predicates.
    """
    collection_name = _resolve_collection_name(query.collection)
    rows = adapter.get_collection(collection_name, topology_id=topology_id)

    filtered = [
        row for row in rows
        if all(_eval_predicate(pred, row, query.var) for pred in query.predicates)
    ]

    return [_project(row, query.projections, query.var) for row in filtered]


def _resolve_collection_name(collection: Any) -> str:
    if isinstance(collection, Path):
        return ".".join(collection.parts)
    if isinstance(collection, Call):
        return collection.func
    return str(collection)
