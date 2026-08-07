# CUI // SP-CTI
"""Read-only rollups over ``runtime_invocations``.

Migration 341 created the table and ``invocation_recorder.record()`` fills it
from four choke points (MCP tools, agents, personas, roles). Nothing could read
it: ``audit_trail`` got ``icdev audit tail`` and ``tools/audit/store.py``; this
table got neither, so "which MCP tool is slow" was a question you could only
answer with a SQL client.

This is that table's ``AuditStore``. Same shape deliberately:

  * READ ONLY. Nothing here writes. The recorder owns the write path and has to
    stay on the hot path's budget; a reader that could write would blur that.
  * Injectable ``connection_factory``, so a test can point at a fixture DB
    without patching a module global.
  * One plain SELECT per method, dialect-neutral, ordered and limited
    server-side. PostgreSQL is primary and SQLite is the init/test fallback, so
    nothing here may depend on either dialect's extensions — which is why the
    rollup uses ``sum(CASE WHEN ...)`` rather than ``count(*) FILTER``.

## Why ``calls`` and ``completed`` are both reported

``avg(duration_ms)`` skips NULLs, and a row is NULL-duration for as long as it
is ``running``. So the average is over COMPLETED invocations while ``calls``
counts every row, and a rollup that printed only those two would quietly invite
the reader to divide one by the other. ``completed`` is the honest denominator
for the duration columns and is reported next to them.

## An empty rollup is not the same as a broken one

Every method returns ``[]`` on failure — a telemetry reader that raised into a
dashboard render would be worse than a blank panel. But blank-because-empty and
blank-because-the-query-failed look identical on screen, so the failure is kept
on :attr:`InvocationStore.last_error` and both callers render it: the CLI to
stderr with a non-zero exit, the dashboard as a degraded banner.

One concrete way this fires: ``runtime_invocations`` has no ``tenant_id``
column, so if a caller holds a Flask security context the RLS predicate
injection appends ``tenant_id = %s`` and PostgreSQL raises UndefinedColumn.
That is a real failure and must read as one, not as "no MCP tool has ever run".
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Cap on rows returned by :meth:`InvocationStore.by_name`, whatever the caller
#: asks for. 512 MCP tools is the realistic upper bound on distinct names.
MAX_ROWS = 1000

_TABLE = "runtime_invocations"


def _num(value: Any) -> Optional[float]:
    """Coerce a DB aggregate to a plain float, or None.

    ``avg()`` returns ``Decimal`` on PostgreSQL and ``float`` on SQLite, and
    ``Decimal`` is not JSON-serializable — ``--json`` and every jsonify() route
    would raise on a value that rendered fine as text. Normalizing here means no
    caller has to know which backend it read from.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    """Same, for a count. A NULL count means zero rows matched, i.e. 0."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rate_pct(numerator: int, denominator: int) -> float:
    """Percentage, rounded to 1dp. Zero calls is 0.0%, never a ZeroDivision."""
    if denominator <= 0:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


def _shape(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one aggregate row into the shape every caller renders."""
    calls = _int(row.get("calls"))
    errors = _int(row.get("errors"))
    avg_ms = _num(row.get("avg_ms"))
    return {
        "calls": calls,
        "errors": errors,
        "running": _int(row.get("running")),
        "completed": _int(row.get("completed")),
        "error_rate_pct": _rate_pct(errors, calls),
        "avg_ms": round(avg_ms, 1) if avg_ms is not None else None,
        "max_ms": _int(row.get("max_ms")) if row.get("max_ms") is not None else None,
    }


