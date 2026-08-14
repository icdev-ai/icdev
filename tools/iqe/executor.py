"""IQE executor — dispatches ForeachNode AST to registered collection adapters.

**Completeness is part of the answer** (ctx-trust-04). Adapters cap their scan
and ``run`` applies the query's ``where`` clauses in PYTHON afterwards, so a
question whose window matches more rows than the cap is answered from the
newest ``cap`` rows only. That is not a crash — it is a *wrong number*, and
``analyst._label_rows_result`` used to stamp it ``confidence_score: 1.0``.

Predicate pushdown (filtering in SQL) is the other possible fix; it is not what
this module does, because every adapter signature would have to change. Instead
an adapter may report that it capped, by returning a :class:`RowSet` built with
:func:`capped_rows`. The reason travels through ``union``/``join`` and out of
:meth:`Executor.run_with_meta` as an :class:`ExecutionResult`, so the caller can
label the answer honestly. An adapter that returns a plain ``list`` is taken at
its word — complete — which keeps every existing adapter working unchanged.
"""
from __future__ import annotations

import logging as _log
import re
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from concurrent.futures import as_completed as _as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from tools.iqe.ast_nodes import AttrRef, BinOp, CollectionCall, ForeachNode, Literal, SelectNode, WhereNode

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_logger = _log.getLogger("iqe.executor")

#: Rows read before the unregistered-collection fallback stops. That path used
#: to issue ``SELECT * FROM {table}`` with no LIMIT at all — an unbounded scan
#: into Python memory whose result was then also filtered in Python.
FALLBACK_ROW_CAP = 10000


class RowSet(list):
    """A row list that knows whether its source may have withheld rows.

    ``incomplete`` holds one dict per reason, e.g.
    ``{"collection": "cortex.audit", "reason": "row_cap", "limit": 10000}``.
    An empty list means the rows are the complete set for that collection.
    """

    def __init__(self, rows: Iterable[dict] = (), incomplete: Iterable[dict] = ()) -> None:
        super().__init__(rows)
        self.incomplete: list[dict] = [dict(x) for x in incomplete]

    @property
    def complete(self) -> bool:
        return not self.incomplete


def capped_rows(rows: Iterable[dict], collection: str, limit: int) -> RowSet:
    """Build a :class:`RowSet` marked as cut short by a row cap.

    Adapters call this instead of returning a bare list when their ``LIMIT``
    actually bit, so the executor's caller can refuse to label the answer
    maximally confident.
    """
    return RowSet(rows, [{"collection": collection, "reason": "row_cap", "limit": int(limit)}])


def incompleteness_of(rows: Any) -> list[dict]:
    """Reasons *rows* may be missing rows; ``[]`` for a plain list."""
    return [dict(x) for x in (getattr(rows, "incomplete", ()) or ())]


@dataclass
class ExecutionResult:
    """Rows plus whether they are the complete answer to the query."""

    rows: list[dict]
    incomplete: list[dict] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.incomplete


