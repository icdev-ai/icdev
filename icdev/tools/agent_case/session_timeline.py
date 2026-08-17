#!/usr/bin/env python3
# CUI // SP-CTI
"""AGOV CASE — join the per-session agent records ICDEV already writes into one
ordered timeline.

ICDEV writes rich agent activity into several append-only tables and, before
AGOV, never read any of it back keyed by session. This module is the join: given
a ``session_id`` it returns a single ordered list of normalized entries plus an
explicit statement of what could NOT be joined.

Joinable sources — these tables carry a ``session_id`` column:

=========================  ===================================================
``hook_events``            every pre/post tool-use hook, HMAC-signed
``audit_trail``            the immutable audit log, hash-chained since
                           migration 149
``agent_session_events``   the append-only agent event log (hcx-evt-01), one
                           row per model-visible event, ordered by ``seq``
``agent_findings``         AGOV detection findings (present once agov-det-05
                           lands)
=========================  ===================================================

NOT joinable, and this is a real limitation rather than an oversight:
``agent_executions``, ``ai_telemetry`` and ``ace_audit_log`` all record agent
activity but none of them has a ``session_id`` column — they key on
``execution_id``, ``agent_id``/``user_id`` and ``instance_id`` respectively. A
timeline cannot silently omit them, so every result names them under
``limits``. Correlating them needs a schema change, not a wider SELECT.
``agent_session_events`` is what a schema change looks like when it is made:
hcx-evt-01 gave it ``session_id`` by construction, so it joins with no special
case at all.

Joined, but NOT wholly: ``agent_session_events.payload_json`` is deliberately
not selected. It can hold verbatim model input, and both this timeline and the
case bundle built from it carry no transcript by construction rather than by
filtering (``case_bundler`` invariant 3). ``payload_hash`` IS carried — it is
the same SHA-256 recipe the migration-149 audit chain uses
(``tools/audit/row_hash.py``), so a holder of the payload can still prove what
it was. Every result where the table is present says this under ``limits``,
because "the payload is not here" and "there was no payload" are different
facts.

Ordering is by timestamp, then source, then the source's own tiebreak, then
record id, so two runs over the same data produce the same sequence. The
tiebreak exists for ``agent_session_events``: several of its events routinely
share one ``occurred_at``, which is exactly why hcx-evt-01 ordered that table by
a monotonic ``seq`` and held a UNIQUE index over ``(session_id, seq)``. Falling
back to its ``event_id`` — a random uuid — would be reproducible and wrong.
Rows whose timestamp is NULL or empty cannot be placed in time: they sort last,
are counted in ``undated``, and keep their records rather than being dropped.

Three properties are contractual, because a case bundle (agov-case-02) is
manifested by SHA-256 over what this module produces and a timeline that
reorders, leaks or over-discloses between runs cannot back one:

**Reproducible.** :func:`canonical_timeline` strips the one field that is a
property of the run rather than of the data (``built_at``) and
:func:`timeline_digest` hashes the rest. Two runs over identical data produce
identical bytes; nothing in the path calls an LLM, a clock or a random source.

**Session-scoped.** Every source is filtered by ``session_id`` in SQL, and a
finding that cites an event belonging to another session does not pull that
event in — the citation is reported under ``unresolved_event_ids`` instead.

**Redacted for display, verbatim for proof.** Each entry carries BOTH: the
source row untouched under ``record``, and masked strings under ``display``.
Renderers read ``display``; ``bundle_verifier`` re-computes HMACs and the audit
hash chain over ``record``. Redacting in place would break the second without
helping the first — see ``tools/agent_case/timeline_redaction.py``.

Findings are placed at the position of their LAST contributing event rather
than at their own timestamp. A rule fires *after* the chain it matched, often
seconds later and always at least once; sorting it by its own ``created_at``
would draw it detached from the events that caused it, which is precisely the
question an operator opens a timeline to answer. Every contributing id is
listed on the entry, not just the anchor.

Usage:
    from tools.agent_case.session_timeline import build_timeline
    result = build_timeline("sess-abc123")
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Run by path, sys.path[0] is this file's own directory — never the import root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_case.timeline_redaction import (  # noqa: E402
    TimelineRedactor,
    impact_level_for,
)
from tools.db.storage import (  # noqa: E402
    get_connection,
    list_tables,
    sql_placeholder,
)

# --- Source declarations ---------------------------------------------------


def _agent_event_summary(record: dict) -> str:
    """One-line summary of an ``agent_session_events`` row, from METADATA only.

    Every other source names a column that already holds a short human string
    (``tool_name``, ``action``, ``title``). This one has none: its human-readable
    content lives in ``payload_json``, which is precisely the column this module
    refuses to read. So the summary states where the event sits in the session
    and the digest that stands in for its content, and quotes nothing.
    """
    parts = []
    seq = record.get("seq")
    if seq is not None:
        parts.append(f"event seq {seq}")
    correlation = record.get("correlation_id")
    if correlation:
        parts.append(f"correlation {correlation}")
    digest = str(record.get("payload_hash") or "")
    parts.append(f"payload sha256:{digest[:12]}" if digest else "payload hash absent")
    return " | ".join(parts)


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
        "payload_column": "payload",
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
        "operand_columns": ("affected_files",),
    },
    "agent_session_events": {
        "id_column": "event_id",
        "time_column": "occurred_at",
        # payload_json is ABSENT from this allowlist on purpose — see
        # PAYLOAD_WITHHELD_NOTE. payload_hash stands in for it and is what a
        # holder of the payload re-verifies against.
        "columns": (
            "event_id", "session_id", "seq", "event_type", "occurred_at",
            "payload_hash", "tenant_id", "classification", "correlation_id",
        ),
        "kind_column": "event_type",
        "actor_column": "session_id",
        # No free-text title column exists here, and the one column that could
        # supply prose is the one deliberately not read. The summary is built
        # from metadata instead.
        "summary_column": None,
        "summary_builder": _agent_event_summary,
        # Ordering inside one timestamp is `seq`, the invariant hcx-evt-01 put a
        # UNIQUE index behind, never the random uuid in event_id.
        "tiebreak_column": "seq",
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
        "is_finding": True,
        "event_ids_column": "event_ids",
    },
}

# Deterministic ordering when two records share a timestamp. Findings stay last
# because they are derived FROM the other three: a rule that fired in the same
# second as the event it matched belongs after it, not interleaved with it.
SOURCE_ORDER = ("hook_events", "audit_trail", "agent_session_events",
                "agent_findings")

# --- Operand extraction ----------------------------------------------------

# The only payload keys a timeline entry will surface. This is an ALLOWLIST for
# the same reason agov-det-01 built one: a command quoted inside a tool's OUTPUT
# must never be rendered as though the agent ran it. Reading only these keys
# makes that unexpressible rather than merely discouraged.
OPERAND_KEYS = ("command", "file_path", "notebook_path", "path", "url")

# Payload keys whose value is itself a dict of arguments. Claude Code hook
# payloads nest the tool's arguments one level down, so a top-level-only scan
# would find no command on any real row.
OPERAND_CONTAINER_KEYS = ("tool_input", "input", "params", "args")

# Free text — model prose, tool output, file contents. Named explicitly so the
# guard below can fail at import if a later edit moves one into OPERAND_KEYS.
FREE_TEXT_KEYS = frozenset({
    "body", "content", "description", "details", "message", "output",
    "output_summary", "prompt", "reasoning", "response", "result", "stderr",
    "stdout", "text",
})

_overlap = FREE_TEXT_KEYS.intersection(OPERAND_KEYS).union(
    FREE_TEXT_KEYS.intersection(OPERAND_CONTAINER_KEYS))
if _overlap:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        f"OPERAND_KEYS must not read free text; remove {sorted(_overlap)}. "
        "Free text can quote a command the agent never ran."
    )

# Fields that describe the run rather than the data. Excluded from the
# canonical form so a digest over it is stable across runs.
VOLATILE_KEYS = ("built_at",)

# Tables that hold agent activity but cannot be keyed by session_id, with the
# column they actually key on. Reported on every result.
UNJOINABLE_SOURCES = {
    "agent_executions": "execution_id (no session_id column)",
    "ai_telemetry": "agent_id / user_id (no session_id column)",
    "ace_audit_log": "instance_id (no session_id column)",
}

# A source that IS joined, but not in full. Reported whenever
# agent_session_events is present, because a reader who sees the events but no
# payloads must be able to tell "withheld from this export" from "the log
# recorded no payload" — and hcx-evt-01 already spends a NULLable column and an
# explicit ``payload_withheld`` flag keeping exactly that pair apart at rest.
PAYLOAD_WITHHELD_NOTE = (
    "agent_session_events.payload_json is NOT read by this timeline: it can hold "
    "verbatim model input, and a case bundle built from this timeline carries no "
    "transcript by construction rather than by filtering. payload_hash IS carried "
    "for every event, so a holder of the payload can re-verify it with "
    "tools/audit/row_hash.py::compute_payload_hash. Read the log directly "
    "(tools/agent_runtime/event_log.py --with-payload) if you need the documents, "
    "and note that some are withheld at rest by args/agent_event_log.yaml too"
)


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
    # The tiebreak sits between the timestamp and the id so a --limit slice is
    # cut at the same place the Python sort would put the boundary.
    order_by = [time_column]
    tiebreak = spec.get("tiebreak_column")
    if tiebreak and tiebreak in available:
        order_by.append(tiebreak)
    order_by.append(spec["id_column"])
    sql += f" ORDER BY {', '.join(order_by)}"
    if limit:
        sql += f" LIMIT {int(limit)}"

    cursor = conn.cursor()
    cursor.execute(sql, params)
    return [_row_to_dict(cursor, row) for row in cursor.fetchall()]


def _json_list(value) -> list:
    """Coerce a stored JSON list to a Python list of strings.

    ``event_ids`` is a TEXT column holding JSON, but a caller passing an open
    connection with a JSON-aware adapter can hand back a real list. Anything
    that is neither is an empty citation, not a crash.
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        if isinstance(parsed, (list, tuple)):
            return [str(v) for v in parsed]
    return []


