"""ICDEV™ Studio — event sources, workflow triggers, and event dispatch (dwo-evt-01-d3).

dwo-evt-01 created three tables and nothing used them.  This module is the code
that does:

  ``studio_event_sources``      — where events come from.
  ``studio_workflow_triggers``  — "when an event matching this filter arrives on
                                  source S, start workflow W with these inputs".
  ``studio_trigger_events``     — APPEND-ONLY record of every event evaluated,
                                  matched or not, with the run it started.
                                  This is the answer to "why did this run start".

One of everything
-----------------
There is no second workflow engine (matched triggers call
``workflow_runner.start_run``), no second rules DSL (filters are
``automation_builder`` ``CONDITION_OPERATORS`` conditions, evaluated by
``automation_builder.evaluate_conditions``), and no second event ingress —
:func:`dispatch_event` is a *library* call.  Nothing here opens a socket or
registers a route: external events reach it through the gateway's existing
security chain (dwo-evt-02).

Event shape
-----------
An event is a plain dict::

    {"source_id": "src-…", "event_type": "pr.merged", "payload": {...}}

``dispatch_event`` normalises it so filter conditions and input mappings can
name payload fields directly (``branch``) or fully (``event.payload.branch``).

Input mapping
-------------
``input_mapping_json`` maps run-input keys to event fields::

    {"branch": "event.payload.branch", "env": "staging"}

A source string that names no event field is passed through as a literal, so
constants need no second syntax.  The resolved dict is handed to ``start_run``
as ``inputs=`` (dwo-evt-04-d2), so it is present before the first step runs.

CLI::

    python tools/studio/event_sources.py --list-sources --json
    python tools/studio/event_sources.py --list-triggers --workflow-id wf-… --json
    python tools/studio/event_sources.py --simulate trg-… --json
    python tools/studio/event_sources.py --dispatch src-… --event-type pr.merged \\
        --payload '{"branch":"main"}' --json
"""
# CUI // SP-CTI

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
from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.studio.automation_builder import (  # noqa: E402
    evaluate_conditions,
    resolve_input_mapping,
)

logger = get_logger(__name__)

#: Source kinds accepted by the studio_event_sources CHECK constraint.
SOURCE_KINDS = ("gateway_channel", "canvas_bus", "schedule", "manual")

#: Impact levels in increasing order of sensitivity (dwo-evt-02). An unknown
#: label sorts to 0 — the most permissive rank — so ``classification_allows``
#: below compares only labels it recognises and never *silently* refuses on a
#: typo. The refusal path logs the labels it compared for exactly that reason.
IL_ORDER = {"IL2": 2, "IL4": 4, "IL5": 5, "IL6": 6}

#: Outcomes recorded on a studio_trigger_events row. The audit has to say what
#: happened, not merely that something did.
TRIGGER_OUTCOMES = (
    "no_match",                # event reached no enabled trigger
    "matched",                 # trigger matched; this row is the idempotency claim
    "run_started",             # a run was started (second row, references the claim)
    "refused_classification",  # event IL exceeds the target workflow's IL
    "error",                   # start_run raised
)

#: Outcomes that count as a match for the legacy ``matched`` column.
_MATCHED_OUTCOMES = ("matched", "run_started")


