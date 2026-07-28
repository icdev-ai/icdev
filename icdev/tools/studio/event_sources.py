#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Studio — Event source and workflow trigger registry (dwo-evt-01/02).

Binds external events to workflow runs. An *event source* says where events
legitimately come from; a *trigger* says which workflow an event starts and
under what filter.

Tables come from migration 304 (dwo-evt-01); migration 305 adds the columns
dispatch needs — the two IL ceilings and the UNIQUE ``idempotency_key``.

The filter language is deliberately not new: it is
``automation_builder.CONDITION_OPERATORS``, evaluated by
``automation_builder._evaluate_condition``. There is one condition DSL in
Studio and this module reuses it rather than growing a second one.

A filter is a list of ``{"field": "...", "operator": "...", "value": "..."}``
dicts, ALL of which must pass (AND). ``field`` is a dotted path into the event
payload, so ``repository.full_name`` reads ``payload["repository"]["full_name"]``.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.studio.automation_builder import _evaluate_condition  # noqa: E402

SOURCE_KINDS = ("gateway_channel", "canvas_bus", "schedule", "manual")

# Every value ``outcome`` may take. The audit trail records events that started
# nothing as deliberately as those that did — "why did this run NOT start" has
# to be answerable too.
OUTCOMES = ("no_match", "matched", "run_started", "refused_classification", "error")

# Outcomes that set the legacy ``matched`` flag from migration 304. A refusal
# matched a trigger but is not a match for dispatch purposes: nothing ran.
MATCHED_OUTCOMES = ("matched", "run_started")

# Higher number = more sensitive. Mirrors tools/gateway/response_filter.IL_ORDER;
# imported rather than redefined so the two can never drift.
try:  # pragma: no cover - import shape only
    from tools.gateway.response_filter import IL_ORDER
except ImportError:  # pragma: no cover
    IL_ORDER = {"IL2": 0, "IL4": 1, "IL5": 2, "IL6": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Registry CRUD ──────────────────────────────────────────────────────


def create_event_source(
    name: str,
    kind: str = "gateway_channel",
    config: dict | None = None,
    max_il: str = "IL2",
    created_by: str = "system",
    source_id: str | None = None,
) -> str:
    """Register an event source. Returns its source_id."""
    if kind not in SOURCE_KINDS:
        raise ValueError(f"Unknown source kind: {kind} (expected one of {SOURCE_KINDS})")

    sid = source_id or f"src-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_event_sources
                   (source_id, name, kind, config_json, max_il, enabled, created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, 1, %s, %s)""",
            (sid, name, kind, json.dumps(config or {}), max_il, created_by, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


def create_trigger(
    source_id: str,
    workflow_id: str,
    event_type: str = "*",
    filters: list[dict] | None = None,
    input_mapping: dict | None = None,
    workflow_il: str = "IL6",
    project_id: str = "default",
    trigger_id: str | None = None,
) -> str:
    """Bind a workflow to an event source. Returns the trigger_id."""
    tid = trigger_id or f"trg-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_workflow_triggers
                   (trigger_id, source_id, workflow_id, event_type, filter_json,
                    input_mapping_json, workflow_il, project_id, enabled, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s)""",
            (
                tid,
                source_id,
                workflow_id,
                event_type,
                json.dumps(filters or []),
                json.dumps(input_mapping or {}),
                workflow_il,
                project_id,
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return tid


def get_source_by_name(name: str) -> dict | None:
    """Look up an enabled source by its name (e.g. a gateway channel name)."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT source_id, name, kind, config_json, max_il, enabled
                   FROM studio_event_sources WHERE name = %s AND enabled = 1""",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "source_id": row[0],
        "name": row[1],
        "kind": row[2],
        "config": json.loads(row[3] or "{}"),
        "max_il": row[4],
        "enabled": bool(row[5]),
    }


def list_triggers(source_id: str) -> list[dict]:
    """All enabled triggers bound to a source."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT trigger_id, source_id, workflow_id, event_type, filter_json,
                      input_mapping_json, workflow_il, project_id
                   FROM studio_workflow_triggers
                   WHERE source_id = %s AND enabled = 1""",
            (source_id,),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "trigger_id": r[0],
            "source_id": r[1],
            "workflow_id": r[2],
            "event_type": r[3],
            "filters": json.loads(r[4] or "[]"),
            "input_mapping": json.loads(r[5] or "{}"),
            "workflow_il": r[6],
            "project_id": r[7],
        }
        for r in rows
    ]


