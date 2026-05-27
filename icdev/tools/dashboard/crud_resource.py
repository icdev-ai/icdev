# CUI // SP-CTI
"""OPT-69: tools/dashboard/crud_resource.py — declarative CRUD helper.

OPT-69 declarative resource pattern adapted from marmelab/react-admin
(MIT). See https://github.com/marmelab/react-admin

Wires a full REST CRUD surface for a DB table in one call. Existing
dashboard routes are untouched — this helper is for new admin pages
that would otherwise be ~100 lines of boilerplate each.

Usage:
    from flask import Flask
    from tools.dashboard.crud_resource import (
        ColumnSpec, register_resource,
    )

    app = Flask(__name__)
    register_resource(
        app,
        name="audit_trail",
        url_prefix="/api/audit",
        columns=[
            ColumnSpec("id", readable=True, writable=False),
            ColumnSpec("created_at", readable=True, writable=False),
            ColumnSpec("actor", readable=True, writable=True),
            ColumnSpec("action", readable=True, writable=True),
        ],
        sortable=["created_at", "actor"],
        filterable=["actor", "action"],
        allow_create=False,
        allow_edit=False,
        allow_delete=False,  # audit_trail is append-only
    )

Non-goals:
    * No arbitrary-schema introspection — caller declares columns.
    * No JOINs (single-table only).
    * No GraphQL.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request


logger = get_logger(__name__)

# Single safe character class for SQL identifiers. Anything else is
# rejected — stops caller typos turning into SQL injection.
_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _safe_ident(name: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


@dataclass
class ColumnSpec:
    name: str
    readable: bool = True
    writable: bool = True
    pk: bool = False
    default: Any = None

    def __post_init__(self):
        _safe_ident(self.name)


@dataclass
class ResourceConfig:
    name: str
    url_prefix: str
    columns: List[ColumnSpec]
    sortable: List[str] = field(default_factory=list)
    filterable: List[str] = field(default_factory=list)
    allow_create: bool = True
    allow_edit: bool = True
    allow_delete: bool = True
    allow_read: bool = True
    pk_column: str = "id"
    default_sort: str = ""
    default_page_size: int = 50
    max_page_size: int = 500
    audit_event_type: str = "config_changed"
    get_connection: Optional[Callable[[], Any]] = None
    auth_decorator: Optional[Callable] = None

    def readable_columns(self) -> List[str]:
        return [c.name for c in self.columns if c.readable]

    def writable_columns(self) -> List[str]:
        return [c.name for c in self.columns if c.writable]

    def column_map(self) -> Dict[str, ColumnSpec]:
        return {c.name: c for c in self.columns}


# ────────────────────────────────────────────────────────────────────────────
# Query builders
# ────────────────────────────────────────────────────────────────────────────


def _build_list_query(cfg: ResourceConfig, args) -> Dict[str, Any]:
    """Convert a request.args MultiDict into a SELECT query plan."""
    table = _safe_ident(cfg.name)
    cols = [_safe_ident(c) for c in cfg.readable_columns()]
    select_cols = ", ".join(cols) if cols else "*"

    # Filters: only columns marked filterable get through
    where_clauses: List[str] = []
    params: List[Any] = []
    filter_set = {_safe_ident(f) for f in cfg.filterable}
    for key, value in (args.items() if hasattr(args, "items") else args):
        if key in filter_set and value:
            where_clauses.append(f"{key} = %s")
            params.append(value)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Sort
    sort_key = args.get("_sort", cfg.default_sort) if hasattr(args, "get") else None
    sort_dir = args.get("_order", "ASC") if hasattr(args, "get") else "ASC"
    sortable_set = {_safe_ident(s) for s in cfg.sortable}
    order_sql = ""
    if sort_key and sort_key in sortable_set:
        direction = "DESC" if (sort_dir or "").upper() == "DESC" else "ASC"
        order_sql = f" ORDER BY {sort_key} {direction}"

    # Pagination
    try:
        page = max(1, int(args.get("_page", 1))) if hasattr(args, "get") else 1
    except (ValueError, TypeError):
        page = 1
    try:
        size = int(args.get("_page_size", cfg.default_page_size)) if hasattr(args, "get") else cfg.default_page_size
    except (ValueError, TypeError):
        size = cfg.default_page_size
    size = max(1, min(size, cfg.max_page_size))
    offset = (page - 1) * size

    sql = (
        f"SELECT {select_cols} FROM {table}{where_sql}{order_sql} "
        f"LIMIT {int(size)} OFFSET {int(offset)}"
    )
    return {
        "sql": sql,
        "params": tuple(params),
        "page": page,
        "page_size": size,
        "where_sql": where_sql,
        "where_params": tuple(params),
        "table": table,
    }


def _build_count_query(cfg: ResourceConfig, where_sql: str) -> str:
    table = _safe_ident(cfg.name)
    return f"SELECT COUNT(*) AS c FROM {table}{where_sql}"


def _build_insert(cfg: ResourceConfig, payload: Dict[str, Any]) -> Dict[str, Any]:
    table = _safe_ident(cfg.name)
    writable = {_safe_ident(w) for w in cfg.writable_columns()}
    cols: List[str] = []
    params: List[Any] = []
    for key, value in payload.items():
        if key in writable:
            cols.append(key)
            params.append(value)
    if not cols:
        raise ValueError("no writable columns in payload")
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    )
    return {"sql": sql, "params": tuple(params)}


def _build_update(
    cfg: ResourceConfig, pk_value: Any, payload: Dict[str, Any]
) -> Dict[str, Any]:
    table = _safe_ident(cfg.name)
    pk = _safe_ident(cfg.pk_column)
    writable = {_safe_ident(w) for w in cfg.writable_columns()}
    assignments: List[str] = []
    params: List[Any] = []
    for key, value in payload.items():
        if key in writable and key != pk:
            assignments.append(f"{key} = %s")
            params.append(value)
    if not assignments:
        raise ValueError("no writable columns in payload")
    params.append(pk_value)
    sql = (
        f"UPDATE {table} SET {', '.join(assignments)} WHERE {pk} = %s"
    )
    return {"sql": sql, "params": tuple(params)}


def _build_delete(cfg: ResourceConfig, pk_value: Any) -> Dict[str, Any]:
    table = _safe_ident(cfg.name)
    pk = _safe_ident(cfg.pk_column)
    sql = f"DELETE FROM {table} WHERE {pk} = %s"
    return {"sql": sql, "params": (pk_value,)}


# ────────────────────────────────────────────────────────────────────────────
# Registration
# ────────────────────────────────────────────────────────────────────────────


def register_resource(
    app,
    *,
    name: str,
    url_prefix: str,
    columns: List[ColumnSpec],
    sortable: Optional[List[str]] = None,
    filterable: Optional[List[str]] = None,
    allow_create: bool = True,
    allow_edit: bool = True,
    allow_delete: bool = True,
    allow_read: bool = True,
    pk_column: str = "id",
    default_sort: str = "",
    default_page_size: int = 50,
    max_page_size: int = 500,
    audit_event_type: str = "config_changed",
    get_connection: Optional[Callable[[], Any]] = None,
    auth_decorator: Optional[Callable] = None,
) -> Blueprint:
    """Wire CRUD routes for `name` at `url_prefix`. Returns the Blueprint.

    Callers must pass `get_connection` — the helper is stateless and
    does not import tools.db.storage directly, so tests can inject a
    fake factory.
    """
    cfg = ResourceConfig(
        name=name,
        url_prefix=url_prefix,
        columns=list(columns),
        sortable=list(sortable or []),
        filterable=list(filterable or []),
        allow_create=allow_create,
        allow_edit=allow_edit,
        allow_delete=allow_delete,
        allow_read=allow_read,
        pk_column=pk_column,
        default_sort=default_sort,
        default_page_size=default_page_size,
        max_page_size=max_page_size,
        audit_event_type=audit_event_type,
        get_connection=get_connection,
        auth_decorator=auth_decorator,
    )
    _safe_ident(cfg.pk_column)
    _safe_ident(cfg.name)

    bp = Blueprint(
        f"crud_resource_{cfg.name}",
        __name__,
        url_prefix=cfg.url_prefix,
    )

    def _wrap(fn):
        if cfg.auth_decorator is not None:
            return cfg.auth_decorator(fn)
        return fn

    def _conn():
        if cfg.get_connection is None:
            raise RuntimeError(
                f"crud_resource {cfg.name!r} has no get_connection"
            )
        return cfg.get_connection()

    def _rows_to_dicts(rows, col_names):
        out = []
        for row in rows:
            if isinstance(row, dict):
                out.append({k: row[k] for k in col_names if k in row})
            else:
                try:
                    out.append({k: row[k] for k in col_names})
                except (KeyError, TypeError):
                    # Tuple-style row: zip against declared order
                    out.append(dict(zip(col_names, row)))
        return out

    if cfg.allow_read:

        @bp.route("", methods=["GET"])
        @_wrap
        def list_resource():
            plan = _build_list_query(cfg, request.args)
            conn = _conn()
            try:
                rows = conn.execute(plan["sql"], plan["params"]).fetchall()
                count_sql = _build_count_query(cfg, plan["where_sql"])
                count_row = conn.execute(
                    count_sql, plan["where_params"]
                ).fetchone()
                total = (
                    count_row[0] if count_row is not None else len(rows)
                )
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            return jsonify({
                "items": _rows_to_dicts(rows, cfg.readable_columns()),
                "page": plan["page"],
                "page_size": plan["page_size"],
                "total": total,
            })

        @bp.route(f"/<{cfg.pk_column}>", methods=["GET"])
        @_wrap
        def get_one(**kwargs):
            pk_value = kwargs.get(cfg.pk_column)
            table = _safe_ident(cfg.name)
            pk = _safe_ident(cfg.pk_column)
            cols = ", ".join(_safe_ident(c) for c in cfg.readable_columns())
            sql = f"SELECT {cols} FROM {table} WHERE {pk} = %s"
            conn = _conn()
            try:
                row = conn.execute(sql, (pk_value,)).fetchone()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            if not row:
                return jsonify({"error": "not found"}), 404
            return jsonify(_rows_to_dicts([row], cfg.readable_columns())[0])

    if cfg.allow_create:

        @bp.route("", methods=["POST"])
        @_wrap
        def create_resource():
            payload = request.get_json(force=True, silent=True) or {}
            try:
                plan = _build_insert(cfg, payload)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            conn = _conn()
            try:
                conn.execute(plan["sql"], plan["params"])
                if hasattr(conn, "commit"):
                    conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            return jsonify({"status": "created"}), 201

    if cfg.allow_edit:

        @bp.route(f"/<{cfg.pk_column}>", methods=["PATCH", "PUT"])
        @_wrap
        def update_resource(**kwargs):
            pk_value = kwargs.get(cfg.pk_column)
            payload = request.get_json(force=True, silent=True) or {}
            try:
                plan = _build_update(cfg, pk_value, payload)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            conn = _conn()
            try:
                conn.execute(plan["sql"], plan["params"])
                if hasattr(conn, "commit"):
                    conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            return jsonify({"status": "updated", cfg.pk_column: pk_value})

    if cfg.allow_delete:

        @bp.route(f"/<{cfg.pk_column}>", methods=["DELETE"])
        @_wrap
        def delete_resource(**kwargs):
            pk_value = kwargs.get(cfg.pk_column)
            plan = _build_delete(cfg, pk_value)
            conn = _conn()
            try:
                conn.execute(plan["sql"], plan["params"])
                if hasattr(conn, "commit"):
                    conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            return jsonify({"status": "deleted", cfg.pk_column: pk_value})

    app.register_blueprint(bp)
    return bp