def _extract_operands(spec: dict, record: dict) -> dict:
    """The allowlisted operands this record names, as flat scalar strings.

    Only :data:`OPERAND_KEYS` are read, at the payload's top level and one level
    down inside :data:`OPERAND_CONTAINER_KEYS`. A nested key wins over a
    top-level one of the same name because the nested dict is the tool's own
    argument object; a malformed payload yields no operands rather than raising.
    """
    operands = {}
    for column in spec.get("operand_columns", ()):
        value = record.get(column)
        if isinstance(value, str) and value.strip():
            operands[column] = value

    payload_column = spec.get("payload_column")
    if not payload_column:
        return operands
    payload = record.get(payload_column)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return operands
    if not isinstance(payload, dict):
        return operands

    for source in (payload, *(payload.get(k) for k in OPERAND_CONTAINER_KEYS)):
        if not isinstance(source, dict):
            continue
        for key in OPERAND_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                operands[key] = value
    return operands


def _summary_for(spec: dict, record: dict):
    """The entry's summary string — a column, or a builder when no column fits.

    ``agent_session_events`` is the source with no column that fits: its only
    human-readable field is the payload this module refuses to read. A builder
    keeps that a per-source declaration rather than a branch in ``_normalize``.
    """
    builder = spec.get("summary_builder")
    if builder is not None:
        return builder(record)
    column = spec.get("summary_column")
    return record.get(column) if column else None


