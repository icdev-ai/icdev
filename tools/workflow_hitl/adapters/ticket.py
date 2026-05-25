# CUI // SP-CTI
"""Ticket adapter — routes to Jira / ServiceNow / GitHub Issues strategy."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger


from tools.workflow_hitl.adapters.base import ExternalStepAdapter

logger = get_logger(__name__)


class TicketAdapter(ExternalStepAdapter):

    def __init__(self, system: str = "jira"):
        self.system = system.lower()

    def _strategy(self):
        if self.system == "jira":
            from tools.workflow_hitl.adapters.strategies.jira import JiraStrategy
            return JiraStrategy()
        if self.system == "servicenow":
            from tools.workflow_hitl.adapters.strategies.servicenow import ServiceNowStrategy
            return ServiceNowStrategy()
        if self.system == "github":
            from tools.workflow_hitl.adapters.strategies.github import GitHubStrategy
            return GitHubStrategy()
        raise ValueError(f"Unknown ticket system: {self.system!r}")

    def send(self, step: dict) -> str:
        return self._strategy().create_ticket(step)

    def poll_status(self, step: dict) -> bool:
        return self._strategy().is_closed(step)
