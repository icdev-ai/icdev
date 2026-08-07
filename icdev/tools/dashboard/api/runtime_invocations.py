#!/usr/bin/env python3
# CUI // SP-CTI
"""Runtime Performance API — the `runtime_invocations` rollup, over HTTP.

``runtime_invocations`` (migration 341) is the only table that records MCP tool
calls, agent runs, persona sessions and role steps in one shape. Until now it
had exactly two readers, both of them terminals: ``icdev runtime top`` and
``icdev audit tail --source runtime_invocations``. Answering "which tool is slow"
or "what is erroring" from the dashboard meant opening a SQL client.

This blueprint is the third reader, and it is deliberately the *thinnest* one:

  * The aggregation is ``invocation_recorder.summary()`` — no SQL is authored
    here, so the panel and the CLI cannot disagree about what a "call" or an
    "error" is.
  * The per-row shaping is ``tools.cli.runtime.normalise()`` — the same reason.
    PostgreSQL returns ``Decimal`` for ``avg()`` where SQLite returns float, and
    both return NULL while every row in a group is still ``running``; one shared
    coercion means JSON and the terminal round identically.

Endpoints (mounted at /api/v1/runtime-invocations and /api/runtime-invocations):

    GET /summary?surface=mcp&limit=100   → rollup + per-surface breakdown

## Two things this refuses to fake

**An absent table is not an idle system.** ``summary()`` swallows its own
exceptions and returns ``[]`` on failure, so "nothing ran" and "the migration
never ran" are the same value. The response carries ``available`` (probed with
``table_exists``) and ``backend`` so the panel can say which one it is — a git
worktree has no ``.env`` and silently falls back to an empty SQLite file, which
is exactly how an empty panel gets mistaken for a quiet runtime.

**A capped list is not a total.** ``limit`` bounds the number of NAMES, so the
``totals`` and ``by_surface`` figures describe the rows returned, not the whole
table. ``truncated`` says when the cap was hit.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from tools.logging.icdev_logger import get_logger
from tools.observability import invocation_recorder

logger = get_logger(__name__)

runtime_invocations_api = Blueprint("runtime_invocations_api", __name__)

#: Rows are one per (surface, name). 512 MCP tools is the realistic upper bound
#: for a single surface, so 1000 covers "show me everything" without letting a
#: query string ask for an unbounded scan.
_MAX_LIMIT = 1000
_DEFAULT_LIMIT = 200


def _get_db():
    """A connection with NO row-security context attached.

    ``runtime_invocations`` has a ``classification`` column but no ``tenant_id``
    one. Inside a Flask request ``get_connection()`` auto-attaches the caller's
    security context, whose injector unconditionally appends ``tenant_id = %s``
    — producing UndefinedColumn (PG) / "no such column" (SQLite). That error is
    swallowed by ``summary()``, so the panel would render an empty table instead
    of failing visibly. Same deliberate bypass as ``tools/dashboard/api/traces.py``
    and ``tools/observability/health_blueprint.py``.

    Nothing is lost by it: this is platform-wide operational telemetry that
    stores argument KEY NAMES only, never values, so there is no per-tenant row
    to leak.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.set_security_context(None)  # rls-bypass: runtime_invocations has no tenant_id column; injection makes the rollup fail and summary() renders the failure as an empty table
    except AttributeError:
        pass
    return conn


def _describe_backend() -> str:
    """Which database we actually read. Same helper text as `icdev audit tail`."""
    try:
        from tools.cli.audit_tail import _describe_backend as _d

        return _d()
    except Exception:  # noqa: BLE001
        return "unknown"


def _decorate(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add the one derived field the table sorts on but SQL should not compute.

    ``error_rate`` is errors/calls. It lives here rather than in the SQL because
    the rollup is shared with the CLI, which shows counts and not a rate.
    """
    for row in rows:
        calls = row.get("calls") or 0
        row["error_rate"] = round(row.get("errors", 0) / calls, 4) if calls else 0.0
    return rows


def _by_surface(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-surface roll-up of the RETURNED rows, in declared surface order.

    Every surface in ``SURFACES`` appears even at zero, because a surface that
    recorded nothing is the finding — that is the gap migration 341 was created
    to make visible, and dropping the key would hide it.
    """
    out: Dict[str, Dict[str, Any]] = {
        s: {"names": 0, "calls": 0, "errors": 0}
        for s in invocation_recorder.SURFACES
    }
    for row in rows:
        bucket = out.setdefault(
            row["surface"], {"names": 0, "calls": 0, "errors": 0}
        )
        bucket["names"] += 1
        bucket["calls"] += row["calls"]
        bucket["errors"] += row["errors"]
    return out


def _parse_limit(raw: Optional[str]) -> int:
    try:
        value = int(raw) if raw not in (None, "") else _DEFAULT_LIMIT
    except (TypeError, ValueError):
        value = _DEFAULT_LIMIT
    return max(1, min(_MAX_LIMIT, value))


@runtime_invocations_api.route("/summary", methods=["GET"])
def summary():
    """Per-name rollup of runtime invocations: calls, errors, avg/max ms."""
    surface = (request.args.get("surface") or "").strip() or None
    if surface and surface not in invocation_recorder.SURFACES:
        return jsonify({
            "ok": False,
            "error": f"unknown surface '{surface}'",
            "surfaces": list(invocation_recorder.SURFACES),
        }), 400

    limit = _parse_limit(request.args.get("limit"))

    try:
        from tools.cli.runtime import normalise
        from tools.db.storage import table_exists

        conn = _get_db()
        try:
            available = table_exists(conn, "runtime_invocations")
            rows = _decorate(list(normalise(
                invocation_recorder.summary(surface=surface, limit=limit, conn=conn)
            ))) if available else []
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("runtime invocation summary failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc),
                        "backend": _describe_backend()}), 500

    return jsonify({
        "ok": True,
        # False means the migration has not run here — distinct from an empty
        # table, and the difference is the whole point of probing for it.
        "available": available,
        "backend": _describe_backend(),
        "surface": surface,
        "limit": limit,
        "truncated": len(rows) >= limit,
        "rows": rows,
        "surfaces": list(invocation_recorder.SURFACES),
        "totals": {
            "names": len(rows),
            "calls": sum(r["calls"] for r in rows),
            "errors": sum(r["errors"] for r in rows),
        },
        "by_surface": _by_surface(rows),
    })
