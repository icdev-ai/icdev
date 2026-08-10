#!/usr/bin/env python3
# CUI // SP-CTI
"""AGOV CASE — join the per-session agent records ICDEV already writes into one
ordered timeline.

ICDEV writes rich agent activity into several append-only tables and, before
AGOV, never read any of it back keyed by session. This module is the join: given
a ``session_id`` it returns a single ordered list of normalized entries plus an
explicit statement of what could NOT be joined.

Joinable sources — these tables carry a ``session_id`` column:

===================  =========================================================
``hook_events``      every pre/post tool-use hook, HMAC-signed
``audit_trail``      the immutable audit log, hash-chained since migration 149
``agent_findings``   AGOV detection findings (present once agov-det-05 lands)
===================  =========================================================

NOT joinable, and this is a real limitation rather than an oversight:
``agent_executions``, ``ai_telemetry`` and ``ace_audit_log`` all record agent
activity but none of them has a ``session_id`` column — they key on
``execution_id``, ``agent_id``/``user_id`` and ``instance_id`` respectively. A
timeline cannot silently omit them, so every result names them under
``limits``. Correlating them needs a schema change, not a wider SELECT.

Ordering is by timestamp, then source, then record id, so two runs over the same
data produce the same sequence. Rows whose timestamp is NULL or empty cannot be
placed in time: they sort last, are counted in ``undated``, and keep their
records rather than being dropped.

Usage:
    from tools.agent_case.session_timeline import build_timeline
    result = build_timeline("sess-abc123")
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Run by path, sys.path[0] is this file's own directory — never the import root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import (  # noqa: E402
    get_connection,
    list_tables,
    sql_placeholder,
)

# --- Source declarations ---------------------------------------------------

# Each entry: table -> what to select and how to normalize it. ``columns`` is an
# explicit allowlist rather than ``SELECT *`` so a later ALTER TABLE cannot
# silently widen a forensic export, and so the bundler's record files have a
# stable shape the verifier can rely on.
SOURCES = {
    "hook_events": {
        "id_column": "id",
        "time_column": "created_at",
        "columns": (
            "id", "session_id", "hook_type", "tool_name", "project_id",
            "payload", "classification", "signature", "created_at",
        ),
        "kind_column": "hook_type",
        "actor_column": "session_id",
        "summary_column": "tool_name",
    },
    "audit_trail": {
        "id_column": "id",
        "time_column": "created_at",
        "columns": (
            "id", "project_id", "event_type", "actor", "action", "details",
            "affected_files", "classification", "ip_address", "session_id",
            "recorded_at", "created_at",
        ),
        # Migration 149 adds these to audit_trail. They are probed per-database
        # rather than assumed: a checkout whose SQLite file stopped at an earlier
        # migration has the table but not the columns, and naming a missing
        # column in the SELECT fails the whole query instead of one field.
        "optional_columns": ("hash", "previous_hash"),
        "kind_column": "event_type",
        "actor_column": "actor",
        "summary_column": "action",
    },
    "agent_findings": {
        "id_column": "finding_id",
        "time_column": "created_at",
        "columns": (
            "finding_id", "rule_id", "rule_version", "severity", "title",
            "session_id", "actor", "project_id", "event_ids", "tags",
            "decision", "classification", "created_at",
        ),
        "kind_column": "severity",
        "actor_column": "actor",
        "summary_column": "title",
    },
}

# Deterministic ordering when two records share a timestamp.
SOURCE_ORDER = ("hook_events", "audit_trail", "agent_findings")

# Tables that hold agent activity but cannot be keyed by session_id, with the
# column they actually key on. Reported on every result.
UNJOINABLE_SOURCES = {
    "agent_executions": "execution_id (no session_id column)",
    "ai_telemetry": "agent_id / user_id (no session_id column)",
    "ace_audit_log": "instance_id (no session_id column)",
}


def _row_to_dict(cursor, row) -> dict:
    """Materialize a row as a plain dict regardless of row factory."""
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        names = [d[0] for d in cursor.description]
        return dict(zip(names, row))


def _table_columns(conn, table: str) -> set:
    """Column names of ``table``, or an empty set if it cannot be described.

    Uses a single describe per table. A per-column trial SELECT would be worse
    on PostgreSQL: the first failure aborts the transaction and every later
    probe in the same transaction then reports "missing" whether it is or not.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT * FROM {table} WHERE 1=0")  # nosec B608 -- table is a SOURCES key, never caller input
        return {d[0] for d in (cursor.description or ())}
    finally:
        with contextlib.suppress(Exception):  # cursor may already be closed
            cursor.close()


def _fetch_source(conn, table: str, spec: dict, session_id: str,
                  since: str = None, until: str = None,
                  limit: int = None) -> list:
    """Fetch this source's rows for one session, newest constraint applied in SQL."""
    available = _table_columns(conn, table)
    columns = [c for c in spec["columns"] if c in available]
    if not columns:
        return []
    for optional in spec.get("optional_columns", ()):
        if optional in available:
            columns.append(optional)

    ph = sql_placeholder(conn)
    # Interpolated identifiers come only from SOURCES and from the live table's
    # own columns; every caller-supplied value is bound as a parameter below.
    sql = (f"SELECT {', '.join(columns)} FROM {table} "  # nosec B608 -- identifiers are module constants, values are bound
           f"WHERE session_id = {ph}")
    params = [session_id]
    time_column = spec["time_column"]
    if since and time_column in available:
        sql += f" AND {time_column} >= {ph}"
        params.append(since)
    if until and time_column in available:
        sql += f" AND {time_column} <= {ph}"
        params.append(until)
    sql += f" ORDER BY {time_column}, {spec['id_column']}"
    if limit:
        sql += f" LIMIT {int(limit)}"

    cursor = conn.cursor()
    cursor.execute(sql, params)
    return [_row_to_dict(cursor, row) for row in cursor.fetchall()]


