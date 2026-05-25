# CUI // SP-CTI
"""Wiki adapter — routes to Confluence / SharePoint strategy."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger


from tools.workflow_hitl.adapters.base import ExternalStepAdapter

logger = get_logger(__name__)


class WikiAdapter(ExternalStepAdapter):

    def __init__(self, system: str = "confluence"):
        self.system = system.lower()

    def _strategy(self):
        if self.system == "confluence":
            from tools.workflow_hitl.adapters.strategies.confluence import ConfluenceStrategy
            return ConfluenceStrategy()
        if self.system == "sharepoint":
            from tools.workflow_hitl.adapters.strategies.sharepoint import SharePointStrategy
            return SharePointStrategy()
        raise ValueError(f"Unknown wiki system: {self.system!r}")

    def send(self, step: dict) -> str:
        return self._strategy().create_page(step)

    def poll_status(self, step: dict) -> bool:
        return self._strategy().is_approved(step)
