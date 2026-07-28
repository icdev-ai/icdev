# CUI // SP-CTI
"""Bridge Studio approval gates onto workflow_hitl external steps (dwo-dur-04).

Direction (a) of the DWO spike: Studio is a *producer* of ``wf_external_steps``
rows so ``tools/workflow_hitl`` remains the single reviewer inbox. Studio does
not reimplement notification routing, teams, templates or the webhook-token
completion path — it registers its gate with workflow_hitl and lets that
surface own the review.

Linkage carries no new schema: ``wf_external_steps.external_ref`` holds the
Studio ``step_run_id``, and ``external_system`` is ``'studio'``. That gives a
lookup in both directions off columns that already exist.

Exactly-once is enforced two ways:

1. A re-entrancy guard (``_bridging``) so releasing one surface never loops back
   into the surface that initiated the decision.
2. Terminal-status checks on both sides — a second decision on a decided gate is
   rejected and audited rather than applied twice.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# The external_system marker that identifies a Studio-produced external step.
EXTERNAL_SYSTEM = "studio"

# wf_external_steps.step_type is CHECK-constrained to
# ('manual','external_email','external_ticket','external_wiki'). A Studio gate
# is a human decision, so it rides the existing 'manual' type rather than
# widening the constraint.
STEP_TYPE = "manual"

# A single system template + one shadow instance per Studio run satisfy the
# wf_instances/wf_templates foreign keys without inventing a second engine.
TEMPLATE_ID = "wft-studio-gate"
TEMPLATE_NAME = "Studio Workflow Gate"

# Statuses after which a wf_external_steps row must not be decided again.
TERMINAL_STATUSES = ("completed", "failed", "timed_out")

# step_run_ids currently being bridged, so approve-on-one-side does not recurse
# back through the other side.
_bridging: set[str] = set()
_bridging_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _instance_id(run_id: str) -> str:
    return f"wfi-studio-{run_id}"


def _audit(action: str, actor: str, details: dict, approved: bool = True) -> None:
    """Write the gate decision to the one shared append-only audit trail."""
    try:
        from tools.audit.audit_logger import log_event  # noqa: PLC0415

        log_event(
            event_type="code_approved" if approved else "code_rejected",
            actor=actor,
            action=action,
            details=details,
        )
    except Exception as exc:  # audit must never break the gate
        logger.warning("Gate audit write failed for %s: %s", action, exc)


# ── Shadow template / instance ─────────────────────────────

def _ensure_instance(run_id: str, project_id: str, step_name: str) -> bool:
    """Create the wf_templates/wf_instances rows a Studio gate hangs off.

    Idempotent: the template is shared by every Studio run, the instance is
    per-run. Returns False when workflow_hitl tables are not present.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM wf_templates WHERE id=%s", (TEMPLATE_ID,)
        ).fetchone()
        if not row:
            conn.execute(
                """INSERT INTO wf_templates
                   (id, name, canvas_type, stages_json, roles_json,
                    approval_policy, is_system, created_by, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (TEMPLATE_ID, TEMPLATE_NAME, "studio",
                 json.dumps(["review"]), json.dumps(["approver"]),
                 "any_one", 1, "studio", _now(), _now()),
            )

        row = conn.execute(
            "SELECT id FROM wf_instances WHERE id=%s", (_instance_id(run_id),)
        ).fetchone()
        if not row:
            conn.execute(
                """INSERT INTO wf_instances
                   (id, template_id, task_id, project_id, canvas_type,
                    current_stage, status, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (_instance_id(run_id), TEMPLATE_ID, run_id, project_id, "studio",
                 step_name or "review", "active", _now(), _now()),
            )
        conn.commit()
        return True
    except Exception as exc:
        logger.warning("Could not register Studio run %s with workflow_hitl: %s", run_id, exc)
        return False
    finally:
        conn.close()


# ── Studio → workflow_hitl ─────────────────────────────────

def open_gate(
    run_id: str,
    step_run_id: str,
    step_name: str,
    role: str = "approver",
    project_id: str = "default",
) -> str | None:
    """Publish a paused Studio gate into the workflow_hitl reviewer inbox.

    Returns the wf_external_steps id, or None when workflow_hitl is unavailable
    (Studio then falls back to its own Details-modal gate, unchanged).
    """
    existing = find_external_step(step_run_id)
    if existing:
        return existing["id"]

    if not _ensure_instance(run_id, project_id, step_name):
        return None

    try:
        from tools.workflow_hitl import external_steps  # noqa: PLC0415

        step_id = external_steps.create(
            _instance_id(run_id),
            step_name or step_run_id,
            STEP_TYPE,
            {
                "system": EXTERNAL_SYSTEM,
                "studio_run_id": run_id,
                "studio_step_run_id": step_run_id,
                "role": role,
            },
        )
    except Exception as exc:
        logger.warning("Could not open HITL external step for %s: %s", step_run_id, exc)
        return None

    # external_ref is the back-pointer workflow_hitl uses to find the Studio
    # gate; 'sent' puts the row in the /wf/external reviewer inbox.
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE wf_external_steps SET external_ref=%s, status='sent', "
            "notified_at=%s, updated_at=%s WHERE id=%s",
            (step_run_id, _now(), _now(), step_id),
        )
        conn.commit()
    finally:
        conn.close()

    _audit(
        "studio_gate_opened",
        actor="studio",
        details={"run_id": run_id, "step_run_id": step_run_id,
                 "external_step_id": step_id, "role": role},
    )
    return step_id