def _normalize(table: str, spec: dict, record: dict) -> dict:
    """One timeline entry. ``record`` is carried verbatim — the entry is a view."""
    entry = {
        "source": table,
        "record_id": record.get(spec["id_column"]),
        "at": record.get(spec["time_column"]) or None,
        "kind": record.get(spec["kind_column"]),
        "actor": record.get(spec["actor_column"]),
        "summary": _summary_for(spec, record),
        "classification": record.get("classification") or "CUI",
        "operands": _extract_operands(spec, record),
        "record": record,
    }
    if spec.get("is_finding"):
        entry["is_finding"] = True
        entry["contributing_event_ids"] = _json_list(
            record.get(spec.get("event_ids_column")))
    return entry


def event_key(entry: dict) -> str:
    """The id a finding cites this entry by.

    ``"<table>:<id>"`` is the shape agov-det-01's normalized view assigns to
    every projected event, so a finding written by the detection engine cites
    exactly this string and no translation table is needed between the two.
    """
    return f"{entry['source']}:{entry.get('record_id')}"


def _tiebreak(entry: dict) -> str:
    """The source's own within-timestamp order, as a sortable string.

    Only ``agent_session_events`` declares one, and only it needs one: several
    of its events share a single ``occurred_at`` and hcx-evt-01 orders them by a
    monotonic ``seq`` the database holds UNIQUE. Zero-padded so 2 sorts before
    10, and read off ``record`` rather than stored on the entry so no source's
    entry shape — and therefore no existing timeline digest — changes for a
    field only the sort consults.
    """
    column = (SOURCES.get(entry.get("source")) or {}).get("tiebreak_column")
    if not column:
        return ""
    value = (entry.get("record") or {}).get(column)
    if value is None:
        return ""
    try:
        return f"{int(value):020d}"
    except (TypeError, ValueError):
        return str(value)