def classification_allows(event_il: str, workflow_il: str) -> bool:
    """True when an event at ``event_il`` may start a workflow rated ``workflow_il``.

    A workflow may only be started by an event at or below its own IL. The
    event's classification is never downgraded to make it fit.
    """
    return IL_ORDER.get(event_il, 0) <= IL_ORDER.get(workflow_il, 0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(raw: Any, default: Any) -> Any:
    """Parse a JSON text column in Python — never with SQL JSON functions."""
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


# ── Event sources ──────────────────────────────────────────


def list_event_sources(*, enabled_only: bool = False) -> list[dict]:
    """Every registered event source, newest first.

    Returns [] rather than raising when the table has not been migrated yet, so
    the editor's Triggers panel renders on a fresh database.
    """
    conn = get_connection()
    try:
        sql = "SELECT * FROM studio_event_sources"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    out = []
    for row in rows:
        src = dict(row)
        src["config"] = _loads(src.get("config_json"), {})
        out.append(src)
    return out


def get_event_source(source_id: str) -> dict | None:
    """Read one ``studio_event_sources`` row, or None if absent/unavailable.

    Returns None rather than raising when the table has not been migrated yet,
    so dispatch and simulation still answer on a fresh database.
    """
    if not source_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_event_sources WHERE source_id = %s",
            (source_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if not row:
        return None
    src = dict(row)
    src["config"] = _loads(src.get("config_json"), {})
    return src


def create_event_source(
    name: str,
    kind: str,
    *,
    config: dict | None = None,
    created_by: str = "studio",
) -> dict:
    """Register a source a trigger can bind to.

    The Triggers panel needs at least one source to bind to; without this the
    panel would be inert on a fresh install.
    """
    name = (name or "").strip()
    if not name:
        return {"status": "error", "error": "name is required"}
    if kind not in SOURCE_KINDS:
        return {"status": "error", "error": f"kind must be one of {', '.join(SOURCE_KINDS)}"}

    source_id = f"src-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_event_sources "
            "(source_id, name, kind, config_json, enabled, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, 1, %s, %s)",
            (source_id, name, kind, json.dumps(config or {}), created_by, _now_iso()),
        )
        conn.commit()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()

    if kind == "canvas_bus":
        # Wire it onto the canvas event bus now.  Without this a source created
        # from the Triggers panel would stay inert until the next restart.
        try:
            from tools.studio.bus_subscriber import register_source  # noqa: PLC0415

            register_source({
                "source_id": source_id, "kind": kind,
                "enabled": 1, "config": config or {}, "name": name,
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not subscribe canvas_bus source %s: %s", source_id, exc)

    return {"status": "ok", "source_id": source_id, "name": name, "kind": kind}


# ── Workflow triggers ──────────────────────────────────────


def _row_to_trigger(row) -> dict:
    trig = dict(row)
    trig["filter"] = _loads(trig.get("filter_json"), [])
    trig["input_mapping"] = _loads(trig.get("input_mapping_json"), {})
    trig["enabled"] = bool(trig.get("enabled", 1))
    # dwo-evt-02 columns. Defaulted here rather than assumed present, so a
    # database that has not run migration 308 still lists triggers — and
    # defaults to the *most restrictive* posture: IL6 admits any event IL, so
    # an unmigrated row is not accidentally treated as refusing everything.
    trig.setdefault("workflow_il", "IL6")
    trig.setdefault("project_id", "default")
    if not trig.get("workflow_il"):
        trig["workflow_il"] = "IL6"
    if not trig.get("project_id"):
        trig["project_id"] = "default"
    return trig


def list_workflow_triggers(
    workflow_id: str | None = None,
    *,
    source_id: str | None = None,
    enabled_only: bool = False,
) -> list[dict]:
    """Triggers bound to a workflow (or to a source), newest first."""
    clauses, params = [], []
    if workflow_id:
        clauses.append("workflow_id = %s")
        params.append(workflow_id)
    if source_id:
        clauses.append("source_id = %s")
        params.append(source_id)
    if enabled_only:
        clauses.append("enabled = 1")

    sql = "SELECT * FROM studio_workflow_triggers"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"

    conn = get_connection()
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [_row_to_trigger(r) for r in rows]


def get_workflow_trigger(trigger_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_workflow_triggers WHERE trigger_id = %s",
            (trigger_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    return _row_to_trigger(row) if row else None


def create_workflow_trigger(
    workflow_id: str,
    source_id: str,
    *,
    event_type: str = "",
    event_filter: list | None = None,
    input_mapping: dict | None = None,
) -> dict:
    """Bind an event source + filter + input mapping to a workflow.

    ``event_filter`` is a list of ``{"field", "operator", "value"}`` conditions
    using ``automation_builder`` operators — the same vocabulary automations
    use.  ``input_mapping`` maps run-input keys to event fields.
    """
    if not workflow_id:
        return {"status": "error", "error": "workflow_id is required"}
    if not source_id:
        return {"status": "error", "error": "source_id is required"}
    if not get_event_source(source_id):
        return {"status": "error", "error": f"Unknown event source: {source_id}"}

    trigger_id = f"trg-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflow_triggers "
            "(trigger_id, source_id, workflow_id, event_type, filter_json, "
            " input_mapping_json, enabled, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 1, %s)",
            (
                trigger_id, source_id, workflow_id, event_type or "",
                json.dumps(event_filter or []),
                json.dumps(input_mapping or {}),
                _now_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()
    return {"status": "ok", "trigger_id": trigger_id, "workflow_id": workflow_id}


def update_workflow_trigger(
    trigger_id: str,
    *,
    event_type: str | None = None,
    event_filter: list | None = None,
    input_mapping: dict | None = None,
) -> dict:
    """Change a trigger's event type, filter or input mapping."""
    if not get_workflow_trigger(trigger_id):
        return {"status": "error", "error": "Trigger not found"}

    sets, params = [], []
    if event_type is not None:
        sets.append("event_type = %s")
        params.append(event_type)
    if event_filter is not None:
        sets.append("filter_json = %s")
        params.append(json.dumps(event_filter))
    if input_mapping is not None:
        sets.append("input_mapping_json = %s")
        params.append(json.dumps(input_mapping))
    if not sets:
        return {"status": "ok", "trigger_id": trigger_id, "updated": False}

    params.append(trigger_id)
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE studio_workflow_triggers SET {', '.join(sets)} WHERE trigger_id = %s",
            tuple(params),
        )
        conn.commit()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()
    return {"status": "ok", "trigger_id": trigger_id, "updated": True}


def toggle_workflow_trigger(trigger_id: str, enabled: bool) -> dict:
    """Enable or disable a trigger without deleting its history."""
    if not get_workflow_trigger(trigger_id):
        return {"status": "error", "error": "Trigger not found"}
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE studio_workflow_triggers SET enabled = %s WHERE trigger_id = %s",
            (1 if enabled else 0, trigger_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "trigger_id": trigger_id, "enabled": bool(enabled)}


def delete_workflow_trigger(trigger_id: str) -> dict:
    """Remove a trigger.  Its ``studio_trigger_events`` rows are append-only and
    stay behind, so past runs remain explainable."""
    if not get_workflow_trigger(trigger_id):
        return {"status": "error", "error": "Trigger not found"}
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM studio_workflow_triggers WHERE trigger_id = %s", (trigger_id,)
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "trigger_id": trigger_id, "deleted": True}


# ── Matching ───────────────────────────────────────────────


def normalize_event(source_id: str, event_type: str = "", payload: dict | None = None) -> dict:
    """Build the event dict filters and mappings are evaluated against.

    Payload keys are also lifted to the top level (without shadowing the
    envelope) so a condition can name ``branch`` as well as
    ``event.payload.branch``.  Mirrors ``automation_builder._sample_event``.
    """
    payload = payload if isinstance(payload, dict) else {}
    event: dict[str, Any] = {
        "type": "external_event",
        "source_id": source_id or "",
        "event_type": event_type or "",
        "payload": payload,
    }
    for key, value in payload.items():
        event.setdefault(key, value)
    return event


def match_event(event: dict) -> list[dict]:
    """Evaluate every enabled trigger on the event's source.

    Returns one entry per candidate trigger::

        {"trigger": {...}, "matched": bool, "reason": str,
         "condition_results": [...], "inputs": {...}}

    A non-match always carries the reason it did not fire — a trigger that
    silently never fires is undiagnosable.  Named ``match_event`` because that
    is the entry point dwo-evt-02's gateway path calls.
    """
    source_id = event.get("source_id") or ""
    event_type = event.get("event_type") or ""
    results = []

    for trigger in list_workflow_triggers(source_id=source_id, enabled_only=True):
        condition_results = evaluate_conditions(trigger.get("filter") or [], event)
        want_type = (trigger.get("event_type") or "").strip()

        if want_type and want_type != event_type:
            matched, reason = False, (
                f"event_type '{event_type or '(none)'}' does not match '{want_type}'"
            )
        elif not all(c["met"] for c in condition_results):
            failed = [c["field"] for c in condition_results if not c["met"]]
            matched, reason = False, f"filter conditions not met: {', '.join(failed)}"
        else:
            matched, reason = True, "source, event_type and filter matched"

        results.append({
            "trigger": trigger,
            "matched": matched,
            "reason": reason,
            "condition_results": condition_results,
            "inputs": resolve_input_mapping(trigger.get("input_mapping"), event) if matched else {},
        })
    return results


# ── Append-only audit ──────────────────────────────────────


def _is_unique_violation(exc: Exception) -> bool:
    """True when the exception is a duplicate-key error on either backend."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return "unique" in text or "duplicate key" in text


def _is_missing_column(exc: Exception) -> bool:
    """True when the failure is an absent column on either backend.

    SQLite phrases this differently per statement: a SELECT says "no such
    column: x" but an INSERT says "table T has no column named x" — matching
    only the first form silently drops every audit row on an unmigrated
    database. PostgreSQL raises UndefinedColumn / "column ... does not exist".
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return (
        "no such column" in text
        or "has no column named" in text
        or "undefinedcolumn" in text
        or "does not exist" in text
    )


def log_trigger_event(
    source_id: str,
    trigger_id: str | None,
    event_type: str,
    payload: dict,
    *,
    matched: bool,
    run_id: str | None = None,
    reason: str = "",
    outcome: str = "",
    workflow_id: str | None = None,
    classification: str = "",
    idempotency_key: str | None = None,
    envelope_id: str = "",
) -> str | None:
    """Record one evaluated event.  APPEND-ONLY — never updated or deleted.

    Events that matched nothing are logged too (``matched = 0``, ``run_id``
    NULL), so "my trigger never fires" is a question the board can answer.

    ``idempotency_key`` (dwo-evt-02) makes this INSERT the replay guard: the
    column carries a UNIQUE index, so a webhook retry loses the insert and this
    returns **None**, and the caller MUST NOT start a run in that case. That is
    why the claim row is written *before* ``start_run`` — a SELECT-then-INSERT
    check would let two concurrent deliveries both pass.

    Returns the event_id, ``None`` on a lost idempotency race, or ``""`` when
    the row could not be written at all (an unmigrated table must not break
    ingest).
    """
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_trigger_events "
            "(event_id, source_id, trigger_id, event_type, payload_json, "
            " matched, run_id, reason, outcome, workflow_id, classification, "
            " idempotency_key, envelope_id, received_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                event_id, source_id or None, trigger_id, event_type or "",
                json.dumps(payload or {}, default=str),
                1 if matched else 0, run_id, reason,
                outcome or ("matched" if matched else "no_match"),
                workflow_id, classification, idempotency_key, envelope_id,
                _now_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:  # noqa: S110 - rollback failure must not mask the cause
            pass
        if idempotency_key and _is_unique_violation(exc):
            logger.info(
                "trigger event: duplicate delivery, key=%s — no run will start",
                idempotency_key,
            )
            return None
        if _is_missing_column(exc):
            # Database has not run migration 308. Fall back to the pre-dispatch
            # column set rather than losing the audit row entirely — an
            # unmigrated deployment must keep answering "why did this run
            # start", even if it cannot answer "at what classification".
            if idempotency_key:
                logger.warning(
                    "trigger event: idempotency_key column missing — replay "
                    "protection is NOT active until migration 308 runs. "
                    "A retried delivery can start a second run.",
                )
            try:
                conn.execute(
                    "INSERT INTO studio_trigger_events "
                    "(event_id, source_id, trigger_id, event_type, payload_json, "
                    " matched, run_id, reason, received_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        event_id, source_id or None, trigger_id, event_type or "",
                        json.dumps(payload or {}, default=str),
                        1 if matched else 0, run_id, reason, _now_iso(),
                    ),
                )
                conn.commit()
                return event_id
            except Exception as legacy_exc:
                logger.warning(
                    "Could not record trigger event for source %s: %s", source_id, legacy_exc
                )
                return ""
        logger.warning("Could not record trigger event for source %s: %s", source_id, exc)
        return ""
    finally:
        conn.close()
    return event_id


def trigger_event_for_run(run_id: str) -> dict | None:
    """The trigger event that started ``run_id``, or None for a manual run.

    Backs the run-detail badge: a run either points at the event that started
    it or it was started by a person.
    """
    if not run_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_trigger_events WHERE run_id = %s "
            "ORDER BY received_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if not row:
        return None
    evt = dict(row)
    evt["payload"] = _loads(evt.get("payload_json"), {})
    evt["matched"] = bool(evt.get("matched"))
    source = get_event_source(evt.get("source_id") or "")
    if source:
        evt["source_name"] = source.get("name")
        evt["source_kind"] = source.get("kind")
    return evt


def list_trigger_events(
    *,
    trigger_id: str | None = None,
    source_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Recent evaluated events, newest first — the "why did (not) this fire" log."""
    clauses, params = [], []
    if trigger_id:
        clauses.append("trigger_id = %s")
        params.append(trigger_id)
    if source_id:
        clauses.append("source_id = %s")
        params.append(source_id)
    sql = "SELECT * FROM studio_trigger_events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY received_at DESC LIMIT %s"
    params.append(int(limit))

    conn = get_connection()
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    out = []
    for row in rows:
        evt = dict(row)
        evt["payload"] = _loads(evt.get("payload_json"), {})
        evt["matched"] = bool(evt.get("matched"))
        out.append(evt)
    return out


# ── Dispatch & simulate ────────────────────────────────────


def dispatch_event(
    source_id: str,
    event_type: str = "",
    payload: dict | None = None,
    *,
    project_id: str = "default",
) -> dict:
    """Evaluate an event against its source's triggers and start matching runs.

    This is a library call, not a route.  External events reach it only after
    the gateway's security chain has cleared them (dwo-evt-02); adding an
    unauthenticated ingress here would bypass those gates.

    Every candidate trigger is logged to ``studio_trigger_events`` whether it
    matched or not.  A source with no triggers at all is logged once, so an
    event that went nowhere is still visible.
    """
    event = normalize_event(source_id, event_type, payload)
    matches = match_event(event)

    if not matches:
        log_trigger_event(
            source_id, None, event_type, event["payload"],
            matched=False, reason="no enabled trigger is bound to this source",
        )
        return {"status": "ok", "source_id": source_id, "matched": 0, "runs": [], "results": []}

    from tools.studio.workflow_runner import start_run  # noqa: PLC0415

    results, runs = [], []
    for match in matches:
        trigger = match["trigger"]
        trigger_id = trigger["trigger_id"]
        if not match["matched"]:
            event_id = log_trigger_event(
                source_id, trigger_id, event_type, event["payload"],
                matched=False, reason=match["reason"],
            )
            results.append({
                "trigger_id": trigger_id, "matched": False,
                "reason": match["reason"], "event_id": event_id,
            })
            continue

        try:
            # dwo-evt-04-d2 gave start_run an ``inputs=`` parameter, so the
            # mapped inputs go in with the run rather than being seeded into
            # run memory immediately afterwards. The old seeding raced the
            # worker thread: a first step reading a trigger input could run
            # before the seed landed. Passing them here closes that race.
            run_id = start_run(
                trigger["workflow_id"], project_id, inputs=match["inputs"] or {}
            )
        except Exception as exc:
            event_id = log_trigger_event(
                source_id, trigger_id, event_type, event["payload"],
                matched=True, reason=f"run could not be started: {exc}",
            )
            results.append({
                "trigger_id": trigger_id, "matched": True, "error": str(exc),
                "event_id": event_id,
            })
            continue

        event_id = log_trigger_event(
            source_id, trigger_id, event_type, event["payload"],
            matched=True, run_id=run_id, reason=match["reason"],
        )
        runs.append(run_id)
        results.append({
            "trigger_id": trigger_id, "matched": True, "run_id": run_id,
            "workflow_id": trigger["workflow_id"], "inputs": match["inputs"],
            "event_id": event_id,
        })

    return {
        "status": "ok",
        "source_id": source_id,
        "event_type": event_type,
        "matched": len(runs),
        "runs": runs,
        "results": results,
    }


def simulate_workflow_trigger(trigger_id: str, test_event: dict | None = None) -> dict:
    """Dry-run one trigger: would it fire, and with what inputs?

    Starts nothing and logs nothing.  With no ``test_event`` the source's
    registered ``sample_payload`` is used; filter fields are never back-filled —
    a simulation that invented values to make its own conditions pass would be
    worthless.
    """
    trigger = get_workflow_trigger(trigger_id)
    if not trigger:
        return {"status": "error", "error": "Trigger not found"}

    if test_event is None:
        source = get_event_source(trigger["source_id"]) or {}
        sample = source.get("config", {}).get("sample_payload")
        event = normalize_event(
            trigger["source_id"],
            trigger.get("event_type") or "",
            sample if isinstance(sample, dict) else {},
        )
        event["test"] = True
    else:
        event = normalize_event(
            test_event.get("source_id") or trigger["source_id"],
            test_event.get("event_type", trigger.get("event_type") or ""),
            test_event.get("payload") if isinstance(test_event.get("payload"), dict) else test_event,
        )

    condition_results = evaluate_conditions(trigger.get("filter") or [], event)
    want_type = (trigger.get("event_type") or "").strip()
    type_ok = (not want_type) or want_type == (event.get("event_type") or "")
    would_fire = bool(trigger["enabled"]) and type_ok and all(c["met"] for c in condition_results)

    if not trigger["enabled"]:
        reason = "trigger is disabled"
    elif not type_ok:
        reason = f"event_type '{event.get('event_type') or '(none)'}' does not match '{want_type}'"
    elif not all(c["met"] for c in condition_results):
        failed = [c["field"] for c in condition_results if not c["met"]]
        reason = f"filter conditions not met: {', '.join(failed)}"
    else:
        reason = "source, event_type and filter matched"

    return {
        "status": "ok",
        "trigger_id": trigger_id,
        "workflow_id": trigger["workflow_id"],
        "would_fire": would_fire,
        "reason": reason,
        "sample_event": event,
        "condition_results": condition_results,
        "resolved_inputs": resolve_input_mapping(trigger.get("input_mapping"), event),
        "dry_run": True,
    }


# ── CLI ────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ICDEV™ Studio event sources and workflow triggers"
    )
    parser.add_argument("--list-sources", action="store_true", help="List event sources")
    parser.add_argument("--list-triggers", action="store_true", help="List workflow triggers")
    parser.add_argument("--workflow-id", default="", help="Filter triggers by workflow")
    parser.add_argument("--simulate", metavar="TRIGGER_ID", help="Dry-run a trigger")
    parser.add_argument("--dispatch", metavar="SOURCE_ID", help="Dispatch an event for real")
    parser.add_argument("--event-type", default="", help="Event type for --dispatch")
    parser.add_argument("--payload", default="{}", help="JSON payload for --dispatch")
    parser.add_argument("--project-id", default="default", help="Project for started runs")
    parser.add_argument("--events", action="store_true", help="Recent evaluated events")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    if args.list_sources:
        result: Any = {"sources": list_event_sources()}
    elif args.list_triggers:
        result = {"triggers": list_workflow_triggers(args.workflow_id or None)}
    elif args.simulate:
        result = simulate_workflow_trigger(args.simulate)
    elif args.dispatch:
        try:
            payload = json.loads(args.payload)
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": f"--payload is not JSON: {exc}"}))
            return 2
        result = dispatch_event(
            args.dispatch, args.event_type, payload, project_id=args.project_id
        )
    elif args.events:
        result = {"events": list_trigger_events()}
    else:
        parser.print_help()
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
