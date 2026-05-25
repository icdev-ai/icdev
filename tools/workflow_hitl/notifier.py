# CUI // SP-CTI
"""HITL Workflow notifications — wraps tools/notifications/gateway.py."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging

logger = get_logger(__name__)


def _gateway():
    try:
        from tools.notifications.gateway import NotificationGateway
        return NotificationGateway()
    except Exception:
        return None


def _audit(event_type: str, **kwargs):
    try:
        from tools.audit.audit_logger import log_event
        log_event(event_type=event_type, **kwargs)
    except Exception:
        pass


def send_review_request(approval_id: str, team: dict):
    """Notify all team members with reviewer role that a review is pending."""
    gw = _gateway()
    members = [m for m in team.get("members", []) if m.get("role_label") in ("reviewer", "manager")]
    for member in members:
        msg = (
            f"Review requested — Approval gate {approval_id} is waiting for your review. "
            f"Team: {team.get('name')}"
        )
        if gw:
            try:
                gw.send(channel="email", recipient=member.get("user_id"), subject="HITL Review Requested", body=msg)
            except Exception as exc:
                logger.warning("Review notification failed for %s: %s", member.get("user_id"), exc)
    _audit("approval_requested", approval_id=approval_id, team_id=team.get("id"))
    logger.info("Review request sent for approval %s to %d members", approval_id, len(members))


def send_kickback_notice(instance_id: str, reason: str, submitted_by: str):
    """Notify assignee + team that an instance was kicked back for revision."""
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        inst = conn.execute("SELECT * FROM wf_instances WHERE id=%s", (instance_id,)).fetchone()
        if not inst:
            return
        inst = dict(inst)
        approval = conn.execute(
            "SELECT * FROM wf_approvals WHERE instance_id=%s ORDER BY created_at DESC LIMIT 1",
            (instance_id,),
        ).fetchone()
        team_id = approval["team_id"] if approval else inst.get("team_id")
    finally:
        conn.close()

    gw = _gateway()
    msg = f"HITL Kickback — Instance {instance_id} has been sent back for revision.\nReason: {reason}"
    if gw and team_id:
        from tools.workflow_hitl import team_manager
        try:
            team = team_manager.get_team(team_id)
            if team:
                for member in team.get("members", []):
                    try:
                        gw.send(channel="email", recipient=member.get("user_id"),
                                subject="HITL Kickback — Revision Required", body=msg)
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Kickback notification error: %s", exc)

    _audit("approval_kickback", instance_id=instance_id, reason=reason, submitted_by=submitted_by)
    logger.info("Kickback notice sent for instance %s", instance_id)


def send_approval_granted(instance_id: str):
    """Notify task owner + team that the workflow is fully approved."""
    gw = _gateway()
    msg = f"HITL Approval Granted — Workflow instance {instance_id} has been fully approved."
    if gw:
        try:
            gw.send(channel="system", subject="HITL Approved", body=msg)
        except Exception as exc:
            logger.warning("Approval granted notification failed: %s", exc)
    _audit("approval_granted", instance_id=instance_id)
    logger.info("Approval granted notice sent for instance %s", instance_id)


def send_escalation(instance_id: str, reason: str):
    """Notify manager-role members of escalation."""
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        approval = conn.execute(
            "SELECT * FROM wf_approvals WHERE instance_id=%s ORDER BY created_at DESC LIMIT 1",
            (instance_id,),
        ).fetchone()
        team_id = approval["team_id"] if approval else None
    finally:
        conn.close()

    gw = _gateway()
    msg = f"HITL Escalation — Instance {instance_id} has been escalated.\nReason: {reason}"
    if gw and team_id:
        from tools.workflow_hitl import team_manager
        try:
            team = team_manager.get_team(team_id)
            managers = [m for m in (team or {}).get("members", []) if m.get("role_label") == "manager"]
            for m in managers:
                try:
                    gw.send(channel="email", recipient=m.get("user_id"),
                            subject="HITL Escalation", body=msg)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Escalation notification error: %s", exc)

    _audit("approval_escalated", instance_id=instance_id, reason=reason)
    logger.info("Escalation notice sent for instance %s", instance_id)