def _sort_key(entry: dict):
    """Undated entries last; otherwise timestamp, source, tiebreak, id — as text.

    Record ids are mixed-type across sources (``hook_events.id`` is an integer,
    ``agent_findings.finding_id`` is text), so comparing them directly raises on
    a tie. Comparing their string forms only decides ties and keeps the order
    reproducible. The tiebreak is compared before the id and only ever against
    another row of the SAME source, because ``source_rank`` has already
    separated them.
    """
    at = entry.get("at")
    try:
        source_rank = SOURCE_ORDER.index(entry["source"])
    except ValueError:
        source_rank = len(SOURCE_ORDER)
    return (at is None, str(at or ""), source_rank, _tiebreak(entry),
            str(entry.get("record_id") or ""))


def _anchor_findings(entries: list) -> list:
    """Re-place each finding after the LAST event it cites. Returns the limits.

    ``entries`` must already be sorted by :func:`_sort_key`; it is reordered in
    place. Anchoring resolves only against events in THIS timeline, which is
    what keeps a cross-session citation from dragging a foreign event in: an id
    that is not present here is reported under ``unresolved_event_ids`` and the
    finding stays at its own timestamp.

    A finding cites events, never other findings, so anchors are resolved
    against non-finding entries only. Two findings anchored to the same event
    keep timestamp order between themselves.
    """
    limits = []
    position = {
        event_key(entry): index
        for index, entry in enumerate(entries)
        if not entry.get("is_finding")
    }

    order = []
    for index, entry in enumerate(entries):
        if not entry.get("is_finding"):
            order.append((index, 0, index))
            continue

        cited = entry.get("contributing_event_ids") or []
        resolved = [event_id for event_id in cited if event_id in position]
        unresolved = [event_id for event_id in cited if event_id not in position]
        entry["unresolved_event_ids"] = unresolved

        if resolved:
            anchor = max(resolved, key=lambda event_id: position[event_id])
            entry["anchored_to"] = anchor
            entry["anchor_reason"] = (
                f"placed after the last of {len(cited)} contributing events"
            )
            order.append((position[anchor], 1, index))
        else:
            entry["anchored_to"] = None
            entry["anchor_reason"] = (
                "no contributing event is in this timeline; placed by its own timestamp"
                if cited else "cites no events; placed by its own timestamp"
            )
            order.append((index, 0, index))

        if unresolved:
            # Bounded: a sequence rule can cite a long chain, and a limits list
            # that is mostly one finding's ids stops being read at all.
            shown = ", ".join(unresolved[:5])
            if len(unresolved) > 5:
                shown += f", +{len(unresolved) - 5} more"
            limits.append(
                f"finding {entry.get('record_id')} cites {len(unresolved)} event(s) "
                f"not in this timeline ({shown}); they belong to another session, "
                "fall outside --since/--until, or were cut by --limit"
            )

    entries[:] = [entries[index] for _, _, index in sorted(order)]
    return limits


