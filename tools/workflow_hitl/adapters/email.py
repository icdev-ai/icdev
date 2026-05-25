# CUI // SP-CTI
"""Email external step adapter — SMTP via tools/notifications/adapters/email_adapter.py."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os

from tools.workflow_hitl.adapters.base import ExternalStepAdapter

logger = get_logger(__name__)


class EmailAdapter(ExternalStepAdapter):

    def send(self, step: dict) -> str:
        """Send a workflow email notification.

        Returns a pseudo message-id for tracking (SMTP adapters may return the real one).
        """
        payload = self.build_payload(step)
        template_name = payload.get("template", "review_kickoff")
        recipients = payload.get("recipients", [])

        body = self._render_template(template_name, step, payload)

        message_id = f"wf-email-{step['id']}"
        for recipient in recipients:
            try:
                self._send_smtp(recipient, f"Workflow Review — {step.get('stage_name','')}", body)
                logger.info("WF email sent to %s for step %s", recipient, step["id"])
            except Exception as exc:
                logger.warning("WF email failed to %s: %s", recipient, exc)

        return message_id

    def poll_status(self, step: dict) -> bool:
        # Email steps require manual confirmation (click 'mark done' in HITL queue)
        return False

    def _render_template(self, template_name: str, step: dict, payload: dict) -> str:
        """Render an email template from context/workflow_email_templates/."""
        tpl_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "context", "workflow_email_templates"
        )
        tpl_path = os.path.join(tpl_dir, f"{template_name}.html")
        try:
            with open(tpl_path, encoding="utf-8") as f:
                template = f.read()
            from jinja2 import Template
            return Template(template).render(step=step, payload=payload)
        except Exception:
            return (
                f"Workflow review requested for stage '{step.get('stage_name')}'.\n"
                f"Instance: {step.get('instance_id')}\n"
                f"Please log in to ICDEV to review."
            )

    def _send_smtp(self, recipient: str, subject: str, body: str):
        try:
            from tools.notifications.adapters.email_adapter import send as email_send
            email_send(to=recipient, subject=subject, body=body, html=True)
        except ImportError:
            # Fallback: log only
            logger.info("[SMTP FALLBACK] To: %s | Subject: %s | Body length: %d",
                        recipient, subject, len(body))
