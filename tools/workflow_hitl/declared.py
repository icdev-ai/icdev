# CUI // SP-CTI
"""Declared decision templates: every decision a person takes runs through one (icx-cnf-02).

The owner's rule (2026-11-14): wherever a PERSON decides something, the decision
goes through a declared workflow, and agents only propose. ICDEV Cortex and the
ACE Co-Worker Engine had eleven decision surfaces with no template. Each now
names one under ``args/workflow_hitl_templates/*.yaml``, and its route calls this
module before the effect runs.

A template is a ``wf_templates`` row like any other: an automated ``propose``
stage, then ONE manual stage held by a role. The manual stage adds three fields
the engine ignores and this module enforces:

``holders``          the dashboard roles that may take the step. A service key
                     (role ``service``) is never a holder: it is not a person.
``decisions``        each allowed decision, ``affirm`` or ``deny``.
``reason_required``  the decisions that need a reason.
``separate_from``    ``proposer``: the person who proposed may not decide.

Two ways through a template:

``take_step``  the person decides in the request itself (one action, so the
               console keeps its tempo). A wf instance is opened on the template,
               its manual approval is closed by that person, and a ``decided``
               row lands in ``wf_decision_steps``. Then the route runs its effect.
``propose``    an agent, a service key or a person without the role asks. A
               ``proposed`` row lands; nothing else happens. A holder decides at
               ``/decisions`` (``decide``), and a machine caller then re-posts with
               the step id, which ``spend`` honours once, for the same payload.

The ledger is append-only. ``UNIQUE(step_id, event)`` is what stops one step
being decided or spent twice. If the workflow store cannot be written, the step
is refused and the effect does not run: a decision that cannot be recorded is
not taken.

CLI:
    python -m tools.workflow_hitl.declared --seed --json
    python -m tools.workflow_hitl.declared --list --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from icdev.core.paths import repo_root

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

REPO_ROOT = repo_root(__file__)
TEMPLATES_DIR = REPO_ROOT / "args" / "workflow_hitl_templates"
LEDGER = "wf_decision_steps"

#: A machine principal. Never a holder of a person's step.
SERVICE_ROLE = "service"

EVENTS = ("proposed", "decided", "spent", "withdrawn")


class StepRefused(Exception):
    """The step cannot be taken. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "workflow_refused": True}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Templates ────────────────────────────────────────────────────────────────