class InvocationStore:
    """Query ``runtime_invocations``. Read-only; safe to construct per call."""

    def __init__(self, connection_factory: Optional[Callable[[], Any]] = None) -> None:
        """Args:
            connection_factory: callable returning a DB connection. Defaults to
                ``tools.db.storage.get_connection``.
        """
        if connection_factory is None:
            from tools.db.storage import get_connection

            connection_factory = get_connection
        self._connect = connection_factory
        #: Message from the last failed query, or None. See the module docstring.
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def by_surface(self, since: Optional[str] = None) -> List[Dict[str, Any]]:
        """One row per surface — mcp / agent / persona / role.

        Args:
            since: ISO-8601 UTC timestamp; only invocations started at or after
                it are counted. ``started_at`` is a TEXT column holding
                ``datetime.isoformat()`` output on both backends, so this is a
                lexicographic comparison — correct for UTC ISO-8601, and the
                recorder never writes anything else.

        Returns:
            ``[{surface, names, calls, errors, running, completed,
                error_rate_pct, avg_ms, max_ms}]``, busiest surface first.
        """
        where, params = self._window(since)
        sql = (
            "SELECT surface, "
            "       count(*) AS calls, "
            "       count(DISTINCT name) AS names, " + self._AGGREGATES +
            f"FROM {_TABLE} " + where +
            "GROUP BY surface ORDER BY calls DESC"
        )
        rows = self._run(sql, params, "by_surface")
        return [
            dict(_shape(r), surface=r.get("surface") or "", names=_int(r.get("names")))
            for r in rows
        ]

    def by_name(self, surface: Optional[str] = None, since: Optional[str] = None,
                limit: int = 20, order_by: str = "calls") -> List[Dict[str, Any]]:
        """One row per (surface, name) — per tool / agent / persona / role.

        Args:
            surface: restrict to one surface.
            since: as :meth:`by_surface`.
            limit: rows to return, capped at :data:`MAX_ROWS`.
            order_by: ``calls`` (default), ``errors`` or ``duration``. The last
                two are what you actually want when hunting a bad tool, and
                sorting in SQL rather than in Python keeps the LIMIT meaningful
                — a Python sort of the top-20-by-calls cannot surface the
                slowest tool if it is called twice a day.
        """
        where, params = self._window(since)
        if surface:
            where = (where + "AND " if where else "WHERE ") + "surface = %s "
            params.append(surface)

        sql = (
            "SELECT surface, name, count(*) AS calls, " + self._AGGREGATES +
            f"FROM {_TABLE} " + where +
            "GROUP BY surface, name ORDER BY " + self._order(order_by) + " LIMIT %s"
        )
        params.append(max(1, min(int(limit), MAX_ROWS)))
        rows = self._run(sql, params, "by_name")
        return [
            dict(_shape(r), surface=r.get("surface") or "", name=r.get("name") or "")
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    #: The columns every rollup shares. ``sum(CASE WHEN ...)`` rather than the
    #: `count(*) FILTER (WHERE ...)` PostgreSQL would prefer, because SQLite has
    #: no FILTER clause and this file must run identically on both.
    #:
    #: `completed` counts rows that have a duration, which is the denominator
    #: `avg_ms` actually used — avg() skips NULLs and a `running` row has none.
    _AGGREGATES = (
        "       sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors, "
        "       sum(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running, "
        "       sum(CASE WHEN duration_ms IS NULL THEN 0 ELSE 1 END) AS completed, "
        "       avg(duration_ms) AS avg_ms, "
        "       max(duration_ms) AS max_ms "
    )

    #: Sort keys, resolved from a closed vocabulary so no caller-supplied string
    #: ever reaches the SQL text.
    _ORDERS = {
        "calls": "calls DESC",
        "errors": "errors DESC, calls DESC",
        "duration": "max_ms DESC, calls DESC",
    }

    @classmethod
    def _order(cls, order_by: str) -> str:
        return cls._ORDERS.get((order_by or "").lower(), cls._ORDERS["calls"])

    @staticmethod
    def _window(since: Optional[str]) -> tuple:
        if since:
            return "WHERE started_at >= %s ", [since]
        return "", []

    def _run(self, sql: str, params: List[Any], label: str) -> List[Dict[str, Any]]:
        """Execute one read. Records the failure rather than raising it."""
        self.last_error = None
        try:
            conn = self._connect()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.warning("InvocationStore.%s could not connect: %s", label, exc)
            return []
        try:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.warning("InvocationStore.%s failed: %s", label, exc)
            # A failed statement poisons the whole transaction on PostgreSQL, so
            # clear it before the connection goes back to the pool.
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return []
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — close must never mask a result
                pass
