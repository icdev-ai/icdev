"""ICDEV™ Studio — Citizen Automation Builder.

Visual trigger/condition/action rule engine for non-technical users.
Event-sourced execution with full replay for compliance audit (D365).

Rule structure:
  TRIGGER (event occurs) → CONDITION (filter) → ACTION (execute)

Example:
  "When a critical STIG finding is detected AND framework is FedRAMP,
   create a case, assign to ISSO, and send alert."
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "auto") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── Trigger, Condition, Action definitions ────────────────────────────

TRIGGER_TYPES: list[dict[str, Any]] = [
    {
        "id": "finding_detected",
        "label": "Finding Detected",
        "description": "A compliance or security finding is created",
        "icon": "!",
        "color": "#ef4444",
        "category": "compliance",
    },
    {
        "id": "sla_breach",
        "label": "SLA Breach",
        "description": "A case exceeds its SLA time limit",
        "icon": "T",
        "color": "#f59e0b",
        "category": "cases",
    },
    {
        "id": "scan_complete",
        "label": "Scan Complete",
        "description": "A security or compliance scan finishes",
        "icon": "S",
        "color": "#3b82f6",
        "category": "security",
    },
    {
        "id": "deployment",
        "label": "Deployment Event",
        "description": "A deployment starts, succeeds, or fails",
        "icon": "D",
        "color": "#8b5cf6",
        "category": "operations",
    },
    {
        "id": "form_submitted",
        "label": "Form Submitted",
        "description": "A Studio form receives a submission",
        "icon": "F",
        "color": "#22c55e",
        "category": "studio",
    },
    {
        "id": "case_state_change",
        "label": "Case State Change",
        "description": "A case transitions to a new state",
        "icon": "C",
        "color": "#6366f1",
        "category": "cases",
    },
    {
        "id": "poam_overdue",
        "label": "POA&M Overdue",
        "description": "A POA&M item passes its due date",
        "icon": "P",
        "color": "#ef4444",
        "category": "compliance",
    },
    {
        "id": "sam_opportunity",
        "label": "SAM.gov Opportunity",
        "description": "A new SAM.gov opportunity matches criteria",
        "icon": "G",
        "color": "#f97316",
        "category": "govcon",
    },
    {
        "id": "schedule",
        "label": "Scheduled",
        "description": "Run on a recurring schedule (daily, weekly, etc.)",
        "icon": "R",
        "color": "#64748b",
        "category": "system",
    },
    {
        "id": "manual",
        "label": "Manual Trigger",
        "description": "Triggered manually by a user",
        "icon": "M",
        "color": "#94a3b8",
        "category": "system",
    },
    {
        "id": "external_event",
        "label": "External Event",
        "description": "An event arrives on a registered Studio event source",
        "icon": "E",
        "color": "#0ea5e9",
        "category": "system",
        # Config names a studio_event_sources row (dwo-evt-01).  Rendered
        # generically by the builder UI from these field descriptors.
        "config_fields": [
            {"name": "source_id", "label": "Event source ID", "placeholder": "src-…", "required": True},
            {"name": "event_type", "label": "Event type (blank = any)", "placeholder": "push", "required": False},
        ],
    },
]

CONDITION_OPERATORS: list[dict[str, str]] = [
    {"id": "equals", "label": "equals", "symbol": "="},
    {"id": "not_equals", "label": "not equals", "symbol": "!="},
    {"id": "contains", "label": "contains", "symbol": "~"},
    {"id": "greater_than", "label": "greater than", "symbol": ">"},
    {"id": "less_than", "label": "less than", "symbol": "<"},
    {"id": "in_list", "label": "is one of", "symbol": "in"},
    {"id": "is_empty", "label": "is empty", "symbol": "null"},
    {"id": "is_not_empty", "label": "is not empty", "symbol": "!null"},
]

ACTION_TYPES: list[dict[str, Any]] = [
    {
        "id": "create_case",
        "label": "Create Case",
        "description": "Create a new case in a case type",
        "icon": "C",
        "color": "#6366f1",
        "category": "cases",
        "params": ["case_type_id", "title_template", "priority"],
    },
    {
        "id": "transition_case",
        "label": "Transition Case",
        "description": "Move a case to a new state",
        "icon": "T",
        "color": "#8b5cf6",
        "category": "cases",
        "params": ["to_state"],
    },
    {
        "id": "send_notification",
        "label": "Send Notification",
        "description": "Send email, Slack, or dashboard notification",
        "icon": "N",
        "color": "#3b82f6",
        "category": "notify",
        "params": ["channel", "recipient", "message_template"],
    },
    {
        "id": "run_tool",
        "label": "Run Tool",
        "description": "Execute an ICDEV™ tool",
        "icon": "R",
        "color": "#22c55e",
        "category": "execute",
        "params": ["tool_path", "args"],
    },
    {
        "id": "run_workflow",
        "label": "Run Workflow",
        "description": "Start a saved Studio workflow run",
        "icon": "W",
        "color": "#f59e0b",
        "category": "execute",
        # input_mapping: {"memory_key": "event.field"} — event fields copied
        # into the run's memory store so workflow steps can read them.
        "params": ["workflow_id", "input_mapping", "project_id"],
    },
    {
        "id": "update_field",
        "label": "Update Field",
        "description": "Set a field value on the triggering entity",
        "icon": "U",
        "color": "#64748b",
        "category": "data",
        "params": ["field_name", "value"],
    },
    {
        "id": "block_gate",
        "label": "Block Gate",
        "description": "Block a deployment or merge gate",
        "icon": "B",
        "color": "#ef4444",
        "category": "security",
        "params": ["gate_name", "reason"],
    },
    {
        "id": "log_event",
        "label": "Log Event",
        "description": "Write to audit trail",
        "icon": "L",
        "color": "#94a3b8",
        "category": "audit",
        "params": ["event_type", "message"],
    },
]

# ── Pre-built automation templates ────────────────────────────────────

AUTOMATION_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "tpl-critical-finding-response",
        "name": "Critical Finding Auto-Response",
        "description": "When a critical STIG finding is detected, create a case, assign to ISSO, and log event",
        "trigger": {"type": "finding_detected", "filters": {"severity": "critical"}},
        "conditions": [{"field": "framework", "operator": "in_list", "value": "FedRAMP,CMMC,NIST"}],
        "actions": [
            {
                "type": "create_case",
                "config": {"title_template": "Critical Finding: {{finding_id}}", "priority": "critical"},
            },
            {
                "type": "send_notification",
                "config": {
                    "channel": "dashboard",
                    "recipient": "isso",
                    "message_template": "Critical finding detected: {{finding_id}}",
                },
            },
            {
                "type": "log_event",
                "config": {"event_type": "auto_response", "message": "Auto-created case for critical finding"},
            },
        ],
    },
    {
        "id": "tpl-poam-escalation",
        "name": "POA&M Overdue Escalation",
        "description": "When a POA&M item is overdue, escalate to PM and block deployment gate",
        "trigger": {"type": "poam_overdue"},
        "conditions": [{"field": "days_overdue", "operator": "greater_than", "value": "0"}],
        "actions": [
            {
                "type": "send_notification",
                "config": {
                    "channel": "dashboard",
                    "recipient": "pm",
                    "message_template": "POA&M item overdue: {{poam_id}}",
                },
            },
            {"type": "block_gate", "config": {"gate_name": "deployment", "reason": "Overdue POA&M item"}},
        ],
    },
    {
        "id": "tpl-scan-to-report",
        "name": "Scan Complete → Generate Report",
        "description": "When a compliance scan completes, generate a report and notify stakeholders",
        "trigger": {"type": "scan_complete"},
        "conditions": [],
        "actions": [
            {
                "type": "run_tool",
                "config": {"tool_path": "tools/compliance/multi_regime_assessor.py", "args": "--json"},
            },
            {
                "type": "send_notification",
                "config": {
                    "channel": "dashboard",
                    "recipient": "team",
                    "message_template": "Scan complete — report generated",
                },
            },
        ],
    },
    {
        "id": "tpl-form-to-case",
        "name": "Form Submission → Create Case",
        "description": "When a form is submitted, automatically create a tracking case",
        "trigger": {"type": "form_submitted"},
        "conditions": [],
        "actions": [
            {
                "type": "create_case",
                "config": {"title_template": "Form: {{form_name}} by {{submitted_by}}", "priority": "medium"},
            },
        ],
    },
    {
        "id": "tpl-sam-opportunity-capture",
        "name": "SAM.gov Opportunity Alert",
        "description": "When a matching SAM.gov opportunity appears, notify BD team and create proposal draft",
        "trigger": {"type": "sam_opportunity"},
        "conditions": [{"field": "naics_match", "operator": "equals", "value": "true"}],
        "actions": [
            {
                "type": "send_notification",
                "config": {
                    "channel": "dashboard",
                    "recipient": "bd_team",
                    "message_template": "New opportunity: {{title}} — {{agency}}",
                },
            },
            {
                "type": "log_event",
                "config": {"event_type": "opportunity_captured", "message": "SAM.gov opportunity matched"},
            },
        ],
    },
]


def get_trigger_types() -> list[dict]:
    return TRIGGER_TYPES


def get_condition_operators() -> list[dict]:
    return CONDITION_OPERATORS


def get_action_types() -> list[dict]:
    return ACTION_TYPES


def get_automation_templates() -> list[dict]:
    return AUTOMATION_TEMPLATES


# ── Automation CRUD ───────────────────────────────────────────────────


def create_automation(
    name: str,
    trigger: dict,
    actions: list[dict],
    *,
    description: str = "",
    conditions: list[dict] | None = None,
    created_by: str = "studio",
) -> dict:
    auto_id = _new_id("auto")

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_automations
               (automation_id, name, description, trigger_json, condition_json,
                action_json, enabled, created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s)""",
            (
                auto_id,
                name,
                description,
                json.dumps(trigger),
                json.dumps(conditions or []),
                json.dumps(actions),
                created_by,
                _now_iso(),
            ),
        )
        conn.commit()
        return {"status": "ok", "automation_id": auto_id}
    finally:
        conn.close()


