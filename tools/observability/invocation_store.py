# CUI // SP-CTI
"""Read-only query layer over ``runtime_invocations``.

Migration 341 created the table and ``invocation_recorder.record()`` fills it —
every MCP tool call, agent run, persona run and role step. Nothing could read
it. ``audit_trail`` got ``icdev audit tail`` backed by ``tools/audit/store.py``;
this is the same shape for the telemetry table, and it backs both
``icdev runtime top`` and the SRE dashboard panel.

Deliberately mirrors ``tools/audit/store.py``:

  * READ ONLY. Nothing here writes. The recorder owns the write path.
  * Injectable ``connection_factory`` so a test can point at a fixture DB
    without patching a module global.
  * A failed statement is rolled back before returning. On PostgreSQL one
    failure poisons the whole transaction, so a missing table would otherwise
    make every LATER query on the same connection look broken too.

## Why the per-surface rollup is folded in Python

There is exactly ONE SQL statement here — a ``GROUP BY surface, name``. The
per-surface totals are derived from its rows by :func:`rollup_by_surface`
rather than by a second ``GROUP BY surface`` query, because two aggregate
queries against a table that is still being written to can disagree with each
other, and because a pure fold is testable without a database at all.

The fold is not a sum of averages. ``avg(duration_ms)`` skips rows whose
duration is NULL — an invocation still ``running`` has no duration yet — so the
weight for a name's average is ``timed`` (the count of rows that HAVE a
duration), not ``calls``. Weighting by ``calls`` would silently drag the
average toward whichever tool happened to have the most in-flight rows at read
time. ``timed`` is selected for that reason and for no other.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

TABLE = "runtime_invocations"

#: Sentinel for "every row" — see :meth:`InvocationStore.by_name`.
NO_LIMIT = 0


@dataclass
class InvocationFilter:
    """Filters for a rollup query. All fields optional and AND-combined."""

    surface: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    since: Optional[str] = None          # ISO-8601; rows strictly newer are counted
    limit: int = 20                      # 0 / NO_LIMIT = every group


def _num(value: Any) -> Optional[float]:
    """Coerce a DB aggregate to a plain float.

    PostgreSQL returns ``Decimal`` from ``avg()`` and ``sum()`` while SQLite
    returns ``float``/``int``. Left alone, a ``Decimal`` breaks ``json.dumps``
    and cannot be multiplied by the float weights in the fold below — so the
    difference is normalized once, here, rather than at every consumer.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    return int(_num(value) or 0)


def _iso(value: Any) -> str:
    """Normalize a DB timestamp (datetime on PG, str on SQLite) to ISO-8601."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def error_rate(calls: Any, errors: Any) -> float:
    """Errors as a fraction of calls. Zero calls is 0.0, never a ZeroDivision."""
    total = _int(calls)
    return (_int(errors) / total) if total else 0.0


def rollup_by_surface(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fold per-name rollup rows into one row per surface.

    Pure: no database, no I/O. Given the rows :meth:`InvocationStore.by_name`
    returns, produces ``[{surface, names, calls, errors, error_rate, timed,
    avg_ms, max_ms}]`` ordered by call volume.

    ``avg_ms`` is the ``timed``-weighted mean of the per-name averages, which
    is the true mean over every timed invocation on the surface — see the
    module docstring for why the weight is ``timed`` and not ``calls``. A
    surface with no completed invocation yet reports ``avg_ms``/``max_ms`` of
    ``None`` rather than 0, because "nothing has finished" and "everything
    finished instantly" are different facts.
    """
    acc: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        surface = row.get("surface") or ""
        agg = acc.setdefault(surface, {
            "surface": surface,
            "names": 0,
            "calls": 0,
            "errors": 0,
            "timed": 0,
            "_weighted_ms": 0.0,
            "max_ms": None,
        })
        agg["names"] += 1
        agg["calls"] += _int(row.get("calls"))
        agg["errors"] += _int(row.get("errors"))

        timed = _int(row.get("timed"))
        avg_ms = _num(row.get("avg_ms"))
        if timed and avg_ms is not None:
            agg["timed"] += timed
            agg["_weighted_ms"] += avg_ms * timed

        max_ms = _num(row.get("max_ms"))
        if max_ms is not None and (agg["max_ms"] is None or max_ms > agg["max_ms"]):
            agg["max_ms"] = max_ms

    out: List[Dict[str, Any]] = []
    for agg in acc.values():
        timed = agg.pop("timed")
        weighted = agg.pop("_weighted_ms")
        agg["timed"] = timed
        agg["avg_ms"] = (weighted / timed) if timed else None
        agg["error_rate"] = error_rate(agg["calls"], agg["errors"])
        out.append(agg)

    out.sort(key=lambda a: (-a["calls"], a["surface"]))
    return out


