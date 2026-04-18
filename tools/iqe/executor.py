"""IQE executor — dispatches ForeachNode AST to registered collection adapters."""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from tools.iqe.ast_nodes import AttrRef, BinOp, CollectionCall, ForeachNode, Literal, SelectNode, WhereNode

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Executor:
    """Execute IQE AST against registered collection adapters or a SQLite conn."""

    def __init__(self) -> None:
        self._registry: dict[str, Callable[..., list[dict]]] = {}

    def register_collection(self, name: str, adapter_fn: Callable[..., list[dict]]) -> None:
        """Bind *adapter_fn(conn) -> list[dict]* to collection *name*."""
        self._registry[name] = adapter_fn

    def run(self, ast: ForeachNode, conn: Any) -> list[dict]:
        """Execute *ast*, returning matching rows projected per SELECT."""
        coll = ast.collection
        if isinstance(coll, CollectionCall):
            name = str(coll.name)
            call_args = [a.value for a in coll.args]
        else:
            name = str(coll)
            call_args = []
        rows = self._fetch(name, conn, call_args)
        rows = self._filter(rows, ast.var, ast.where_clauses)
        return self._project(rows, ast.var, ast.select)

    def _fetch(self, name: str, conn: Any, call_args: list | None = None) -> list[dict]:
        if call_args is None:
            call_args = []
        if name in self._registry:
            fn = self._registry[name]
            if call_args:
                return list(fn(conn, *call_args))
            return list(fn(conn))
        # SQLite fallback: validate table name is a safe identifier before interpolating.
        table = name.split(".")[-1]
        if not _SAFE_IDENT.match(table):
            raise ValueError(f"Unsafe collection table name: {table!r}")
        cursor = conn.execute(f"SELECT * FROM {table}")  # noqa: S608  # nosec B608
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def _filter(self, rows: list[dict], var: str, clauses: list[WhereNode]) -> list[dict]:
        if not clauses:
            return rows
        return [r for r in rows if all(self._eval(r, var, c.predicate) for c in clauses)]

    def _project(self, rows: list[dict], var: str, sel: Optional[SelectNode]) -> list[dict]:
        if sel is None or sel.wildcard:
            return rows
        out: list[dict] = []
        for row in rows:
            projected: dict[str, Any] = {}
            for f in sel.fields:
                key_parts = self._strip_var(f.parts, var)
                if len(key_parts) > 1:
                    key = ".".join(key_parts)
                elif key_parts:
                    key = key_parts[0]
                else:
                    key = str(f)
                projected[key] = self._resolve(row, key_parts)
            out.append(projected)
        return out

    def _resolve(self, obj: Any, parts: list[str]) -> Any:
        for p in parts:
            if isinstance(obj, dict):
                obj = obj.get(p)
            else:
                obj = getattr(obj, p, None)
        return obj

    def _eval(self, row: dict, var: str, pred: Any) -> bool:
        if isinstance(pred, BinOp):
            op = pred.op
            if op == "and":
                return self._eval(row, var, pred.left) and self._eval(row, var, pred.right)
            if op == "or":
                return self._eval(row, var, pred.left) or self._eval(row, var, pred.right)
            if op == "not":
                return not self._eval(row, var, pred.right)
            return self._compare(op, self._val(row, var, pred.left), self._val(row, var, pred.right))
        if isinstance(pred, AttrRef):
            return bool(self._resolve(row, self._strip_var(pred.parts, var)))
        if isinstance(pred, Literal):
            return bool(pred.value)
        return True

    def _compare(self, op: str, lv: Any, rv: Any) -> bool:
        if op == "==":
            return lv == rv
        if op == "!=":
            return lv != rv
        if op == ">":
            return lv > rv
        if op == "<":
            return lv < rv
        if op == ">=":
            return lv >= rv
        if op == "<=":
            return lv <= rv
        if op == "contains":
            return rv in lv if lv is not None else False
        if op == "startswith":
            return str(lv).startswith(str(rv)) if lv is not None else False
        return False

    def _val(self, row: dict, var: str, node: Any) -> Any:
        if isinstance(node, AttrRef):
            return self._resolve(row, self._strip_var(node.parts, var))
        if isinstance(node, Literal):
            return node.value
        return node

    @staticmethod
    def _strip_var(parts: list[str], var: str) -> list[str]:
        return parts[1:] if parts and parts[0] == var else parts


_default = Executor()


def register_collection(name: str, adapter_fn: Callable[..., list[dict]]) -> None:
    """Register *adapter_fn* on the module-level default Executor."""
    _default.register_collection(name, adapter_fn)


def execute_query(ast: ForeachNode, conn: Any) -> list[dict]:
    """Execute *ast* against *conn* using the module-level default Executor."""
    return _default.run(ast, conn)
