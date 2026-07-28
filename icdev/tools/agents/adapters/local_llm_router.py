# CUI // SP-CTI
"""OPT-71: Local LLMRouter adapter.

Runs the agent session through tools.llm.router.LLMRouter instead of
shelling out to a CLI. This is the air-gap fallback for any consumer
that doesn't have the Claude Code CLI installed.

Unlike the kanban LocalPythonTaskExecutor (OPT-31), this adapter does
NOT thread — it runs synchronously inside `invoke()` and returns the
full result. Consumers that need async dispatch should wrap it.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from tools.agents.adapter_base import (
    AgentResult,
    AgentSession,
    NotInstalledError,
)


class LocalLlmRouterAdapter:
    name = "local_llm_router"

    def available(self) -> bool:
        try:
            from tools.llm.router import LLMRouter
            router = LLMRouter()
            if hasattr(router, "is_no_llm_mode") and router.is_no_llm_mode():
                return False
            # Cheap probe: is there ANY chain for code_generation?
            chain = router._get_chain_for_function("code_generation")  # noqa: SLF001
            return bool(chain)
        except Exception:
            return False

    def prepare_prompt(self, session: AgentSession) -> str:
        return session.prompt

    def invoke(self, session: AgentSession) -> AgentResult:
        if not self.available():
            raise NotInstalledError(
                "LLMRouter has no available chain for code_generation"
            )
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        system = session.system_prompt or (
            "You are an autonomous task executor for the ICDEV™ "
            "kanban system. Produce a detailed plan + result; describe "
            "exactly what a downstream tool or human should apply."
        )
        request = LLMRequest(
            messages=[{"role": "user", "content": session.prompt}],
            system_prompt=system,
            max_tokens=session.max_tokens,
            temperature=0.3,
            skip_injection_scan=True,
            agent_id=f"adapter:{self.name}",
            project_id=session.task_id,
        )

        t0 = time.time()
        try:
            response = router.invoke("code_generation", request)
            dur = int((time.time() - t0) * 1000)
            content = response.content or ""
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=self.detect_completion(content),
                exit_code=0,
                output=content,
                duration_ms=dur,
                structured={
                    "provider": getattr(response, "provider", ""),
                    "model_id": getattr(response, "model_id", ""),
                    "input_tokens": getattr(response, "input_tokens", 0),
                    "output_tokens": getattr(response, "output_tokens", 0),
                },
            )
        except Exception as exc:
            dur = int((time.time() - t0) * 1000)
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=False,
                exit_code=1,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=dur,
            )

    def detect_completion(self, output: str) -> bool:
        return bool(output and len(output.strip()) >= 200)

    def parse_response(self, raw: str) -> Dict[str, Any]:
        return {"content": raw or "", "tool_calls": [], "diff": ""}


ADAPTER = LocalLlmRouterAdapter()
