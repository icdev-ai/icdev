# CUI // SP-CTI
"""OPT-71: OpenAI Codex CLI adapter — STUB.

Ships as a stub so the registry shape is stable. Implementation is
deferred until a real user wires OpenAI Codex into the kanban loop.
"""
from __future__ import annotations

import shutil
from typing import Any, Dict

from tools.agents.adapter_base import (
    AgentResult,
    AgentSession,
    NotInstalledError,
)


class CodexCliAdapter:
    name = "codex_cli"

    def available(self) -> bool:
        return shutil.which("openai-codex") is not None

    def prepare_prompt(self, session: AgentSession) -> str:
        return session.prompt

    def invoke(self, session: AgentSession) -> AgentResult:
        raise NotInstalledError(
            "codex_cli adapter is a stub — not yet wired to a backend"
        )

    def detect_completion(self, output: str) -> bool:
        return False

    def parse_response(self, raw: str) -> Dict[str, Any]:
        return {"content": raw or "", "tool_calls": [], "diff": ""}


ADAPTER = CodexCliAdapter()