class InvocationStore:
    """Query ``runtime_invocations``. Read-only; safe to construct per call."""

    def __init__(self, connection_factory=None) -> None:
        """Args:
            connection_factory: callable returning a DB connection. Defaults to
                ``tools.db.storage.get_connection``.
        """
        if connection_factory is None:
            from tools.db.storage import get_connection

            connection_factory = get_connection
        self._connect = connection_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def by_name(self, filters: Optional[InvocationFilter] = None) -> List[Dict[str, Any]]:
        """Per-``(surface, name)`` rollup, busiest first.

        Each row: ``{surface, name, calls, errors, error_rate, timed, avg_ms,
        max_ms, last_started_at}``. Returns ``[]`` rather than raising if the
        migration has not run — a reporting command must not be the thing that
        fails on an un-migrated database.
        """
        f = filters or InvocationFilter()
        where, params = self._where(f)

        sql = (
            "SELECT surface, name, "
            "       count(*) AS calls, "
            "       sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors, "
            "       count(duration_ms) AS timed, "
            "       avg(duration_ms) AS avg_ms, "
            "       max(duration_ms) AS max_ms, "
            "       max(started_at) AS last_started_at "
            f"FROM {TABLE} "
            + where
            # Ties are broken on (surface, name) so two runs against the same
            # data print the same order — otherwise a --json diff is noise.
            + "GROUP BY surface, name ORDER BY calls DESC, surface ASC, name ASC"
        )
        limit = max(0, int(f.limit or 0))
        if limit:
            sql += " LIMIT %s"
            params = params + [limit]

        conn = self._connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except Exception as exc:  # noqa: BLE001 — a missing table must not kill the report
            logger.warning("InvocationStore.by_name failed: %s", exc)
            self._rollback(conn)
            return []
        finally:
            self._close(conn)

        out = []
        for r in rows:
            d = dict(r)
            calls = _int(d.get("calls"))
            errors = _int(d.get("errors"))
            out.append({
                "surface": d.get("surface") or "",
                "name": d.get("name") or "",
                "calls": calls,
                "errors": errors,
                "error_rate": error_rate(calls, errors),
                "timed": _int(d.get("timed")),
                "avg_ms": _num(d.get("avg_ms")),
                "max_ms": _num(d.get("max_ms")),
                "last_started_at": _iso(d.get("last_started_at")),
            })
        return out

    def by_surface(self, filters: Optional[InvocationFilter] = None) -> List[Dict[str, Any]]:
        """Per-surface totals.

        Reads EVERY group (``limit`` is ignored) and folds them, so the totals
        are the real totals. Deriving them from a top-N list would silently
        report "20 tools' worth of calls" as though it were the whole surface.
        """
        f = filters or InvocationFilter()
        every = InvocationFilter(
            surface=f.surface, name=f.name, status=f.status,
            since=f.since, limit=NO_LIMIT,
        )
        return rollup_by_surface(self.by_name(every))

    def report(self, filters: Optional[InvocationFilter] = None) -> Dict[str, Any]:
        """Both rollups from ONE read, for a CLI or an API to render.

        The per-name list is truncated to ``filters.limit``; the per-surface
        totals are computed from the untruncated set first, so the headline
        numbers stay correct however small the top-N is.
        """
        f = filters or InvocationFilter()
        every = InvocationFilter(
            surface=f.surface, name=f.name, status=f.status,
            since=f.since, limit=NO_LIMIT,
        )
        names = self.by_name(every)
        limit = max(0, int(f.limit or 0))
        return {
            "surfaces": rollup_by_surface(names),
            "names": names[:limit] if limit else names,
            "total_names": len(names),
            "filters": {
                "surface": f.surface, "name": f.name,
                "status": f.status, "since": f.since, "limit": limit,
            },
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _where(f: InvocationFilter) -> tuple:
        clauses: List[str] = []
        params: List[Any] = []
        if f.surface:
            clauses.append("surface = %s")
            params.append(f.surface)
        if f.name:
            clauses.append("name = %s")
            params.append(f.name)
        if f.status:
            clauses.append("status = %s")
            params.append(f.status)
        if f.since:
            clauses.append("started_at > %s")
            params.append(f.since)
        return (("WHERE " + " AND ".join(clauses) + " ") if clauses else "", params)

    @staticmethod
    def _rollback(conn) -> None:
        """Clear an aborted transaction so the connection stays usable."""
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _close(conn) -> None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — close must never mask a result
            pass
