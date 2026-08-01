# CUI // SP-CTI
"""OPT-71: GitHub Copilot CLI adapter — STUB.

Ships as a stub so the registry shape is stable. Implementation is
deferred until a real user wires `gh copilot` into the kanban loop.
"""
from __future__ import annotations

import shutil
from typing import Any, Dict

from tools.agents.adapter_base import (
    AgentResult,
    AgentSession,
    NotInstalledError,
)


class CopilotCliAdapter:
    name = "copilot_cli"

    def available(self) -> bool:
        # `gh copilot` requires gh plus the extension — report False
        # until implementation lands.
        return False and (shutil.which("gh") is not None)

    def prepare_prompt(self, session: AgentSession) -> str:
        return session.prompt

    def invoke(self, session: AgentSession) -> AgentResult:
        raise NotInstalledError(
            "copilot_cli adapter is a stub — not yet wired to a backend"
        )

    def detect_completion(self, output: str) -> bool:
        return False

    def parse_response(self, raw: str) -> Dict[str, Any]:
        return {"content": raw or "", "tool_calls": [], "diff": ""}


ADAPTER = CopilotCliAdapter()