def _apply_display(entry: dict, redactor) -> list:
    """Attach the strings a renderer may show. Returns the entities masked.

    ``kind`` is not redacted: it is a schema vocabulary term (``pre_tool_use``,
    ``code_change``, ``high``) chosen by ICDEV, never user content, and masking
    it would only make the timeline unreadable.
    """
    fields = {"summary": entry.get("summary"), "actor": entry.get("actor")}
    operands = dict(entry.get("operands") or {})
    if redactor is None:
        entry["display"] = {**fields, "operands": operands}
        entry["redacted_entities"] = []
        return []

    impact = impact_level_for(entry.get("classification"))
    masked_fields, found = redactor.redact_mapping(fields, impact_level=impact)
    masked_operands, operand_found = redactor.redact_mapping(operands, impact_level=impact)
    entry["display"] = {**masked_fields, "operands": masked_operands}
    entities = sorted(set(found).union(operand_found))
    entry["redacted_entities"] = entities
    return entities


def build_timeline(session_id: str, conn=None, since: str = None,
                   until: str = None, limit: int = None,
                   redact: bool = True, redactor=None) -> dict:
    """Ordered timeline for one agent session.

    Args:
        session_id: The session to reconstruct. Required; a timeline over "all
            sessions" is a feed, not a case, and the dashboard already has one.
        conn: Optional open connection. When omitted one is opened and closed.
        since / until: Inclusive ISO-8601 bounds on each source's timestamp.
        limit: Cap on rows fetched PER SOURCE, applied in SQL.
        redact: Mask the display projection through ``tools/redaction``. On by
            default — the caller who forgets the flag should get the safe
            behaviour. ``record`` is untouched either way, so turning this off
            changes what a renderer shows, never what a verifier checks.
        redactor: Optional pre-built :class:`TimelineRedactor`, so a caller
            building several timelines pays the detector's setup cost once.

    Returns:
        dict with ``entries``, ``sources``, ``limits``, ``counts``,
        ``redaction`` and ``undated``. Never raises for a session that does not
        exist — that is an empty timeline, which is a finding rather than an
        error.
    """
    if not session_id:
        raise ValueError("session_id is required")
    if redact and redactor is None:
        redactor = TimelineRedactor()
    elif not redact:
        redactor = None

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
            if table == "agent_session_events":
                # Stated whenever the table is present, not only when it had
                # rows: an operator who sees zero events needs to know the
                # payloads were never in scope either way.
                result["limits"].append(PAYLOAD_WITHHELD_NOTE)
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
    result["limits"].extend(_anchor_findings(result["entries"]))

    masked_entities = set()
    for seq, entry in enumerate(result["entries"], start=1):
        entry["seq"] = seq
        masked_entities.update(_apply_display(entry, redactor))

    result["redaction"] = {
        "applied": redactor is not None,
        "stack": "tools/redaction (detector + anonymizer)",
        "entity_types": sorted(masked_entities),
        "scope": "display projection only; entry.record is the verbatim source row",
    }
    if redactor is None:
        result["limits"].append(
            "redaction is OFF: entry.display carries unmasked values and this "
            "timeline must not be disclosed as rendered"
        )

    result["undated"] = sum(1 for e in result["entries"] if e["at"] is None)
    result["counts"] = {
        "entries": len(result["entries"]),
        "sources_joined": sum(1 for s in result["sources"].values() if s.get("present")),
        "findings": sum(1 for e in result["entries"] if e.get("is_finding")),
        "redacted_entries": sum(1 for e in result["entries"] if e.get("redacted_entities")),
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


def canonical_timeline(result: dict) -> dict:
    """The timeline with every run-dependent field removed.

    This is what a bundle manifest should be computed over. ``built_at`` is a
    property of the export, not of the session: leaving it in would give the
    same evidence a different digest every time it was re-exported, and an
    operator comparing two bundles could not tell "re-run" from "tampered".
    """
    canonical = copy.deepcopy(result)
    for key in VOLATILE_KEYS:
        canonical.pop(key, None)
    return canonical


def canonical_json(result: dict) -> str:
    """Canonical JSON for :func:`canonical_timeline` — sorted keys, no spacing."""
    return json.dumps(canonical_timeline(result), sort_keys=True,
                      separators=(",", ":"), default=str)


def timeline_digest(result: dict) -> str:
    """SHA-256 over :func:`canonical_json`. Equal digests mean equal timelines."""
    return hashlib.sha256(canonical_json(result).encode("utf-8")).hexdigest()


def format_timeline(result: dict) -> str:
    """Human-readable timeline, rendered from the REDACTED display projection.

    Deterministic by construction: no field of the run leaks into the text, so
    two renders of the same data are byte-identical. ``built_at`` is therefore
    absent here and available in ``--json``.
    """
    lines = ["CUI // SP-CTI", f"Session: {result['session_id']}"]
    joined = ", ".join(
        f"{t}={s['records']}" for t, s in result["sources"].items() if s.get("present")
    ) or "none"
    lines.append(f"Sources joined: {joined}")
    lines.append(f"Entries: {result['counts']['entries']}")
    redaction = result.get("redaction") or {}
    if redaction.get("applied"):
        entities = ", ".join(redaction.get("entity_types") or []) or "nothing matched"
        lines.append(f"Redaction: ON ({entities})")
    else:
        lines.append("Redaction: OFF — do not disclose as rendered")
    lines.append(f"Digest: {timeline_digest(result)}")
    lines.append("")

    if not result["entries"]:
        lines.append("(no records for this session in any joinable source)")
    for entry in result["entries"]:
        at = entry["at"] or "(undated)"
        display = entry.get("display") or {}
        lines.append(
            f"{entry['seq']:>5}  {at}  {entry['source']}#{entry['record_id']}  "
            f"[{entry['kind']}] {display.get('summary') or ''}".rstrip()
        )
        if display.get("actor"):
            lines.append(f"         actor: {display['actor']}")
        for key in sorted(display.get("operands") or {}):
            lines.append(f"         {key}: {display['operands'][key]}")
        if entry.get("is_finding"):
            cited = entry.get("contributing_event_ids") or []
            lines.append(
                f"         contributing events: {', '.join(cited) if cited else 'none cited'}"
            )
            if entry.get("anchored_to"):
                lines.append(f"         anchored to: {entry['anchored_to']}")
            if entry.get("unresolved_event_ids"):
                lines.append(
                    f"         NOT in this timeline: "
                    f"{', '.join(entry['unresolved_event_ids'])}"
                )

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
    parser.add_argument(
        "--no-redact", action="store_true",
        help="Show unmasked values. The rendered output must not be disclosed.")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    result = build_timeline(args.session, since=args.since, until=args.until,
                           limit=args.limit, redact=not args.no_redact)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_timeline(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