def _normalize(table: str, spec: dict, record: dict) -> dict:
    """One timeline entry. ``record`` is carried verbatim — the entry is a view."""
    return {
        "source": table,
        "record_id": record.get(spec["id_column"]),
        "at": record.get(spec["time_column"]) or None,
        "kind": record.get(spec["kind_column"]),
        "actor": record.get(spec["actor_column"]),
        "summary": record.get(spec["summary_column"]),
        "classification": record.get("classification") or "CUI",
        "record": record,
    }


def _sort_key(entry: dict):
    """Undated entries last; otherwise timestamp, then source, then id — as text.

    Record ids are mixed-type across sources (``hook_events.id`` is an integer,
    ``agent_findings.finding_id`` is text), so comparing them directly raises on
    a tie. Comparing their string forms only decides ties and keeps the order
    reproducible.
    """
    at = entry.get("at")
    try:
        source_rank = SOURCE_ORDER.index(entry["source"])
    except ValueError:
        source_rank = len(SOURCE_ORDER)
    return (at is None, str(at or ""), source_rank, str(entry.get("record_id") or ""))


def build_timeline(session_id: str, conn=None, since: str = None,
                   until: str = None, limit: int = None) -> dict:
    """Ordered timeline for one agent session.

    Args:
        session_id: The session to reconstruct. Required; a timeline over "all
            sessions" is a feed, not a case, and the dashboard already has one.
        conn: Optional open connection. When omitted one is opened and closed.
        since / until: Inclusive ISO-8601 bounds on each source's timestamp.
        limit: Cap on rows fetched PER SOURCE, applied in SQL.

    Returns:
        dict with ``entries``, ``sources``, ``limits``, ``counts`` and
        ``undated``. Never raises for a session that does not exist — that is an
        empty timeline, which is a finding rather than an error.
    """
    if not session_id:
        raise ValueError("session_id is required")

    owns_conn = conn is None
    conn = conn or get_connection()
    result = {
        "session_id": session_id,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "classification": "CUI",
        "window": {"since": since, "until": until, "limit_per_source": limit},
        "sources": {},
        "limits": [],
        "entries": [],
    }
    try:
        present = set(list_tables(conn))
        for table in SOURCE_ORDER:
            spec = SOURCES[table]
            if table not in present:
                result["sources"][table] = {
                    "present": False, "records": 0,
                    "note": "table does not exist in this database",
                }
                continue
            records = _fetch_source(conn, table, spec, session_id,
                                    since=since, until=until, limit=limit)
            result["sources"][table] = {"present": True, "records": len(records)}
            if limit and len(records) == int(limit):
                result["sources"][table]["truncated"] = True
                result["limits"].append(
                    f"{table}: hit --limit {limit}; the timeline is a prefix, not the whole session"
                )
            for record in records:
                result["entries"].append(_normalize(table, spec, record))
    finally:
        if owns_conn:
            conn.close()

    result["entries"].sort(key=_sort_key)
    for seq, entry in enumerate(result["entries"], start=1):
        entry["seq"] = seq

    result["undated"] = sum(1 for e in result["entries"] if e["at"] is None)
    result["counts"] = {
        "entries": len(result["entries"]),
        "sources_joined": sum(1 for s in result["sources"].values() if s.get("present")),
    }
    if result["undated"]:
        result["limits"].append(
            f"{result['undated']} entries carry no timestamp and are ordered last, "
            "not in place"
        )
    for table, keyed_on in UNJOINABLE_SOURCES.items():
        result["limits"].append(
            f"{table} is NOT in this timeline: it keys on {keyed_on}, so its rows "
            "cannot be attributed to a session without a schema change"
        )
    return result


def format_timeline(result: dict) -> str:
    """Human-readable timeline. Limits print even when the timeline is empty."""
    lines = ["CUI // SP-CTI", f"Session: {result['session_id']}",
             f"Built at: {result['built_at']}"]
    joined = ", ".join(
        f"{t}={s['records']}" for t, s in result["sources"].items() if s.get("present")
    ) or "none"
    lines.append(f"Sources joined: {joined}")
    lines.append(f"Entries: {result['counts']['entries']}")
    lines.append("")

    if not result["entries"]:
        lines.append("(no records for this session in any joinable source)")
    for entry in result["entries"]:
        at = entry["at"] or "(undated)"
        lines.append(
            f"{entry['seq']:>5}  {at}  {entry['source']}#{entry['record_id']}  "
            f"[{entry['kind']}] {entry['summary'] or ''}".rstrip()
        )
        if entry.get("actor"):
            lines.append(f"         actor: {entry['actor']}")

    if result["limits"]:
        lines.append("")
        lines.append("Limits:")
        for limit in result["limits"]:
            lines.append(f"  * {limit}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Join ICDEV's per-session agent records into one ordered timeline.")
    parser.add_argument("--session", required=True, help="session_id to reconstruct")
    parser.add_argument("--since", help="Inclusive ISO-8601 lower bound")
    parser.add_argument("--until", help="Inclusive ISO-8601 upper bound")
    parser.add_argument("--limit", type=int, help="Max rows per source")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    result = build_timeline(args.session, since=args.since, until=args.until,
                           limit=args.limit)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_timeline(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
