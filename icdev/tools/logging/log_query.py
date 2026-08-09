#!/usr/bin/env python3
# CUI // SP-CTI
"""Query the EQO centralized_logs sink (eqo-log-04).

``query_logs`` is the single read path shared by the ``/logs`` dashboard page,
the ``GET /api/logs`` route, and this module's ``__main__`` CLI. It reads from
the append-only ``centralized_logs`` table (created by migration 181) through the
RLS-aware :func:`tools.db.storage.get_connection`, so every row returned is
already filtered to the caller's tenant + classification.

Filters (all optional, AND-combined):
  component  exact match on the emitting component
  level      exact match on the log level (DEBUG/INFO/WARNING/ERROR/CRITICAL)
  since      ISO-8601 lower bound on ``ts`` (``ts >= since``)
  contains   case-insensitive substring match on ``message``
  limit      max rows (bounded by MAX_LIMIT), newest first

The query degrades to an empty list if the table is not yet present (migration
not run on this DB) rather than raising — the dashboard shows an empty state.

CLI:
    python tools/logging/log_query.py --component genesis --level ERROR --json
    python tools/logging/log_query.py --contains timeout --since 2026-06-06 --limit 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.constants import DEFAULT_LIMIT, LOGS_TABLE, MAX_LIMIT


def _is_pg(conn) -> bool:
    """True when the active backend is PostgreSQL (``?`` → ``%s`` placeholders)."""
    declared = getattr(conn, "_backend", None)
    if declared:
        return str(declared).lower().startswith(("postgre", "pg"))
    return os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower() in (
        "postgresql", "postgres", "pg",
    )


def _q(conn, sql: str) -> str:
    """Translate ``?`` placeholders to ``%s`` for the PostgreSQL backend."""
    return sql.replace("?", "%s") if _is_pg(conn) else sql


def query_logs(
    component: Optional[str] = None,
    level: Optional[str] = None,
    since: Optional[str] = None,
    contains: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    conn: Any = None,
) -> list[dict]:
    """Return centralized_logs rows matching the filters, newest first.

    Args:
        component: exact ``component`` match.
        level: exact ``level`` match.
        since: ISO-8601 lower bound — keeps rows with ``ts >= since``.
        contains: case-insensitive substring match on ``message``.
        limit: max rows returned, clamped to ``[1, MAX_LIMIT]``.
        conn: optional RLS-aware connection; one is opened (and closed) if omitted.

    Returns:
        A list of plain dicts (one per row). Empty if nothing matches or the
        table does not yet exist.
    """
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    n = max(1, min(n, MAX_LIMIT))

    owned = False
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection()
        owned = True

    where: list[str] = []
    params: list[Any] = []
    if component:
        where.append("component = ?")
        params.append(component)
    if level:
        where.append("level = ?")
        params.append(level)
    if since:
        where.append("ts >= ?")
        params.append(since)
    if contains:
        where.append("LOWER(message) LIKE ?")
        params.append(f"%{contains.lower()}%")

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT id, ts, component, level, message, trace_id, session_id, "
        f"classification, tenant_id, extra_json FROM {LOGS_TABLE} "
        f"{clause} ORDER BY ts DESC, id DESC LIMIT ?"
    )
    params.append(n)

    try:
        cur = conn.execute(_q(conn, sql), tuple(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        # Table absent (migration not run) or transient read error — empty state.
        return []
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Query the EQO centralized_logs sink.")
    ap.add_argument("--component", help="exact component match")
    ap.add_argument("--level", help="exact level match (DEBUG/INFO/WARNING/ERROR/CRITICAL)")
    ap.add_argument("--since", help="ISO-8601 lower bound on ts (ts >= since)")
    ap.add_argument("--contains", help="case-insensitive substring match on message")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"max rows (<= {MAX_LIMIT})")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a text table")
    args = ap.parse_args(argv)

    rows = query_logs(
        component=args.component,
        level=args.level,
        since=args.since,
        contains=args.contains,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps({"count": len(rows), "rows": rows}, indent=2, default=str))
        return 0

    if not rows:
        print("(no matching log rows)")
        return 0

    for r in rows:
        ts = str(r.get("ts", ""))[:19]
        print(f"{ts}  {str(r.get('level','')):<8}  {str(r.get('component','')):<24}  {r.get('message','')}")
    print(f"\n{len(rows)} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
