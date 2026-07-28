# CUI // SP-CTI
"""External step lifecycle — create, send, poll, mark_complete."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_webhook_token(step_id: str) -> str:
    secret = os.getenv("WF_WEBHOOK_SECRET", "icdev-wf-secret")
    return hmac.new(secret.encode(), step_id.encode(), hashlib.sha256).hexdigest()


def create(
    instance_id: str,
    stage_name: str,
    step_type: str,
    stage_config: dict,
) -> str:
    step_id = f"wfes-{uuid.uuid4().hex[:12]}"
    token = _generate_webhook_token(step_id)
    payload = json.dumps({k: v for k, v in stage_config.items() if k != "step_type"})

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wf_external_steps
               (id, instance_id, stage_name, step_type, external_system, webhook_token,
                status, payload_json, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (step_id, instance_id, stage_name, step_type,
             stage_config.get("system"), token, "pending", payload, _now(), _now()),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    return step_id


def send(step_id: str) -> str | None:
    """Dispatch the external step to the right adapter. Returns external_ref."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM wf_external_steps WHERE id=%s", (step_id,)).fetchone()
        if not row:
            return None
        step = dict(row)
    finally:
        conn.close()

    try:
        from tools.workflow_hitl.adapters import get_adapter
        adapter = get_adapter(step["step_type"], step.get("external_system"))
        external_ref = adapter.send(step)
    except Exception as exc:
        logger.warning("External step send failed for %s: %s", step_id, exc)
        _update_step(step_id, status="failed")
        return None

    _update_step(step_id, status="sent", external_ref=external_ref, notified_at=_now())

    # Mark instance as waiting_external
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE wf_instances SET status='waiting_external', updated_at=%s WHERE id=%s",
            (_now(), step["instance_id"]),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    return external_ref


def mark_complete(step_id: str, completed_by: str) -> bool:
    """Mark an external step as completed and advance the workflow instance."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM wf_external_steps WHERE id=%s", (step_id,)).fetchone()
        if not row:
            return False
        step = dict(row)
        instance_id = step["instance_id"]
    finally:
        conn.close()

    # A step that has already been decided must not be decided twice — the
    # reviewer inbox and the Studio Details modal are two doors onto the same
    # gate (dwo-dur-04).
    if step.get("status") in ("completed", "failed", "timed_out"):
        logger.warning(
            "Rejected duplicate decision on external step %s (status=%s)",
            step_id, step.get("status"),
        )
        return False

    _update_step(step_id, status="completed", completed_at=_now(), completed_by=completed_by)

    # A Studio-produced gate has no wf_ stages to advance — release the paused
    # Studio run instead, then close out its shadow instance.
    try:
        from tools.studio import gate_bridge
        if gate_bridge.is_studio_step(step):
            gate_bridge.release_studio_gate(step, completed_by)
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE wf_instances SET status='approved', updated_at=%s WHERE id=%s",
                    (_now(), instance_id),
                )
                try:
                    conn.commit()
                except Exception:
                    pass
            finally:
                conn.close()
            return True
    except Exception as exc:
        logger.warning("Studio gate release failed for %s: %s", step_id, exc)

    # Restore instance status to active and advance
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE wf_instances SET status='active', updated_at=%s WHERE id=%s",
            (_now(), instance_id),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    try:
        from tools.workflow_hitl.engine import WorkflowEngine
        WorkflowEngine().advance_stage(instance_id)
    except Exception as exc:
        logger.warning("advance_stage failed after external step complete: %s", exc)

    return True


def check_all_pending() -> int:
    """Called by Genesis reflex wf_ext_poller.

    Polls all 'sent'/'waiting' external steps; marks complete if done.
    Escalates instances that exceeded max_wait_hours.
    Returns number of steps resolved.
    """
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "..", "args",
                                "workflow_hitl_integrations.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            poll_cfg = yaml.safe_load(f).get("polling", {})
    except Exception:
        poll_cfg = {}

    max_wait_hours = poll_cfg.get("max_wait_hours", 72)

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_external_steps WHERE status IN ('sent','waiting')"
        ).fetchall()
        steps = [dict(r) for r in rows]
    finally:
        conn.close()

    resolved = 0
    for step in steps:
        # Check timeout
        try:
            from datetime import datetime, timezone
            created = datetime.fromisoformat(step["created_at"].replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
            if age_hours > max_wait_hours:
                _update_step(step["id"], status="timed_out")
                from tools.workflow_hitl.engine import WorkflowEngine
                WorkflowEngine().escalate(step["instance_id"], reason=f"External step timed out after {age_hours:.0f}h")
                continue
        except Exception:
            pass

        # Poll adapter
        try:
            from tools.workflow_hitl.adapters import get_adapter
            adapter = get_adapter(step["step_type"], step.get("external_system"))
            if adapter.poll_status(step):
                mark_complete(step["id"], "poll")
                resolved += 1
        except Exception as exc:
            logger.warning("Poll failed for step %s: %s", step["id"], exc)

    return resolved


def verify_webhook_token(token: str, step_id: str) -> bool:
    expected = _generate_webhook_token(step_id)
    return hmac.compare_digest(expected, token)


def _update_step(step_id: str, **kwargs):
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=%s" for k in kwargs)
    vals = list(kwargs.values()) + [step_id]
    conn = get_connection()
    try:
        conn.execute(f"UPDATE wf_external_steps SET {set_clause} WHERE id=%s", vals)
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
