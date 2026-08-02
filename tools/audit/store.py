# CUI // SP-CTI
"""Read-only query layer over the audit feed (``audit_trail`` + ``hook_events``).

``audit_trail`` holds the richest observability data ICDEV produces — 15k+ rows
across 246 event types, written from 165 call sites — and until now nothing but
the dashboard could read it. ``tools/audit/audit_query.py`` reads by project;
``tools/cli/audit.py`` exports SOC 2 evidence. Neither answers "what just
happened", which is the question an operator actually asks.

This module exists to back ``icdev audit tail``. It is deliberately small:

  * READ ONLY. Nothing here writes. ``audit_trail`` is append-only under NIST AU
    and must never be UPDATEd or DELETEd, so a query layer that cannot write is
    the safe shape.
  * No ``subscribe()`` / callback surface. The source analysis proposed one; no
    consumer needs it, and an unexercised callback API is a liability rather
    than an affordance. Add it when something subscribes.

## Why the merge is done in Python

The two tables have different shapes (``audit_trail.event_type`` vs
``hook_events.hook_type``, ``action``/``details`` vs ``tool_name``/``message``)
and different id spaces, so a SQL ``UNION ALL`` would need per-column casts and
aliasing that differ between PostgreSQL and SQLite. Each table is queried with
its own plain SELECT — ordered and limited server-side so we never pull more
than ``limit`` rows per source — and the two already-bounded lists are merged
here. That keeps the SQL dialect-neutral, which matters because PostgreSQL is
the primary backend and SQLite is the init/test fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Logical sources a caller can ask for.
SOURCE_AUDIT = "audit_trail"
SOURCE_HOOK = "hook_events"
ALL_SOURCES = (SOURCE_AUDIT, SOURCE_HOOK)


@dataclass
class AuditFilter:
    """Filters for a feed query. All fields optional and AND-combined."""

    project_id: Optional[str] = None
    event_types: Optional[Sequence[str]] = None
    actor: Optional[str] = None
    since: Optional[str] = None          # ISO-8601; rows strictly newer are returned
    sources: Sequence[str] = field(default_factory=lambda: list(ALL_SOURCES))
    limit: int = 50


def _iso(value: Any) -> str:
    """Normalize a DB timestamp (datetime on PG, str on SQLite) to ISO-8601."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _sort_key(row: Dict[str, Any]) -> tuple:
    """Newest first, with a stable tiebreak.

    ``created_at`` alone is not unique — bulk writes share a timestamp — so ties
    fall back to the numeric id. Without the tiebreak, --follow could re-print or
    skip rows that share a timestamp with the last one seen.
    """
    return (row.get("created_at") or "", row.get("id") or 0)