class Executor:
    """Execute IQE AST against registered collection adapters or a SQLite conn."""

    def __init__(self) -> None:
        self._registry: dict[str, Callable[..., list[dict]]] = {}

    def register_collection(self, name: str, adapter_fn: Callable[..., list[dict]]) -> None:
        """Bind *adapter_fn(conn) -> list[dict]* to collection *name*."""
        self._registry[name] = adapter_fn

    def run(self, ast: ForeachNode, conn: Any) -> list[dict]:
        """Execute *ast*, returning matching rows projected per SELECT.

        Drops the completeness metadata. Callers that present the result to a
        human — or compute a count from it — should use
        :meth:`run_with_meta` instead, so a capped scan cannot be reported as
        a certainty.
        """
        return self.run_with_meta(ast, conn).rows

    def run_with_meta(self, ast: ForeachNode, conn: Any) -> ExecutionResult:
        """Execute *ast*, returning rows **and** why they may be incomplete.

        The ``where`` clauses are applied here, in Python, AFTER the adapter's
        row cap — so any cap the fetch hit makes every aggregate over these
        rows a lower bound rather than an answer. That is exactly what
        ``ExecutionResult.incomplete`` carries.
        """
        coll = ast.collection
        if isinstance(coll, CollectionCall):
            name = str(coll.name)
            call_args = [a.value for a in coll.args]
        else:
            name = str(coll)
            call_args = []
        fetched = self._fetch(name, conn, call_args)
        incomplete = incompleteness_of(fetched)
        rows = self._filter(fetched, ast.var, ast.where_clauses)
        return ExecutionResult(rows=self._project(rows, ast.var, ast.select), incomplete=incomplete)

    def _fetch(self, name: str, conn: Any, call_args: list | None = None) -> list[dict]:
        if call_args is None:
            call_args = []

        # Built-in: union("col1", "col2", ...) — parallel fetch + concatenate
        if name == "union":
            return self._fetch_union([str(a) for a in call_args], conn)

        # Built-in: join("col1", "col2", "key_field") — parallel fetch + inner join
        if name == "join":
            if len(call_args) < 3:  # noqa: PLR2004
                raise ValueError("join() requires 3 args: join(\"col1\", \"col2\", \"key_field\")")
            return self._fetch_join(str(call_args[0]), str(call_args[1]), str(call_args[2]), conn)

        if name in self._registry:
            fn = self._registry[name]
            out = fn(conn, *call_args) if call_args else fn(conn)
            # A RowSet carries the adapter's completeness report; list() would
            # silently discard it and the answer would read as certain again.
            return out if isinstance(out, RowSet) else list(out)
        # SQLite fallback: validate table name is a safe identifier before interpolating.
        table = name.split(".")[-1]
        if not _SAFE_IDENT.match(table):
            raise ValueError(f"Unsafe collection table name: {table!r}")
        # Bounded: this used to be an unbounded scan into Python memory, and
        # the query's where clauses are applied after it either way, so an
        # unregistered collection could quietly answer from a partial table.
        cap = FALLBACK_ROW_CAP
        cursor = conn.execute(f"SELECT * FROM {table} LIMIT {cap + 1}")  # noqa: S608  # nosec B608
        cols = [d[0] for d in cursor.description]
        fetched = [dict(zip(cols, row)) for row in cursor.fetchall()]
        if len(fetched) > cap:
            _logger.warning(
                "collection %s is not registered; the fallback table scan hit its "
                "%d-row cap, so where clauses are applied to a partial table",
                name, cap,
            )
            return capped_rows(fetched[:cap], name, cap)
        return fetched

    def _fetch_union(self, collections: list[str], conn: Any) -> list[dict]:
        """Fetch multiple collections in parallel and concatenate their rows.

        Order is preserved: rows from collections[0] appear before collections[1],
        even though fetches run concurrently.  A failed sub-fetch logs a warning
        and contributes an empty slice rather than aborting the whole union —
        and records that slice as ``reason="fetch_failed"``, because a union
        that lost one of its collections is a short answer, not a complete one.
        """
        if not collections:
            return []
        n_workers = min(len(collections), 8)
        results: dict[int, list[dict]] = {}
        failed: dict[int, str] = {}

        def _fetch_one(idx: int, cname: str) -> tuple[int, list[dict]]:
            return idx, self._fetch(cname, conn, [])

        with _ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="iqe-union") as pool:
            futures = {pool.submit(_fetch_one, i, c): i for i, c in enumerate(collections)}
            for fut in _as_completed(futures):
                idx = futures[fut]
                try:
                    _, results[idx] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("union: sub-fetch failed: %s", exc)
                    failed[idx] = str(exc)

        merged = RowSet()
        for i in range(len(collections)):
            part = results.get(i, [])
            merged.extend(part)
            merged.incomplete.extend(incompleteness_of(part))
            if i in failed:
                merged.incomplete.append(
                    {"collection": collections[i], "reason": "fetch_failed", "error": failed[i]}
                )
        return merged

    def _fetch_join(self, col1: str, col2: str, key: str, conn: Any) -> list[dict]:
        """Fetch two collections in parallel and inner-join on *key*.

        Tries DuckDB first (type coercion, handles mixed numeric/string keys).
        Falls back to a pure-Python dict-lookup join when DuckDB is not installed
        or raises for any reason.

        A capped side makes the JOIN itself incomplete — rows drop out because
        their partner was never fetched — so either side's report is carried
        onto the merged result.
        """
        with _ThreadPoolExecutor(max_workers=2, thread_name_prefix="iqe-join") as pool:
            f1 = pool.submit(self._fetch, col1, conn, [])
            f2 = pool.submit(self._fetch, col2, conn, [])
            left = f1.result()
            right = f2.result()

        incomplete = incompleteness_of(left) + incompleteness_of(right)
        try:
            merged = _duckdb_join(left, right, key)
        except Exception:  # noqa: BLE001
            merged = _python_join(left, right, key)
        return RowSet(merged, incomplete) if incomplete else merged

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
        if op in (">", "<", ">=", "<="):
            # SQL NULL semantics: a comparison against a missing/NULL operand
            # is neither true nor false, so it never matches a WHERE clause.
            if lv is None or rv is None:
                return False
            if op == ">":
                return lv > rv
            if op == "<":
                return lv < rv
            if op == ">=":
                return lv >= rv
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