def list_automations(*, enabled_only: bool = False) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM studio_automations"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["trigger"] = json.loads(d.get("trigger_json", "{}"))
            d["conditions"] = json.loads(d.get("condition_json", "[]"))
            d["actions"] = json.loads(d.get("action_json", "[]"))
            result.append(d)
        return result
    finally:
        conn.close()


def get_automation(auto_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_automations WHERE automation_id = %s",
            (auto_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["trigger"] = json.loads(d.get("trigger_json", "{}"))
        d["conditions"] = json.loads(d.get("condition_json", "[]"))
        d["actions"] = json.loads(d.get("action_json", "[]"))
        return d
    finally:
        conn.close()


def toggle_automation(auto_id: str, enabled: bool) -> dict:
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE studio_automations SET enabled = %s WHERE automation_id = %s",
            (1 if enabled else 0, auto_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"status": "error", "error": "Not found"}
        return {"status": "ok", "automation_id": auto_id, "enabled": enabled}
    finally:
        conn.close()


def delete_automation(auto_id: str) -> dict:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM studio_automations WHERE automation_id = %s", (auto_id,))
        conn.commit()
        return {"status": "ok"} if cur.rowcount else {"status": "error", "error": "Not found"}
    finally:
        conn.close()


# ── Execution log (append-only) ───────────────────────────────────────


def log_automation_run(
    automation_id: str,
    trigger_event: str,
    status: str,
    result: dict | None = None,
) -> dict:
    """Record an automation execution (append-only audit trail)."""
    run_id = _new_id("run")
    now = _now_iso()
    completed = now if status in ("success", "failed", "skipped") else None

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_automation_runs
               (run_id, automation_id, trigger_event, status, result_json,
                started_at, completed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (run_id, automation_id, trigger_event, status, json.dumps(result or {}), now, completed),
        )
        conn.commit()
        return {"status": "ok", "run_id": run_id}
    finally:
        conn.close()


def list_automation_runs(
    *,
    automation_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM studio_automation_runs"
        params: list[Any] = []
        if automation_id:
            sql += " WHERE automation_id = ?"
            params.append(automation_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── External event sources (dwo-evt-01 registry) ─────────────────────


def get_event_source(source_id: str) -> dict | None:
    """Read one studio_event_sources row, or None if absent/unavailable.

    Returns None rather than raising when the table has not been migrated
    yet, so simulation still works on a fresh database.
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
    d = dict(row)
    try:
        d["config"] = json.loads(d.get("config_json") or "{}")
    except (TypeError, ValueError):
        d["config"] = {}
    return d


def _trigger_matches(trigger: dict, event: dict) -> tuple[bool, str]:
    """Does `event` match this automation's trigger? Returns (matched, reason)."""
    ttype = trigger.get("type", "manual")
    etype = event.get("type", ttype)
    if etype != ttype:
        return False, f"event type '{etype}' does not match trigger '{ttype}'"

    if ttype != "external_event":
        return True, "trigger type matched"

    cfg = trigger.get("config", {}) or {}
    want_source = cfg.get("source_id") or ""
    got_source = event.get("source_id") or ""
    if want_source and got_source != want_source:
        return False, f"event source '{got_source or '(none)'}' does not match '{want_source}'"

    want_event = cfg.get("event_type") or ""
    got_event = event.get("event_type") or ""
    if want_event and got_event != want_event:
        return False, f"event_type '{got_event or '(none)'}' does not match '{want_event}'"

    return True, "source and event_type matched"


def _sample_event(auto: dict) -> dict:
    """Build a representative payload for a dry run.

    For external_event we use the source's registered `sample_payload` when it
    has one — otherwise the bare envelope the source would deliver.  Condition
    fields are never back-filled: a simulation that invented values to make its
    own conditions pass would be worthless.
    """
    trigger = auto.get("trigger", {}) or {}
    ttype = trigger.get("type", "manual")
    if ttype != "external_event":
        return {"type": ttype, "test": True}

    cfg = trigger.get("config", {}) or {}
    event: dict[str, Any] = {
        "type": "external_event",
        "source_id": cfg.get("source_id", ""),
        "event_type": cfg.get("event_type", ""),
        "test": True,
    }
    source = get_event_source(cfg.get("source_id", ""))
    if source:
        event["source_name"] = source.get("name")
        event["source_kind"] = source.get("kind")
        sample = source.get("config", {}).get("sample_payload")
        if isinstance(sample, dict):
            event["payload"] = sample
            # Top-level access so conditions can name payload fields directly.
            for k, v in sample.items():
                event.setdefault(k, v)
    return event


_MISSING = object()


def resolve_field(obj: Any, path: str, default: Any = None) -> Any:
    """Walk a dotted path into nested dicts and lists. Missing → ``default``.

    The single field resolver for this DSL. Both halves of a trigger use it, so
    ``filter_json`` and ``input_mapping_json`` cannot disagree about what a
    field name means — which they did (dwo-evt-05): ``input_mapping`` walked
    dotted paths while filters resolved with a flat ``event.get(field, "")``, so
    ``payload.repo.name`` read as empty in a filter and as the real value in a
    mapping. An ``is_empty`` condition on a field that exists therefore matched,
    silently inverting a narrowing filter into one that always fires.

    A purely numeric segment indexes a sequence, so ``labels.0`` reaches the
    first label. Strings are not treated as sequences: ``name.0`` on a string
    field is a missing path, not its first character.

    Missing returns ``default`` (``None``) rather than raising — a filter naming
    a field the payload lacks is a normal non-match, not an error.
    """
    if not path:
        return default
    cursor: Any = obj
    for part in str(path).split("."):
        if isinstance(cursor, dict):
            if part not in cursor:
                return default
            cursor = cursor[part]
        elif isinstance(cursor, (list, tuple)) and part.lstrip("-").isdigit():
            index = int(part)
            if not -len(cursor) <= index < len(cursor):
                return default
            cursor = cursor[index]
        else:
            return default
    return cursor


def _resolve_event_path(event: dict, path: str, default: Any = None) -> Any:
    """``resolve_field`` with the ``event.`` prefix the DSL documents.

    ``event.payload.branch`` and ``payload.branch`` name the same thing; the
    prefix is optional sugar, stripped once here rather than at each call site.
    """
    if isinstance(path, str) and path.startswith("event."):
        stripped = resolve_field(event, path[6:], _MISSING)
        if stripped is not _MISSING:
            return stripped
        # Fall through: a payload may genuinely carry a key called "event".
    return resolve_field(event, path, default)


def _resolve_input_mapping(mapping: Any, event: dict) -> dict:
    """Resolve {"memory_key": "event.field"} against an event payload.

    A source string that names no event field is passed through as a literal,
    so constants can be mapped without a second syntax.
    """
    if isinstance(mapping, str):
        try:
            mapping = json.loads(mapping)
        except (TypeError, ValueError):
            return {}
    if not isinstance(mapping, dict):
        return {}

    resolved: dict[str, Any] = {}
    for key, src in mapping.items():
        if not isinstance(src, str):
            resolved[key] = src
            continue
        value = _resolve_event_path(event, src, _MISSING)
        # An unresolvable source is a literal, so constants need no second syntax.
        resolved[key] = src if value is _MISSING else value
    return resolved


# ── Simulate execution (dry-run) ─────────────────────────────────────


def simulate_automation(auto_id: str, test_event: dict | None = None) -> dict:
    """Simulate an automation without executing actions."""
    auto = get_automation(auto_id)
    if not auto:
        return {"status": "error", "error": "Automation not found"}

    trigger = auto.get("trigger", {}) or {}
    event = test_event or _sample_event(auto)
    trigger_matched, trigger_reason = _trigger_matches(trigger, event)

    condition_results = evaluate_conditions(auto.get("conditions", []), event)
    conditions_met = all(c["met"] for c in condition_results)
    would_fire = trigger_matched and conditions_met

    # List actions that would fire
    actions_preview = []
    for action in auto.get("actions", []):
        preview = {
            "type": action.get("type", ""),
            "config": action.get("config", {}),
            "would_execute": would_fire,
        }
        if action.get("type") == "run_workflow":
            cfg = action.get("config", {}) or {}
            preview["workflow_id"] = cfg.get("workflow_id", "")
            preview["resolved_inputs"] = _resolve_input_mapping(cfg.get("input_mapping"), event)
        actions_preview.append(preview)

    return {
        "status": "ok",
        "automation_id": auto_id,
        "trigger_type": trigger.get("type", ""),
        "trigger_matched": trigger_matched,
        "trigger_reason": trigger_reason,
        "sample_event": event,
        "conditions_met": conditions_met,
        "condition_results": condition_results,
        "actions_preview": actions_preview,
        "dry_run": True,
    }


# ── Live execution ───────────────────────────────────────────────────


def _run_workflow_action(config: dict, event: dict) -> dict:
    """Start a real Studio workflow run for a run_workflow action."""
    from tools.studio import run_memory  # noqa: PLC0415
    from tools.studio.workflow_runner import start_run  # noqa: PLC0415

    workflow_id = (config or {}).get("workflow_id", "")
    if not workflow_id:
        return {"status": "failed", "error": "run_workflow action has no workflow_id"}

    project_id = (config or {}).get("project_id") or "default"
    try:
        run_id = start_run(workflow_id, project_id)
    except Exception as exc:
        return {"status": "failed", "workflow_id": workflow_id, "error": str(exc)}

    inputs = _resolve_input_mapping(config.get("input_mapping"), event)
    for key, value in inputs.items():
        # Best effort: the run thread is already live, so seed immediately.
        try:
            run_memory.set(run_id, key, value)
        except Exception:  # noqa: S110 — a run must not die over one input
            pass

    return {"status": "success", "workflow_id": workflow_id, "run_id": run_id, "inputs": inputs}


def _execute_action(action: dict, event: dict) -> dict:
    atype = action.get("type", "")
    if atype == "run_workflow":
        result = _run_workflow_action(action.get("config", {}) or {}, event)
    else:
        # Every other action type is still declarative-only — no executor is
        # registered.  Say so rather than reporting a success that never was.
        result = {"status": "skipped", "reason": "no executor registered for this action type"}
    result["type"] = atype
    return result


def trigger_automation(auto_id: str, event: dict | None = None) -> dict:
    """Fire an automation for a real event, executing its actions.

    Every outcome is written to the append-only studio_automation_runs log,
    including non-matches — a non-firing automation must be diagnosable.
    """
    auto = get_automation(auto_id)
    if not auto:
        return {"status": "error", "error": "Automation not found"}

    trigger = auto.get("trigger", {}) or {}
    event = event or {"type": trigger.get("type", "manual")}
    trigger_event = event.get("event_type") or event.get("type") or trigger.get("type", "manual")

    def _log(status: str, payload: dict) -> dict:
        logged = log_automation_run(auto_id, str(trigger_event), status, payload)
        payload["status"] = status
        payload["automation_id"] = auto_id
        payload["run_log_id"] = logged.get("run_id")
        return payload

    if not auto.get("enabled", 1):
        return _log("skipped", {"reason": "automation is disabled"})

    matched, reason = _trigger_matches(trigger, event)
    if not matched:
        return _log("skipped", {"reason": reason, "trigger_matched": False})

    condition_results = evaluate_conditions(auto.get("conditions", []), event)
    if not all(c["met"] for c in condition_results):
        return _log(
            "skipped",
            {
                "reason": "conditions not met",
                "trigger_matched": True,
                "condition_results": condition_results,
            },
        )

    action_results = [_execute_action(a, event) for a in auto.get("actions", [])]
    status = "failed" if any(r.get("status") == "failed" for r in action_results) else "success"
    return _log(
        status,
        {
            "trigger_matched": True,
            "condition_results": condition_results,
            "action_results": action_results,
        },
    )


# ── Condition DSL (the single implementation) ────────────────────────
#
# Automations are not the only thing that filters an event by
# ``{"field", "operator", "value"}`` — workflow triggers do too, and anything
# else that grows an event filter should as well.  These two functions are the
# one implementation of ``CONDITION_OPERATORS``; import them rather than
# writing a second condition DSL that drifts from the operator list the UI
# renders.


def evaluate_conditions(conditions: list[dict] | None, event: dict) -> list[dict]:
    """Evaluate every condition against an event, returning a per-condition trace.

    Each result carries the field, operator, expected and actual values so a
    non-match is explainable — callers decide whether all conditions must be
    met by inspecting ``met``.
    """
    results = []
    for cond in conditions or []:
        field = cond.get("field", "")
        op = cond.get("operator", "equals")
        expected = cond.get("value", "")

        # Flat key first, dotted path second. Deliberately in that order: this
        # DSL is shared with the automation surface, so a stored condition whose
        # field name literally contains a dot must keep resolving to that key
        # rather than being reinterpreted as a path into nested data. Only a
        # name that is NOT a literal key is walked, which makes nested access
        # strictly additive — no existing automation changes meaning.
        if isinstance(event, dict) and field in event:
            actual = event[field]
        else:
            actual = _resolve_event_path(event, field, _MISSING)
            if actual is _MISSING:
                actual = ""  # unresolvable reads as empty, as it always has
        results.append(
            {
                "field": field,
                "operator": op,
                "expected": expected,
                "actual": str(actual),
                "met": evaluate_condition(actual, op, expected),
            }
        )
    return results


def evaluate_condition(actual: Any, operator: str, expected: str) -> bool:
    """Evaluate a single condition. Unknown operators never match."""
    actual_str = str(actual).lower()
    expected_lower = expected.lower()

    if operator == "equals":
        return actual_str == expected_lower
    elif operator == "not_equals":
        return actual_str != expected_lower
    elif operator == "contains":
        return expected_lower in actual_str
    elif operator == "greater_than":
        try:
            return float(actual) > float(expected)
        except (ValueError, TypeError):
            return False
    elif operator == "less_than":
        try:
            return float(actual) < float(expected)
        except (ValueError, TypeError):
            return False
    elif operator == "in_list":
        items = [i.strip().lower() for i in expected.split(",")]
        return actual_str in items
    elif operator == "is_empty":
        return not actual_str or actual_str == "none"
    elif operator == "is_not_empty":
        return bool(actual_str) and actual_str != "none"
    return False


def resolve_input_mapping(mapping: Any, event: dict) -> dict:
    """Resolve ``{"memory_key": "event.field"}`` against an event payload.

    Lives beside the condition DSL for the same reason it does: an automation
    action and a workflow trigger both turn an event into workflow inputs, and
    two implementations of that would drift.  ``tools/studio/event_sources.py``
    imports this rather than reimplementing it.

    A source string that names no event field is passed through as a literal,
    so a constant can be mapped without inventing a second syntax.
    """
    if isinstance(mapping, str):
        try:
            mapping = json.loads(mapping)
        except (TypeError, ValueError):
            return {}
    if not isinstance(mapping, dict):
        return {}

    resolved: dict[str, Any] = {}
    for key, src in mapping.items():
        if not isinstance(src, str):
            resolved[key] = src
            continue
        path = src[6:] if src.startswith("event.") else src
        cursor: Any = event
        found = True
        for part in path.split("."):
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                found = False
                break
        resolved[key] = cursor if found else src
    return resolved


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Studio Automation Builder")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("triggers", help="List trigger types")
    sub.add_parser("operators", help="List condition operators")
    sub.add_parser("actions", help="List action types")
    sub.add_parser("templates", help="List automation templates")
    sub.add_parser("list", help="List saved automations")
    sub.add_parser("runs", help="List recent runs")

    p_sim = sub.add_parser("simulate", help="Simulate an automation")
    p_sim.add_argument("automation_id")
    p_sim.add_argument("--event", help="Test event as JSON (default: sample payload)")

    p_trig = sub.add_parser("trigger", help="Fire an automation for a real event")
    p_trig.add_argument("automation_id")
    p_trig.add_argument("--event", help="Event as JSON")

    args = parser.parse_args()
    result: Any = None

    if args.command == "triggers":
        result = get_trigger_types()
    elif args.command == "operators":
        result = get_condition_operators()
    elif args.command == "actions":
        result = get_action_types()
    elif args.command == "templates":
        result = get_automation_templates()
    elif args.command == "list":
        result = list_automations()
    elif args.command == "runs":
        result = list_automation_runs()
    elif args.command == "simulate":
        result = simulate_automation(args.automation_id, json.loads(args.event) if args.event else None)
    elif args.command == "trigger":
        result = trigger_automation(args.automation_id, json.loads(args.event) if args.event else None)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
