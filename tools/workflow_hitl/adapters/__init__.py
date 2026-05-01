# CUI // SP-CTI
"""External step adapter registry — maps (step_type, system) → adapter class."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.workflow_hitl.adapters.base import ExternalStepAdapter


def get_adapter(step_type: str, system: str | None = None) -> "ExternalStepAdapter":
    """Return the right adapter instance for a given step_type + system combination."""
    if step_type == "external_email":
        from tools.workflow_hitl.adapters.email import EmailAdapter
        return EmailAdapter()
    if step_type == "external_ticket":
        from tools.workflow_hitl.adapters.ticket import TicketAdapter
        return TicketAdapter(system or "jira")
    if step_type == "external_wiki":
        from tools.workflow_hitl.adapters.wiki import WikiAdapter
        return WikiAdapter(system or "confluence")
    # manual steps don't need an adapter — handled in-app
    raise ValueError(f"No adapter for step_type={step_type!r}, system={system!r}")