def load_templates(directory: Path | None = None) -> dict[str, dict]:
    """Template id -> template, read from the YAML files."""
    out: dict[str, dict] = {}
    for path in sorted((directory or TEMPLATES_DIR).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data["_file"] = path.name
        out[str(data["id"])] = data
    return out


def template_for(surface: str, directory: Path | None = None) -> dict | None:
    """The declared template whose ``decides`` names ``surface``."""
    for tmpl in load_templates(directory).values():
        if surface in (tmpl.get("decides") or []):
            return tmpl
    return None


def manual_stage(tmpl: dict) -> dict:
    """The one manual stage of a declared template."""
    stages = [s for s in tmpl.get("stages") or [] if s.get("step_type") == "manual"]
    if len(stages) != 1:
        raise ValueError(f"{tmpl.get('id')}: a declared template has exactly one manual stage")
    return stages[0]


def validate_template(tmpl: dict) -> list[str]:
    """Shape errors for one template. Empty when it is well formed."""
    errors: list[str] = []
    tid = tmpl.get("id") or tmpl.get("_file")
    if not tmpl.get("id"):
        errors.append(f"{tid}: no id")
    if not tmpl.get("decides"):
        errors.append(f"{tid}: decides names no surface")
    try:
        stage = manual_stage(tmpl)
    except ValueError as exc:
        return errors + [str(exc)]
    if not stage.get("role"):
        errors.append(f"{tid}: the manual stage has no role")
    holders = stage.get("holders") or []
    if not holders:
        errors.append(f"{tid}: the manual stage has no holders")
    if SERVICE_ROLE in holders:
        errors.append(f"{tid}: a service key cannot hold a person's step")
    decisions = stage.get("decisions") or {}
    if not decisions or set(decisions.values()) - {"affirm", "deny"}:
        errors.append(f"{tid}: decisions must map each decision to affirm or deny")
    for name in stage.get("reason_required") or []:
        if name not in decisions:
            errors.append(f"{tid}: reason_required names unknown decision {name!r}")
    if stage.get("separate_from") not in (None, "proposer"):
        errors.append(f"{tid}: separate_from may only be 'proposer'")
    return errors


def _wf_row(tmpl: dict) -> tuple[str, str]:
    """(stages_json, roles_json) as the engine stores them."""
    stages = [{k: v for k, v in s.items()} for s in tmpl.get("stages") or []]
    roles = {s["name"]: s["role"] for s in stages if s.get("role")}
    return json.dumps(stages, sort_keys=True), json.dumps(roles, sort_keys=True)


def seed_declared_templates(conn=None, directory: Path | None = None) -> dict:
    """Upsert every declared template into ``wf_templates``. Idempotent.

    System rows, never the default for a canvas: ``get_default`` must not hand a
    kanban task a decision template.
    """
    own = conn is None
    conn = conn or get_connection()
    seeded, updated = [], []
    try:
        for tid, tmpl in load_templates(directory).items():
            errors = validate_template(tmpl)
            if errors:
                raise ValueError("; ".join(errors))
            stages, roles = _wf_row(tmpl)
            now = _now()
            row = conn.execute(
                "SELECT stages_json FROM wf_templates WHERE id=%s", (tid,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO wf_templates
                       (id, name, canvas_type, stages_json, roles_json, approval_policy,
                        kickback_limit, is_default, is_system, created_by, created_at, updated_at)
                       VALUES (%s,%s,NULL,%s,%s,%s,%s,0,1,'declared',%s,%s)""",
                    (tid, tmpl["name"], stages, roles, tmpl.get("approval_policy", "any_one"),
                     int(tmpl.get("kickback_limit", 1)), now, now),
                )
                seeded.append(tid)
            elif row[0] != stages:
                conn.execute(
                    "UPDATE wf_templates SET name=%s, stages_json=%s, roles_json=%s, "
                    "updated_at=%s WHERE id=%s",
                    (tmpl["name"], stages, roles, now, tid),
                )
                updated.append(tid)
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"seeded": seeded, "updated": updated}


# ── Who is deciding ──────────────────────────────────────────────────────────


def current_actor() -> dict | None:
    """``{id, role}`` of the authenticated principal, or None outside a request."""
    try:
        from flask import g, has_request_context
    except Exception:  # noqa: BLE001
        return None
    if not has_request_context():
        return None
    user = getattr(g, "current_user", None)
    if not user:
        return None
    if isinstance(user, dict):
        uid, role = user.get("id"), user.get("role")
    else:
        uid, role = getattr(user, "id", None), getattr(user, "role", None)
    if not uid:
        return None
    return {"id": str(uid), "role": str(role or "")}


def can_hold(template_id: str, actor: dict | None) -> bool:
    """True when ``actor`` is a person whose role holds the template's manual stage."""
    if not actor or actor.get("role") == SERVICE_ROLE:
        return False
    tmpl = _template(template_id)
    return actor.get("role") in (manual_stage(tmpl).get("holders") or [])


def payload_digest(payload: Any) -> str:
    """SHA-256 over canonical JSON. A spent step must match it exactly."""
    raw = json.dumps(payload if payload is not None else {}, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Taking a step ────────────────────────────────────────────────────────────


def _template(template_id: str) -> dict:
    tmpl = load_templates().get(template_id)
    if tmpl is None:
        raise StepRefused(500, "undeclared", f"no declared template {template_id!r}")
    return tmpl


def _check(tmpl: dict, surface: str, actor: dict | None, decision: str, reason: str,
           proposed_by: str = "") -> tuple[dict, bool]:
    if surface not in (tmpl.get("decides") or []):
        raise StepRefused(500, "undeclared",
                          f"{tmpl['id']} does not decide {surface}")
    stage = manual_stage(tmpl)
    if not actor:
        raise StepRefused(401, "no_person", "a decision needs a signed-in person")
    if actor.get("role") == SERVICE_ROLE:
        raise StepRefused(403, "not_a_person",
                          "a service key can propose this decision, not take it")
    if actor.get("role") not in (stage.get("holders") or []):
        raise StepRefused(403, "role", (
            f"the '{stage['name']}' step of {tmpl['id']} is held by "
            f"{', '.join(stage.get('holders') or [])}; your role is {actor.get('role') or 'none'}"))
    decisions = stage.get("decisions") or {}
    if decision not in decisions:
        raise StepRefused(400, "decision",
                          f"decision must be one of {', '.join(sorted(decisions))}")
    if decision in (stage.get("reason_required") or []) and not (reason or "").strip():
        raise StepRefused(400, "reason", f"a reason is required to {decision}")
    if stage.get("separate_from") == "proposer" and proposed_by and proposed_by == actor["id"]:
        raise StepRefused(403, "separation",
                          "the person who proposed this may not decide it")
    return stage, decisions[decision] == "affirm"


def _append(conn, **row) -> str:
    event_id = f"wds-{uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO " + LEDGER + " (event_id, step_id, event, template_id, surface, subject, "
        "actor, actor_role, decision, affirmative, reason, instance_id, payload_digest, "
        "payload_json, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (event_id, row["step_id"], row["event"], row["template_id"], row["surface"],
         row.get("subject", ""), row["actor"], row.get("actor_role", ""),
         row.get("decision", ""), 1 if row.get("affirmative") else 0, row.get("reason", ""),
         row.get("instance_id", ""), row.get("payload_digest", ""),
         json.dumps(row.get("payload") if row.get("payload") is not None else {},
                    sort_keys=True, default=str),
         _now()),
    )
    return event_id


def _close_manual_stage(instance_id: str, actor: dict, decision: str, affirmative: bool,
                        reason: str) -> None:
    """The person closes the manual stage of a fresh instance.

    Affirm goes through the engine's own feedback path (it advances the instance
    to ``approved``). Deny records a kickback with the reason and ends the
    instance ``rejected``: a declared decision is one-shot, so it does not loop
    back to the propose stage the way a kanban review does.
    """
    from tools.workflow_hitl import feedback
    from tools.workflow_hitl.constants import WfStatus
    from tools.workflow_hitl.engine import WorkflowEngine

    status = WorkflowEngine().get_status(instance_id) or {}
    pending = next((a for a in status.get("approvals", []) if a.get("status") == "pending"), None)
    if pending is None:
        raise RuntimeError(f"instance {instance_id} opened no manual approval")
    comments = f"{decision}: {reason}".strip().rstrip(":")
    if affirmative:
        feedback.submit_feedback(pending["id"], "approve", actor["id"], comments=comments)
        return
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wf_feedback
               (id, approval_id, instance_id, task_id, canvas_type, template_id, stage,
                decision, comments, kickback_reason, submitted_by, submitted_at)
               VALUES (%s,%s,%s,NULL,NULL,%s,%s,'kickback',%s,%s,%s,%s)""",
            (f"wff-{uuid.uuid4().hex[:12]}", pending["id"], instance_id,
             status.get("template_id"), pending["stage"], comments, reason or decision,
             actor["id"], _now()),
        )
        conn.execute("UPDATE wf_approvals SET status='kickback', updated_at=%s WHERE id=%s",
                     (_now(), pending["id"]))
        conn.commit()
    finally:
        conn.close()
    WorkflowEngine()._set_status(instance_id, WfStatus.REJECTED)
    try:
        from tools.agent_runtime.inbox_adapters import settle_workflow

        settle_workflow(pending["id"], approved=False, resolved_by=actor["id"],
                        reason=reason or decision)
    except Exception as exc:  # noqa: BLE001
        logger.debug("approval-inbox settle skipped for %s: %s", pending["id"], exc)


def _record_decision(tmpl: dict, *, step_id: str, surface: str, subject: str, actor: dict,
                     decision: str, affirmative: bool, reason: str,
                     payload: Any = None) -> dict:
    from tools.workflow_hitl.engine import WorkflowEngine

    seed_declared_templates()
    instance_id = WorkflowEngine().create_instance(template_id=tmpl["id"])
    _close_manual_stage(instance_id, actor, decision, affirmative, reason)
    conn = get_connection()
    try:
        _append(conn, step_id=step_id, event="decided", template_id=tmpl["id"],
                surface=surface, subject=subject, actor=actor["id"],
                actor_role=actor.get("role", ""), decision=decision,
                affirmative=affirmative, reason=reason, instance_id=instance_id,
                payload_digest=payload_digest(payload) if payload is not None else "",
                payload=payload)
        conn.commit()
    except Exception as exc:
        conn.close()
        WorkflowEngine().cancel(instance_id)
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise StepRefused(409, "already_decided", f"step {step_id} is already decided")
        raise
    conn.close()
    stage = manual_stage(tmpl)
    return {"step_id": step_id, "template_id": tmpl["id"], "instance_id": instance_id,
            "stage": stage["name"], "role": stage["role"], "decision": decision,
            "affirmative": affirmative, "decided_by": actor["id"], "status": "decided"}


def take_step(template_id: str, *, surface: str, decision: str, subject: str = "",
              reason: str = "", actor: dict | None = None, payload: Any = None) -> dict:
    """A person takes the manual step in the request itself. Raises StepRefused."""
    tmpl = _template(template_id)
    actor = actor if actor is not None else current_actor()
    _, affirmative = _check(tmpl, surface, actor, decision, reason)
    try:
        return _record_decision(tmpl, step_id=f"step-{uuid.uuid4().hex[:16]}",
                                surface=surface, subject=subject, actor=actor,
                                decision=decision, affirmative=affirmative,
                                reason=reason, payload=payload)
    except StepRefused:
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed: no record, no effect
        logger.warning("declared step %s on %s not recorded: %s", template_id, surface, exc)
        raise StepRefused(503, "store_unavailable",
                          f"the decision could not be recorded, so it was not taken ({exc})")


def propose(template_id: str, *, surface: str, subject: str = "", payload: Any = None,
            actor: dict | None = None, note: str = "") -> dict:
    """Record a proposal for a holder to decide. Raises StepRefused when it cannot."""
    tmpl = _template(template_id)
    if surface not in (tmpl.get("decides") or []):
        raise StepRefused(500, "undeclared", f"{template_id} does not decide {surface}")
    actor = actor if actor is not None else current_actor()
    step_id = f"step-{uuid.uuid4().hex[:16]}"
    try:
        conn = get_connection()
        try:
            _append(conn, step_id=step_id, event="proposed", template_id=template_id,
                    surface=surface, subject=subject,
                    actor=(actor or {}).get("id") or "anonymous",
                    actor_role=(actor or {}).get("role", ""), reason=note,
                    payload_digest=payload_digest(payload), payload=payload)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        raise StepRefused(503, "store_unavailable",
                          f"the proposal could not be recorded ({exc})")
    stage = manual_stage(tmpl)
    return {"step_id": step_id, "template_id": template_id, "status": "pending",
            "stage": stage["name"], "role": stage["role"],
            "holders": list(stage.get("holders") or []), "decide_at": "/decisions"}


def withdraw(step_id: str, reason: str) -> None:
    """The proposal's own effect failed; nothing is left to decide. Best effort."""
    try:
        events = _events(step_id)
        first = events.get("proposed")
        if not first or "decided" in events:
            return
        conn = get_connection()
        try:
            _append(conn, step_id=step_id, event="withdrawn", template_id=first["template_id"],
                    surface=first["surface"], subject=first["subject"], actor="system",
                    reason=reason)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not withdraw %s: %s", step_id, exc)


def _events(step_id: str, conn=None) -> dict[str, dict]:
    own = conn is None
    conn = conn or get_connection()
    try:
        rows = conn.execute(
            "SELECT event_id, step_id, event, template_id, surface, subject, actor, actor_role, "
            "decision, affirmative, reason, instance_id, payload_digest, payload_json, created_at "
            "FROM " + LEDGER + " WHERE step_id=%s", (step_id,),
        ).fetchall()
    finally:
        if own:
            conn.close()
    cols = ("event_id", "step_id", "event", "template_id", "surface", "subject", "actor",
            "actor_role", "decision", "affirmative", "reason", "instance_id",
            "payload_digest", "payload_json", "created_at")
    out = {}
    for r in rows:
        row = dict(zip(cols, tuple(r)))
        row["created_at"] = str(row["created_at"] or "")
        out[row["event"]] = row
    return out


def decide(step_id: str, *, decision: str, reason: str = "",
           actor: dict | None = None) -> dict:
    """A holder decides a pending proposal. Raises StepRefused."""
    try:
        events = _events(step_id)
    except Exception as exc:  # noqa: BLE001
        raise StepRefused(503, "store_unavailable", f"the ledger cannot be read ({exc})")
    proposed = events.get("proposed")
    if proposed is None:
        raise StepRefused(404, "unknown_step", f"no proposal {step_id}")
    if "decided" in events:
        raise StepRefused(409, "already_decided", f"step {step_id} is already decided")
    if "withdrawn" in events:
        raise StepRefused(409, "withdrawn", f"step {step_id} was withdrawn")
    tmpl = _template(proposed["template_id"])
    actor = actor if actor is not None else current_actor()
    _, affirmative = _check(tmpl, proposed["surface"], actor, decision, reason,
                            proposed_by=proposed["actor"])
    payload = json.loads(proposed["payload_json"] or "{}")
    try:
        return _record_decision(tmpl, step_id=step_id, surface=proposed["surface"],
                                subject=proposed["subject"], actor=actor, decision=decision,
                                affirmative=affirmative, reason=reason, payload=payload)
    except StepRefused:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StepRefused(503, "store_unavailable",
                          f"the decision could not be recorded, so it was not taken ({exc})")


def spend(step_id: str, *, template_id: str, surface: str, payload: Any,
          actor: dict | None = None) -> dict:
    """Use a decided step once, for exactly the payload that was decided.

    For a machine caller that proposed and now re-posts. Raises StepRefused when
    the step is unknown, undecided, denied, already spent, for another surface,
    or for a different payload.
    """
    try:
        events = _events(step_id)
    except Exception as exc:  # noqa: BLE001
        raise StepRefused(503, "store_unavailable", f"the ledger cannot be read ({exc})")
    decided = events.get("decided")
    if decided is None:
        raise StepRefused(409, "not_decided", f"step {step_id} has not been decided by a person")
    if decided["template_id"] != template_id or decided["surface"] != surface:
        raise StepRefused(409, "wrong_surface", f"step {step_id} decides another surface")
    if not decided["affirmative"]:
        raise StepRefused(403, "denied", f"step {step_id} was decided '{decided['decision']}'")
    if "spent" in events:
        raise StepRefused(409, "spent", f"step {step_id} has already been used")
    if decided["payload_digest"] != payload_digest(payload):
        raise StepRefused(409, "payload", "the request differs from what the person decided")
    actor = actor if actor is not None else current_actor()
    try:
        conn = get_connection()
        try:
            _append(conn, step_id=step_id, event="spent", template_id=template_id,
                    surface=surface, subject=decided["subject"],
                    actor=(actor or {}).get("id") or "anonymous",
                    actor_role=(actor or {}).get("role", ""),
                    instance_id=decided["instance_id"], payload_digest=decided["payload_digest"])
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise StepRefused(409, "spent", f"step {step_id} has already been used")
        raise StepRefused(503, "store_unavailable", f"the step could not be spent ({exc})")
    return {"step_id": step_id, "template_id": template_id, "instance_id": decided["instance_id"],
            "decision": decided["decision"], "decided_by": decided["actor"], "status": "spent"}


def list_steps(status: str = "", limit: int = 200) -> list[dict]:
    """Steps, newest first, with their state derived from the ledger.

    ``status``: '' (all), 'pending', 'decided', 'spent' or 'withdrawn'.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT step_id, event, template_id, surface, subject, actor, actor_role, decision, "
            "affirmative, reason, instance_id, created_at FROM " + LEDGER
            + " ORDER BY created_at DESC LIMIT %s", (max(1, min(int(limit), 2000)) * 4,),
        ).fetchall()
    finally:
        conn.close()
    templates = load_templates()
    steps: dict[str, dict] = {}
    for r in rows:
        (step_id, event, template_id, surface, subject, actor, _role, decision,
         affirmative, reason, instance_id, created_at) = tuple(r)
        step = steps.setdefault(step_id, {
            "step_id": step_id, "template_id": template_id, "surface": surface,
            "subject": subject, "template_name": (templates.get(template_id) or {}).get("name", ""),
            "proposed_by": "", "decided_by": "", "decision": "", "affirmative": None,
            "reason": "", "note": "", "instance_id": "", "status": "", "updated_at": "",
        })
        if event == "proposed":
            step["proposed_by"] = actor
            step["note"] = reason
        elif event == "decided":
            step.update(decided_by=actor, decision=decision, affirmative=bool(affirmative),
                        reason=reason, instance_id=instance_id)
        step.setdefault("_events", set()).add(event)
        step["updated_at"] = max(step["updated_at"], str(created_at or ""))
    out = []
    for step in steps.values():
        events = step.pop("_events")
        step["status"] = next((e for e in ("withdrawn", "spent", "decided") if e in events),
                              "pending")
        if step["status"] == "pending" and "proposed" not in events:
            continue
        tmpl = templates.get(step["template_id"])
        if tmpl:
            stage = manual_stage(tmpl)
            step["stage"] = stage["name"]
            step["holders"] = list(stage.get("holders") or [])
            step["decisions"] = sorted(stage.get("decisions") or {})
            step["reason_required"] = list(stage.get("reason_required") or [])
        if not status or step["status"] == status:
            out.append(step)
    out.sort(key=lambda s: s["updated_at"], reverse=True)
    return out[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", action="store_true", help="seed declared templates")
    parser.add_argument("--list", action="store_true", help="list decision steps")
    parser.add_argument("--status", default="", help="filter --list by status")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.seed:
        out: Any = seed_declared_templates()
    elif args.list:
        out = list_steps(args.status)
    else:
        out = {tid: {"decides": t.get("decides"), "errors": validate_template(t)}
               for tid, t in load_templates().items()}
    print(json.dumps(out, indent=2, default=str) if args.json else out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