class AuditStore:
    """Query the audit feed. Read-only; safe to construct per call."""

    def __init__(self, connection_factory=None) -> None:
        """Args:
            connection_factory: callable returning a DB connection. Defaults to
                ``tools.db.storage.get_connection`` — injectable so tests can
                point at a fixture DB without patching a module global.
        """
        if connection_factory is None:
            from tools.db.storage import get_connection

            connection_factory = get_connection
        self._connect = connection_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tail(self, filters: Optional[AuditFilter] = None) -> List[Dict[str, Any]]:
        """Return the most recent events across the requested sources.

        Newest first. Each row is normalized to a common shape so a caller can
        render both tables without branching:

            {id, source, created_at, event_type, actor, project_id,
             summary, severity, session_id}
        """
        f = filters or AuditFilter()
        limit = max(1, int(f.limit))

        rows: List[Dict[str, Any]] = []
        conn = self._connect()
        try:
            if SOURCE_AUDIT in f.sources:
                rows.extend(self._query_audit_trail(conn, f, limit))
            if SOURCE_HOOK in f.sources:
                rows.extend(self._query_hook_events(conn, f, limit))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — close must never mask a result
                pass

        rows.sort(key=_sort_key, reverse=True)
        return rows[:limit]

    def event_types(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Distinct audit_trail event types with counts, most frequent first.

        Backs ``--event-type`` discoverability: 246 valid types is too many to
        remember, and the useful subset is whatever this deployment emits.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT event_type, count(*) AS n FROM audit_trail "
                "GROUP BY event_type ORDER BY n DESC LIMIT %s",
                (int(limit),),
            )
            return [{"event_type": dict(r)["event_type"], "count": dict(r)["n"]}
                    for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("AuditStore.event_types failed: %s", exc)
            return []
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Per-source queries
    # ------------------------------------------------------------------

    def _query_audit_trail(self, conn, f: AuditFilter, limit: int) -> List[Dict[str, Any]]:
        where: List[str] = []
        params: List[Any] = []
        if f.project_id:
            where.append("project_id = %s")
            params.append(f.project_id)
        if f.actor:
            where.append("actor = %s")
            params.append(f.actor)
        if f.since:
            where.append("created_at > %s")
            params.append(f.since)
        if f.event_types:
            # Placeholders are generated from the COUNT of values, never from the
            # values themselves, so this stays parameterized.
            where.append("event_type IN (" + ",".join(["%s"] * len(f.event_types)) + ")")
            params.extend(f.event_types)

        sql = (
            "SELECT id, project_id, event_type, actor, action, details, "
            "       classification, session_id, created_at "
            "FROM audit_trail "
            + ("WHERE " + " AND ".join(where) + " " if where else "")
            + "ORDER BY created_at DESC, id DESC LIMIT %s"
        )
        params.append(limit)

        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except Exception as exc:  # noqa: BLE001 — a missing table must not kill the feed
            logger.warning("AuditStore: audit_trail query failed: %s", exc)
            self._rollback(conn)
            return []

        out = []
        for r in rows:
            d = dict(r)
            out.append({
                "id": d.get("id"),
                "source": SOURCE_AUDIT,
                "created_at": _iso(d.get("created_at")),
                "event_type": d.get("event_type") or "",
                "actor": d.get("actor") or "",
                "project_id": d.get("project_id") or "",
                "summary": (d.get("action") or d.get("details") or "").strip(),
                "severity": "info",
                "session_id": d.get("session_id") or "",
                "classification": d.get("classification") or "",
            })
        return out

    def _query_hook_events(self, conn, f: AuditFilter, limit: int) -> List[Dict[str, Any]]:
        where: List[str] = []
        params: List[Any] = []
        if f.project_id:
            where.append("project_id = %s")
            params.append(f.project_id)
        if f.since:
            where.append("created_at > %s")
            params.append(f.since)
        if f.event_types:
            where.append("hook_type IN (" + ",".join(["%s"] * len(f.event_types)) + ")")
            params.extend(f.event_types)
        # `actor` has no hook_events analogue; filtering on it excludes this
        # source entirely rather than silently ignoring the filter.
        if f.actor:
            return []

        sql = (
            "SELECT id, project_id, hook_type, tool_name, message, severity, "
            "       classification, session_id, created_at "
            "FROM hook_events "
            + ("WHERE " + " AND ".join(where) + " " if where else "")
            + "ORDER BY created_at DESC, id DESC LIMIT %s"
        )
        params.append(limit)

        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.warning("AuditStore: hook_events query failed: %s", exc)
            self._rollback(conn)
            return []

        out = []
        for r in rows:
            d = dict(r)
            summary = (d.get("message") or "").strip()
            if not summary and d.get("tool_name"):
                summary = f"tool: {d['tool_name']}"
            out.append({
                "id": d.get("id"),
                "source": SOURCE_HOOK,
                "created_at": _iso(d.get("created_at")),
                "event_type": d.get("hook_type") or "",
                "actor": d.get("tool_name") or "",
                "project_id": d.get("project_id") or "",
                "summary": summary,
                "severity": d.get("severity") or "info",
                "session_id": d.get("session_id") or "",
                "classification": d.get("classification") or "",
            })
        return out

    @staticmethod
    def _rollback(conn) -> None:
        """Clear an aborted transaction.

        On PostgreSQL a failed statement poisons the whole transaction — every
        subsequent query returns 'current transaction is aborted' until it is
        rolled back. Without this, one missing table would make every LATER
        source look broken too.
        """
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
