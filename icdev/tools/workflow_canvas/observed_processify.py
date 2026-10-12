#!/usr/bin/env python3
# CUI // SP-CTI
"""Process-ify from an OBSERVED scenario (rea-pif-01, project card ``rea``).

WHAT THIS IS. Process-Ify normally reads a document. This module reads what a
program was SEEN doing: an operator-run REA ``capture_process_scenario`` (or
``capture_browser_scenario``) becomes an as-observed step sequence, and that
sequence becomes a DRAFT workflow in the workflow canvas (``studio_workflows``,
so the existing BPMN export works on it unchanged). Every step is tagged
``basis: observed`` with the REA ``evidence_id`` it came from; the workflow is
``status: draft`` with ``review.state: pending_hitl`` and cannot be linked into
a process chain until a human approves it (:func:`chain_eligibility`).

THE CAPTURE IS OPT-IN, PER RUN. A capture LAUNCHES the target (REA declares
``launchesProcess``; the browser capture also ``mutatesTarget`` and
``accessesNetwork``). So :func:`capture_scenario` refuses -- before REA is
reached, before a registry exists -- unless ``allow_execute is True`` for THIS
call. Not truthy: ``True``. The REA capture tools are never on the default
registry's tool list; they are exposed only by a registry constructed with
``allow_execute=True`` (``execute_tools:`` in ``args/external_mcp_servers.yaml``),
and this module builds one for one capture and closes it.

THE OTHER RAILS (card ``rea``):
  * authorized targets only -- ``authorization_basis`` (owned | licensed |
    customer-authorized, or ``ICDEV_RE_AUTHORIZATION_BASIS``) is required, the
    executable is resolved to a file, hashed, and granted on this run's
    registry only. sha256 + basis ride on the draft's ``provenance``;
  * analysis stays local -- REA is the stdio server rea-wire-01 registered;
  * containment -- the REA process is launched under the omx-guard-05 L3
    write sandbox when the host has one, and ``containment`` says when not;
  * an unmeasured list is ``None``, never ``[]``: a capture REA returned no
    recognisable step sequence for gives ``steps: None`` with
    ``steps_basis``, and no draft is written from it.

WHAT IS NOT WIRED, and why. ``capture_browser_scenario``'s target is a URL, not
a file ``target_auth`` can hash and grant, and REA 6.4.0's recorded catalog
(rea-spike-01, Windows) does not offer it -- so ``kind="browser"`` is
``unavailable`` / ``browser_target_unauthorizable`` and nothing runs. A browser
capture made elsewhere can still be turned into a draft with
:func:`build_draft`, which accepts REA's evidence record as given.

Library, no CLI. Dashboard: ``POST /workflow-canvas/api/workflows/observed-draft``
and ``POST /workflow-canvas/api/workflows/<id>/observed-review``.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "KINDS",
    "REVIEW_APPROVED",
    "REVIEW_PENDING",
    "REVIEW_REJECTED",
    "build_draft",
    "capture_scenario",
    "chain_eligibility",
    "review_draft",
    "save_draft",
    "steps_from_capture",
]

SERVER = "rea"
KINDS: Dict[str, str] = {
    "process": "capture_process_scenario",
    "browser": "capture_browser_scenario",
}

REVIEW_PENDING = "pending_hitl"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"
DECISIONS = {"approve": REVIEW_APPROVED, "reject": REVIEW_REJECTED}

#: Keys of ``normalized_result`` that may carry the observed sequence, in the
#: order they are preferred. The first that holds a non-empty list wins.
SEQUENCE_KEYS: Tuple[str, ...] = ("steps", "actions", "interactions", "events", "timeline")
#: Step labels: the first present key names the step.
LABEL_KEYS: Tuple[str, ...] = ("description", "label", "title", "action", "name", "type", "kind")
DETAIL_KEYS: Tuple[str, ...] = ("selector", "target", "url", "command", "data", "text", "value")
MAX_STEPS = 200
REDACTED = "[redacted]"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refusal(kind: str, reason: str, detail: str, *, status: str = "error",
             **extra: Any) -> Dict[str, Any]:
    return {"status": status, "kind": kind, "reason": reason, "reason_detail": detail, "record": None, **extra}


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _default_registry(command_prefix: List[str]) -> Any:
    from icdev.tools.mcp_client import registry as reg

    if not any(s.get("name") == SERVER for s in reg.enabled_servers()):
        return None
    return reg.ExternalToolRegistry(command_prefix=command_prefix, allow_execute=True)


def capture_scenario(
    kind: str,
    scenario: Mapping[str, Any],
    *,
    allow_execute: Any = False,
    authorization_basis: Optional[str] = None,
    actor: Optional[str] = None,
    registry_factory: Optional[Callable[[List[str]], Any]] = None,
) -> Dict[str, Any]:
    """Run ONE operator-approved REA capture, or say exactly why not.

    Returns ``{status, kind, reason, reason_detail, record, provenance,
    containment}`` where ``record`` is the mapped REA evidence record
    (:func:`tools.analyzers.rea_adapter.map_response`) on success.
    """
    from icdev.tools.analyzers import rea_adapter

    if kind not in KINDS:
        return _refusal(kind, "unknown_kind", f"kind {kind!r} is not one of {sorted(KINDS)}")
    if allow_execute is not True:
        return _refusal(kind, "allow_execute_required",
                        "a scenario capture LAUNCHES the target; pass allow_execute=True for "
                        "this run to opt in. Nothing was run.")
    if kind == "browser":
        return _refusal(kind, "browser_target_unauthorizable",
                        "capture_browser_scenario targets a URL, which target_auth cannot hash "
                        "and grant, and REA 6.4.0's recorded catalog does not offer it. "
                        "Nothing was run.", status="unavailable")

    scenario = dict(scenario or {})
    executable = str(scenario.get("executable") or "").strip()
    resolved = shutil.which(executable) if executable else None
    path = Path(resolved or executable).expanduser() if executable else None
    sha256 = rea_adapter._digest(path) if path is not None and path.is_file() else None
    if sha256 is None:
        return _refusal(kind, "target_unreadable",
                        "scenario.executable must name a file (or a command on PATH) whose "
                        "bytes can be hashed for authorization. Nothing was run.")
    scenario["executable"] = str(path.resolve())

    basis_raw = (authorization_basis if authorization_basis is not None
                 else os.environ.get(rea_adapter.ENV_AUTHORIZATION, ""))
    auth = str(basis_raw or "").strip().lower()
    provenance = {"target_sha256": sha256,
                  "authorization_basis": auth if auth in rea_adapter.AUTHORIZATION_BASES else None,
                  "allow_execute": True, "analysis": "local", "server": SERVER,
                  "rea_version": rea_adapter.REA_VERSION, "tool": KINDS[kind],
                  "captured_at": _now()}
    if auth not in rea_adapter.AUTHORIZATION_BASES:
        return _refusal(kind, "authorization_missing",
                        "no authorization basis for this target (%s): pass "
                        "authorization_basis= or set %s. Nothing was run."
                        % (" | ".join(rea_adapter.AUTHORIZATION_BASES),
                           rea_adapter.ENV_AUTHORIZATION),
                        provenance=provenance)

    scratch = tempfile.mkdtemp(prefix="icdev-rea-capture-")
    prefix, l3 = rea_adapter._write_sandbox(scratch)
    containment = {"write_sandbox": l3, "allow_execute": True}
    registry = (registry_factory or _default_registry)(prefix)
    try:
        if registry is None:
            return _refusal(kind, "rea_not_enabled",
                            "the `rea` entry in args/external_mcp_servers.yaml is off (or the "
                            "master switch is), or this is an air-gapped or shared deployment. "
                            "Nothing was run.", status="unavailable", provenance=provenance,
                            containment=containment)
        offered = {t.remote_name for t in registry.discover() if t.server == SERVER}
        if KINDS[kind] not in offered:
            return _refusal(kind, "capture_not_offered",
                            f"{KINDS[kind]!r} is not exposed: REA is not connected, or the "
                            "registry was not opened with allow_execute. Nothing was run.",
                            status="unavailable", provenance=provenance, containment=containment)
        registry.grant_target(SERVER, scenario["executable"], basis=auth, operator=actor,
                              note=f"observed_processify {kind}")
        call = registry.call(f"ext__{SERVER}__{KINDS[kind]}", scenario,
                             classification=rea_adapter._classification())
        mapped = rea_adapter.map_response(KINDS[kind], call)
        if mapped["kind"] != "evidence":
            return _refusal(kind, mapped.get("reason") or "call_failed",
                            str(mapped.get("detail") or ""), provenance=provenance,
                            containment=containment, rea_error=mapped.get("rea_error"))
        return {"status": "ok", "kind": kind, "reason": None, "reason_detail": None,
                "record": mapped["record"], "provenance": provenance,
                "containment": containment, "scenario": _scrub_scenario(scenario)}
    finally:
        if registry is not None:
            registry.close()
        shutil.rmtree(scratch, ignore_errors=True)


def _scrub_scenario(scenario: Mapping[str, Any]) -> Dict[str, Any]:
    """The scenario as it may be stored: sensitive input and env values removed."""
    out = {k: v for k, v in scenario.items() if k not in ("environment",)}
    if isinstance(scenario.get("environment"), dict):
        out["environment_keys"] = sorted(scenario["environment"])
    events = []
    for ev in scenario.get("events") or []:
        if isinstance(ev, dict) and ev.get("sensitive"):
            ev = {**ev, "data": REDACTED}
        events.append(ev)
    if events:
        out["events"] = events
    return out


# ---------------------------------------------------------------------------
# Evidence -> as-observed steps
# ---------------------------------------------------------------------------

def _label(item: Mapping[str, Any]) -> Optional[str]:
    head = next((str(item[k]) for k in LABEL_KEYS if isinstance(item.get(k), (str, int))
                 and str(item[k]).strip()), None)
    if head is None:
        return None
    if item.get("sensitive") is True:
        return f"{head}: {REDACTED}"
    detail = next((str(item[k]) for k in DETAIL_KEYS if isinstance(item.get(k), (str, int))
                   and str(item[k]).strip() and str(item[k]) != head), None)
    text = f"{head}: {detail}" if detail else head
    return " ".join(text.split())[:160]


def steps_from_capture(record: Optional[Mapping[str, Any]]) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """``(steps, steps_basis)`` from one mapped REA capture evidence record.

    ``steps`` is ``None`` when the sequence was not measured -- no record, no
    ``normalized_result``, or none of :data:`SEQUENCE_KEYS` holds a list of
    labelled entries. It is never ``[]``.
    """
    if not record or not isinstance(record.get("normalized_result"), Mapping):
        return None, "no_evidence"
    normalized = record["normalized_result"]
    sequence = next((normalized[k] for k in SEQUENCE_KEYS
                     if isinstance(normalized.get(k), list) and normalized[k]), None)
    if sequence is None:
        return None, "no_sequence_in_evidence"
    steps: List[Dict[str, Any]] = []
    for item in sequence[:MAX_STEPS]:
        label = _label(item) if isinstance(item, Mapping) else None
        if label is None:
            continue
        steps.append({
            "id": f"obs-{len(steps) + 1:03d}",
            "title": label,
            "basis": "observed",
            "evidence_id": record.get("evidence_id"),
            "at_ms": item.get("at_ms") if isinstance(item.get("at_ms"), (int, float)) else None,
        })
    exit_info = normalized.get("exit")
    if steps and isinstance(exit_info, Mapping) and exit_info.get("code") is not None:
        steps.append({"id": f"obs-{len(steps) + 1:03d}",
                      "title": f"Process exited (code {exit_info.get('code')})",
                      "basis": "observed", "evidence_id": record.get("evidence_id"),
                      "at_ms": None})
    if not steps:
        return None, "no_labelled_steps"
    basis = "truncated" if record.get("truncated") or len(sequence) > MAX_STEPS else "reported_by_rea"
    return steps, basis


# ---------------------------------------------------------------------------
# Draft workflow + HITL review
# ---------------------------------------------------------------------------

def build_draft(capture: Mapping[str, Any], *, name: Optional[str] = None) -> Dict[str, Any]:
    """A draft workflow dict (the shape ``bpmn_export.workflow_to_bpmn`` reads).

    ``steps`` is ``None`` when the capture yielded no measured sequence; the
    caller must not save such a draft (:func:`save_draft` refuses it).
    """
    record = capture.get("record") if capture.get("status") == "ok" else None
    steps, steps_basis = steps_from_capture(record)
    for i, step in enumerate(steps or []):
        step["assignee_role"] = "Observed actor"
        step["dependencies"] = [steps[i - 1]["id"]] if i else []
    kind = capture.get("kind") or "process"
    target = Path(str((capture.get("scenario") or {}).get("executable") or "")).name
    return {
        "workflow_name": name or f"Observed {kind} scenario{': ' + target if target else ''}",
        "workflow_description": ("AS-OBSERVED draft from an REA scenario capture. Steps are what "
                                 "the program was seen doing, not a designed process; a human "
                                 "must review it before it becomes a chain."),
        "industry": "General",
        "basis": "observed",
        "status": "draft",
        "observed": True,
        "review": {"required": True, "state": REVIEW_PENDING, "reviewer": None,
                   "decided_at": None, "note": None},
        "steps": steps,
        "steps_basis": steps_basis,
        "evidence_ids": [record["evidence_id"]] if record and record.get("evidence_id") else [],
        "limitations": record.get("limitations") if record else None,
        "residual_unknowns": record.get("residual_unknowns") if record else None,
        "provenance": dict(capture.get("provenance") or {}),
        "scenario": capture.get("scenario"),
    }


def _audit(event_type: str, action: str, details: Dict[str, Any]) -> None:
    try:
        from icdev.tools.audit.audit_logger import log_event

        log_event(event_type=event_type, actor="observed_processify", action=action,
                  details=details)
    except Exception:  # noqa: BLE001 -- the workflow row is the record; audit is corroboration
        pass


def save_draft(draft: Mapping[str, Any], *, conn: Any = None, created_by: str = "observed_processify") -> str:
    """Persist *draft* to ``studio_workflows``. Returns its ``workflow_id``.

    Raises ``ValueError`` for a draft with no measured steps or one that is not
    a pending observed draft -- an approved workflow is never minted here.
    """
    import yaml

    if not draft.get("steps"):
        raise ValueError(f"no observed steps to draft ({draft.get('steps_basis')})")
    if draft.get("basis") != "observed" or (draft.get("review") or {}).get("state") != REVIEW_PENDING:
        raise ValueError("save_draft writes only pending observed drafts")
    from icdev.tools.db.storage import get_connection, sql_placeholder

    own = conn is None
    conn = conn or get_connection()
    workflow_id = f"wfl-{uuid.uuid4().hex[:12]}"
    body = dict(draft)
    body["name"] = draft.get("workflow_name")
    try:
        ph = sql_placeholder(conn)
        now = _now()
        # The caller's classification, so the exact-match RLS predicate lets the
        # same caller read and review the draft (workflow_editor.create_workflow).
        ctx = getattr(conn, "_security_context", None)
        classification = getattr(ctx, "classification", None) or "CUI"
        conn.execute(
            f"""INSERT INTO studio_workflows
                (workflow_id, name, description, template_yaml, category,
                 created_by, created_at, updated_at, version, shared, classification)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
            (workflow_id, draft.get("workflow_name"), draft.get("workflow_description"),
             yaml.safe_dump(body, default_flow_style=False, allow_unicode=True, sort_keys=False),
             "observed", created_by, now, now, 1, 0, classification),
        )
        conn.commit()
    finally:
        if own:
            conn.close()
    _audit("workflow_created", f"observed draft {workflow_id}",
           {"workflow_id": workflow_id, "basis": "observed", "review": REVIEW_PENDING,
            "target_sha256": (draft.get("provenance") or {}).get("target_sha256"),
            "evidence_ids": draft.get("evidence_ids")})
    return workflow_id


