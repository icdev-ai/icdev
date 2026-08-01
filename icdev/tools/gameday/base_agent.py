# CUI // SP-CTI
"""AI GameDay League — base agent backed by the ICDEV LLM router.

All 16 team members inherit from GameDayAgent. Inference is routed through
tools.llm.router (LLMRouter.get_provider_for_function + provider.invoke),
mirroring tools/gameday/judge_agent.py::_run_lens. The model is resolved from
args/llm_config.yaml / .env by the router — never hardcoded here — so the same
code runs against Bedrock, Anthropic, Ollama, Gemini, etc. without change.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import time
from typing import Any

from .constants import DEFAULT_AGENT_MODEL, GAMEDAY_LLM_FUNCTION, OLLAMA_BASE_URL
from .db import log_llmops_event

log = get_logger(__name__)


class GameDayAgent:
    """Single team member agent. Inference goes through the ICDEV LLM router."""

    def __init__(
        self,
        name: str,
        role: str,
        team_key: str,
        specialty: str,
        system_prompt: str,
        model: str = DEFAULT_AGENT_MODEL,
        ollama_url: str = OLLAMA_BASE_URL,
        time_budget_seconds: int = 480,  # 8 min default
    ):
        self.name = name
        self.role = role
        self.team_key = team_key
        self.specialty = specialty
        self.system_prompt = system_prompt
        # ``model`` is an optional per-member override resolved from config
        # (may be empty); the router resolves the concrete model otherwise.
        self.model = model or ""
        # Retained for backward compatibility; routing is now handled by the
        # LLM router, so this URL is no longer used for the call itself.
        self.ollama_url = (ollama_url or "").rstrip("/")
        self.time_budget_seconds = time_budget_seconds

    # ──────────────────────────────────────────────────────────────────────────

    def run(
        self,
        user_prompt: str,
        context: dict | None = None,
        tournament_id: int | None = None,
        round_id: int | None = None,
        agent_loop_content: str | None = None,
    ) -> dict[str, Any]:
        """Run one inference call. Returns parsed JSON dict + metadata.

        When ``agent_loop_content`` is supplied (from an upstream budget-guarded
        agent loop), it is parsed directly and no LLM call is made.
        """
        context_str = ""
        if context:
            context_str = "\n\nContext from previous team members:\n" + json.dumps(context, indent=2)

        # Fast path: reuse content already produced by the agent loop.
        if agent_loop_content:
            parsed = self._parse_json_response(agent_loop_content)
            if tournament_id is not None:
                log_llmops_event(
                    tournament_id=tournament_id,
                    round_id=round_id,
                    team_key=self.team_key,
                    member_role=self.role,
                    model=self.model or "agent_loop",
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=0,
                    error=None,
                )
            return {
                "team_key":    self.team_key,
                "member_role": self.role,
                "model":       self.model or "agent_loop",
                "parsed":      parsed,
                "raw_content": agent_loop_content,
                "tokens_used": 0,
                "latency_ms":  0,
                "error":       None,
            }

        t0 = time.time()
        latency_ms = 0
        prompt_tokens = 0
        completion_tokens = 0
        error_msg = None
        raw_content = ""
        effective_model = self.model or "router"

        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

            router = LLMRouter()
            provider, model_id, cfg = router.get_provider_for_function(GAMEDAY_LLM_FUNCTION)
            if provider is None:
                return self._unavailable("no LLM provider available from router")

            effective_model = self.model or model_id or "router"
            req = LLMRequest(
                messages=[{"role": "user", "content": user_prompt + context_str}],
                system_prompt=self.system_prompt,
                max_tokens=2048,
                temperature=0.7,
                skip_injection_scan=True,
            )
            resp = provider.invoke(req, model_id, cfg)
            latency_ms = int((time.time() - t0) * 1000)

            raw_content = (getattr(resp, "content", "") or "")
            prompt_tokens = int(getattr(resp, "input_tokens", 0) or 0)
            completion_tokens = int(getattr(resp, "output_tokens", 0) or 0)
            effective_model = getattr(resp, "model_id", None) or effective_model

            parsed = self._parse_json_response(raw_content)

        except Exception as exc:
            latency_ms = int((time.time() - t0) * 1000)
            error_msg = str(exc)
            log.warning("[%s/%s] inference error: %s", self.team_key, self.role, exc)
            parsed = {"error": error_msg, "raw": ""}
            raw_content = ""

        if tournament_id is not None:
            log_llmops_event(
                tournament_id=tournament_id,
                round_id=round_id,
                team_key=self.team_key,
                member_role=self.role,
                model=effective_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                error=error_msg,
            )

        return {
            "team_key":          self.team_key,
            "member_role":       self.role,
            "model":             effective_model,
            "parsed":            parsed,
            "raw_content":       raw_content if not error_msg else "",
            "tokens_used":       prompt_tokens + completion_tokens,
            "latency_ms":        latency_ms,
            "error":             error_msg,
        }

    # ──────────────────────────────────────────────────────────────────────────

    def _parse_json_response(self, content: str) -> dict:
        """Extract JSON from model response, tolerating markdown fences."""
        content = content.strip()
        # Strip markdown fence if present
        if content.startswith("```"):
            lines = content.splitlines()
            inner = []
            in_fence = False
            for line in lines:
                if line.startswith("```") and not in_fence:
                    in_fence = True
                    continue
                if line.startswith("```") and in_fence:
                    break
                if in_fence:
                    inner.append(line)
            content = "\n".join(inner)

        # Find first { ... } block
        start = content.find("{")
        end   = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass

        # Fallback: return as raw text payload
        return {"raw_text": content, "parse_failed": True}

    def _unavailable(self, reason: str) -> dict:
        return {
            "team_key":    self.team_key,
            "member_role": self.role,
            "model":       self.model or "router",
            "parsed":      {"error": reason},
            "raw_content": "",
            "tokens_used": 0,
            "latency_ms":  0,
            "error":       reason,
        }