def find_external_step(step_run_id: str) -> dict | None:
    """Return the wf_external_steps row bridged to a Studio step_run_id."""
    try:
        conn = get_connection()
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM wf_external_steps WHERE external_ref=%s AND external_system=%s",
            (step_run_id, EXTERNAL_SYSTEM),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def complete_external_step(step_run_id: str, decision: str, actor: str) -> bool:
    """Studio decided — close out the mirrored workflow_hitl external step.

    Returns True when the external step transitioned; False when there was no
    bridged step or it had already been decided (double-approval, audited).
    """
    with _bridging_lock:
        if step_run_id in _bridging:
            return False          # this decision came *from* workflow_hitl
        _bridging.add(step_run_id)
    try:
        step = find_external_step(step_run_id)
        if not step:
            return False
        if step.get("status") in TERMINAL_STATUSES:
            _audit(
                "studio_gate_duplicate_decision_rejected",
                actor=actor,
                details={"step_run_id": step_run_id, "external_step_id": step["id"],
                         "existing_status": step.get("status"), "attempted": decision},
                approved=False,
            )
            return False

        from tools.workflow_hitl import external_steps  # noqa: PLC0415

        ok = external_steps.mark_complete(step["id"], completed_by=actor)
        if ok:
            _audit(
                f"studio_gate_{decision}",
                actor=actor,
                details={"step_run_id": step_run_id, "external_step_id": step["id"],
                         "source": "studio"},
                approved=(decision == "approved"),
            )
        return ok
    except Exception as exc:
        logger.warning("Could not complete external step for %s: %s", step_run_id, exc)
        return False
    finally:
        with _bridging_lock:
            _bridging.discard(step_run_id)


# ── workflow_hitl → Studio ─────────────────────────────────

def release_studio_gate(step: dict, actor: str) -> bool:
    """A Studio-bridged external step completed in workflow_hitl — release the gate.

    Called from ``external_steps.mark_complete``. Returns True when the Studio
    run gate was released.
    """
    step_run_id = step.get("external_ref")
    if not step_run_id:
        return False

    with _bridging_lock:
        if step_run_id in _bridging:
            return False          # this decision came *from* Studio
        _bridging.add(step_run_id)
    try:
        from tools.studio import workflow_runner  # noqa: PLC0415

        released = workflow_runner.approve_step(step_run_id, actor=actor)
        _audit(
            "studio_gate_approved" if released else "studio_gate_release_noop",
            actor=actor,
            details={"step_run_id": step_run_id, "external_step_id": step.get("id"),
                     "source": "workflow_hitl"},
            approved=released,
        )
        return released
    except Exception as exc:
        logger.warning("Could not release Studio gate %s: %s", step_run_id, exc)
        return False
    finally:
        with _bridging_lock:
            _bridging.discard(step_run_id)


def is_studio_step(step: dict) -> bool:
    return step.get("external_system") == EXTERNAL_SYSTEM