# ── Matching ───────────────────────────────────────────────────────────


def resolve_field(payload: dict, field: str) -> Any:
    """Read a dotted path out of a payload. Missing path -> None."""
    current: Any = payload
    for part in str(field).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def evaluate_filters(payload: dict, filters: list[dict]) -> bool:
    """All conditions must pass. An empty filter list matches everything."""
    for cond in filters or []:
        actual = resolve_field(payload, cond.get("field", ""))
        if not _evaluate_condition(actual, cond.get("operator", "equals"), str(cond.get("value", ""))):
            return False
    return True


def match_event(source_name: str, event_type: str, payload: dict) -> list[dict]:
    """Return every enabled trigger on ``source_name`` this event satisfies.

    An unregistered or disabled source matches nothing — an event arriving from
    somewhere nobody declared must not start a workflow.
    """
    source = get_source_by_name(source_name)
    if not source:
        return []

    matched: list[dict] = []
    for trigger in list_triggers(source["source_id"]):
        if trigger["event_type"] not in ("*", event_type):
            continue
        if not evaluate_filters(payload, trigger["filters"]):
            continue
        trigger["source"] = source
        matched.append(trigger)
    return matched


# ── Audit trail ────────────────────────────────────────────────────────


def record_trigger_event(
    outcome: str,
    *,
    source_id: str | None = None,
    trigger_id: str | None = None,
    workflow_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    reason: str = "",
    classification: str = "",
    idempotency_key: str | None = None,
    payload: dict | None = None,
    envelope_id: str = "",
) -> str | None:
    """Append one row to the append-only trigger audit trail.

    Returns the event_id, or None when the row lost the idempotency race — i.e.
    this delivery id was already recorded, so the caller must NOT start a run.
    """
    eid = f"evt-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_trigger_events
                   (event_id, source_id, trigger_id, workflow_id, run_id, event_type,
                    outcome, matched, reason, classification, idempotency_key,
                    payload_json, envelope_id, received_at, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                eid,
                source_id,
                trigger_id,
                workflow_id,
                run_id,
                event_type,
                outcome,
                1 if outcome in MATCHED_OUTCOMES else 0,
                reason,
                classification,
                idempotency_key,
                json.dumps(payload or {})[:100_000],
                envelope_id,
                _now(),
                _now(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - unique-violation shape differs per backend
        conn.rollback()
        if idempotency_key and _is_unique_violation(exc):
            return None
        raise
    finally:
        conn.close()
    return eid


def _is_unique_violation(exc: Exception) -> bool:
    """True when the exception is a duplicate-key error on either backend."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return "unique" in text or "duplicate key" in text


def classification_allows(event_il: str, workflow_il: str) -> bool:
    """A workflow may only be started by an event at or below its own IL."""
    return IL_ORDER.get(event_il, 0) <= IL_ORDER.get(workflow_il, 0)


# ── CLI ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Studio event source registry")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p_src = sub.add_parser("add-source", help="Register an event source")
    p_src.add_argument("name")
    p_src.add_argument("--kind", default="gateway_channel", choices=SOURCE_KINDS)
    p_src.add_argument("--max-il", default="IL2")

    p_trg = sub.add_parser("add-trigger", help="Bind a workflow to a source")
    p_trg.add_argument("source_id")
    p_trg.add_argument("workflow_id")
    p_trg.add_argument("--event-type", default="*")
    p_trg.add_argument("--workflow-il", default="IL6")

    p_list = sub.add_parser("triggers", help="List triggers for a source")
    p_list.add_argument("source_id")

    args = parser.parse_args()
    result: Any

    if args.command == "add-source":
        result = {"source_id": create_event_source(args.name, args.kind, max_il=args.max_il)}
    elif args.command == "add-trigger":
        result = {
            "trigger_id": create_trigger(
                args.source_id,
                args.workflow_id,
                event_type=args.event_type,
                workflow_il=args.workflow_il,
            )
        }
    elif args.command == "triggers":
        result = list_triggers(args.source_id)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2) if args.json else result)


if __name__ == "__main__":
    main()