# ---------------------------------------------------------------------------
# Module-level join helpers (used by Executor._fetch_join)
# ---------------------------------------------------------------------------

def _python_join(left: list[dict], right: list[dict], key: str) -> list[dict]:
    """Inner join two row lists on a shared key field — no external dependencies.

    When both sides have the same field name (other than the join key), the
    left row's value takes precedence in the merged dict.
    """
    right_idx: dict[Any, list[dict]] = {}
    for row in right:
        k = row.get(key)
        if k is not None:
            right_idx.setdefault(k, []).append(row)

    merged: list[dict] = []
    for lrow in left:
        k = lrow.get(key)
        if k in right_idx:
            for rrow in right_idx[k]:
                merged.append({**rrow, **lrow})  # left wins on field conflict
    return merged


def _duckdb_join(left: list[dict], right: list[dict], key: str) -> list[dict]:
    """Inner join using DuckDB for type coercion.

    Writes both sides to temp JSON files, executes a JOIN inside DuckDB, then
    cleans up.  Raises ``ImportError`` when duckdb is not installed (caller
    falls back to ``_python_join``).  Any other exception also propagates to
    the caller's except clause.
    """
    import json           # noqa: PLC0415
    import tempfile       # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    import duckdb         # noqa: PLC0415  — raises ImportError if not installed

    tmp = Path(tempfile.gettempdir())
    lp = tmp / "_iqe_join_left.json"
    rp = tmp / "_iqe_join_right.json"
    lp.write_text(json.dumps(left), encoding="utf-8", newline="")
    rp.write_text(json.dumps(right), encoding="utf-8", newline="")
    try:
        con = duckdb.connect()
        esc = key.replace('"', '""')
        result = con.execute(
            f"SELECT * FROM read_json_auto('{lp.as_posix()}') l "
            f"JOIN read_json_auto('{rp.as_posix()}') r "
            f'ON l."{esc}" = r."{esc}"'
        )
        cols = [d[0] for d in result.description]
        raw_rows = [dict(zip(cols, row)) for row in result.fetchall()]
        # Post-process: overlay original left-row values so left wins on field
        # conflict (consistent with _python_join semantics).
        left_map: dict[Any, dict] = {}
        for lrow in left:
            k_val = lrow.get(key)
            if k_val is not None and k_val not in left_map:
                left_map[k_val] = lrow
        return [{**row, **left_map.get(row.get(key), {})} for row in raw_rows]
    finally:
        lp.unlink(missing_ok=True)
        rp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Module-level singleton and public API
# ---------------------------------------------------------------------------

_default = Executor()


def register_collection(name: str, adapter_fn: Callable[..., list[dict]]) -> None:
    """Register *adapter_fn* on the module-level default Executor."""
    _default.register_collection(name, adapter_fn)


def list_collections() -> list[str]:
    """Return the names of all collections registered on the default Executor."""
    return sorted(_default._registry)


def execute_query(ast: ForeachNode, conn: Any) -> list[dict]:
    """Execute *ast* against *conn* using the module-level default Executor.

    Returns rows only. Use :func:`execute_query_with_meta` when the answer is
    shown to a human or reduced to a count — a row cap that bit is invisible
    here.
    """
    return _default.run(ast, conn)


def execute_query_with_meta(ast: ForeachNode, conn: Any) -> ExecutionResult:
    """Execute *ast*, returning rows plus why they may be incomplete."""
    return _default.run_with_meta(ast, conn)