def review_draft(workflow_id: str, decision: str, *, reviewer: str, note: str = "",
                 conn: Any = None) -> Dict[str, Any]:
    """Record a human decision on an observed draft. Returns the new review block.

    Raises ``ValueError`` for an unknown decision, a missing reviewer, a
    workflow that is not an observed draft, or one already decided.
    """
    import yaml

    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
    if not str(reviewer or "").strip():
        raise ValueError("a named reviewer is required")
    from icdev.tools.db.storage import get_connection, sql_placeholder

    own = conn is None
    conn = conn or get_connection()
    try:
        ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT template_yaml FROM studio_workflows WHERE workflow_id = {ph}",
                           (workflow_id,)).fetchone()
        if row is None:
            raise ValueError(f"workflow {workflow_id!r} not found")
        wf = yaml.safe_load(row[0] or "{}") or {}
        review = wf.get("review") or {}
        if wf.get("basis") != "observed":
            raise ValueError(f"workflow {workflow_id!r} is not an observed draft")
        if review.get("state") != REVIEW_PENDING:
            raise ValueError(f"workflow {workflow_id!r} was already {review.get('state')}")
        review = {"required": True, "state": DECISIONS[decision], "reviewer": reviewer.strip(),
                  "decided_at": _now(), "note": note or None}
        wf["review"] = review
        wf["status"] = "reviewed" if decision == "approve" else "rejected"
        conn.execute(
            f"UPDATE studio_workflows SET template_yaml = {ph}, updated_at = {ph} "
            f"WHERE workflow_id = {ph}",
            (yaml.safe_dump(wf, default_flow_style=False, allow_unicode=True, sort_keys=False),
             review["decided_at"], workflow_id),
        )
        conn.commit()
    finally:
        if own:
            conn.close()
    _audit("approval_approved" if decision == "approve" else "approval_rejected",
           f"observed draft {workflow_id} {review['state']}",
           {"workflow_id": workflow_id, "reviewer": review["reviewer"], "note": review["note"]})
    return review


def chain_eligibility(template: Optional[Mapping[str, Any]]) -> Tuple[bool, Optional[str]]:
    """May this workflow be linked into a process chain? ``(ok, reason)``.

    Only observed workflows are gated: they need an ``approved`` review.
    """
    wf = template or {}
    if wf.get("basis") != "observed":
        return True, None
    state = (wf.get("review") or {}).get("state")
    if state == REVIEW_APPROVED:
        return True, None
    return False, f"observed draft awaiting human review (review.state={state})"
