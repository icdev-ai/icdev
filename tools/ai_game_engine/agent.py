# CUI // SP-CTI
"""AI Game Engine — agent wrapper with optional Chain of Thought / Chain of Debate routing.

If ICDEV_COD_ENABLED=true in the environment, inference routes through
GameDayChainBridge (CoT for strategy, CoD for debates).
Otherwise, falls back to the existing direct Ollama /api/chat pattern
used by tools.gameday.base_agent.GameDayAgent.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from .chain_bridge import GameDayChainBridge

log = get_logger(__name__)

# Re-use GameDay defaults for direct-Ollama fallback
from tools.gameday.constants import DEFAULT_AGENT_MODEL, OLLAMA_BASE_URL

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class AIGameAgent:
    """GameDay agent that optionally routes through ChainOrchestrator.

    Args:
        name: Human-readable agent name.
        role: Agent role (e.g. "bull", "bear", "neutral", "strategist").
        team_key: Team affiliation ("red", "blue", "gold", "green").
        system_prompt: System prompt for direct-Ollama mode.
        model: Ollama model tag for direct-Ollama mode.
        ollama_url: Ollama base URL for direct-Ollama mode.
        use_bridge: If True, route through GameDayChainBridge. If None,
            auto-detect from ICDEV_COD_ENABLED env var.
    """

    def __init__(
        self,
        name: str,
        role: str,
        team_key: str,
        system_prompt: str,
        model: str = DEFAULT_AGENT_MODEL,
        ollama_url: str = OLLAMA_BASE_URL,
        use_bridge: Optional[bool] = None,
    ):
        self.name = name
        self.role = role
        self.team_key = team_key
        self.system_prompt = system_prompt
        self.model = model
        self.ollama_url = ollama_url.rstrip("/")
        self.use_bridge = use_bridge if use_bridge is not None else _cod_enabled()
        self._bridge: Optional[GameDayChainBridge] = None

    # ──────────────────────────────────────────────────────────────────────────

    def run(
        self,
        user_prompt: str,
        scenario: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        tournament_id: Optional[int] = None,
        round_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run one inference call.

        Routes through GameDayChainBridge when use_bridge=True:
          - CoD (debate) for AI league roles: bull, bear, neutral
          - CoT (reason) for strategist / planner roles

        Otherwise performs a direct Ollama /api/chat call.
        """
        if self.use_bridge and scenario is not None:
            return self._run_via_bridge(scenario, user_prompt, context)
        return self._run_direct(user_prompt, context, tournament_id, round_id)

    # ──────────────────────────────────────────────────────────────────────────
    # Direct Ollama path (backward-compatible)
    # ──────────────────────────────────────────────────────────────────────────

    def _run_direct(
        self,
        user_prompt: str,
        context: Optional[Dict[str, Any]],
        tournament_id: Optional[int],
        round_id: Optional[int],
    ) -> Dict[str, Any]:
        if not HAS_REQUESTS:
            return self._unavailable("requests library not installed")

        context_str = ""
        if context:
            context_str = "\n\nContext from previous team members:\n" + json.dumps(context, indent=2)

        messages = [
            {"role": "user", "content": user_prompt + context_str},
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "system": self.system_prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 2048,
            },
        }

        t0 = time.time()
        latency_ms = 0
        prompt_tokens = 0
        completion_tokens = 0
        error_msg = None

        try:
            resp = _requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=480,
            )
            resp.raise_for_status()
            data = resp.json()
            latency_ms = int((time.time() - t0) * 1000)

            raw_content = data.get("message", {}).get("content", "")
            prompt_tokens = data.get("prompt_eval_count", 0) or 0
            completion_tokens = data.get("eval_count", 0) or 0

            parsed = self._parse_json_response(raw_content)

        except Exception as exc:
            latency_ms = int((time.time() - t0) * 1000)
            error_msg = str(exc)
            log.warning("[%s/%s] direct inference error: %s", self.team_key, self.role, exc)
            parsed = {"error": error_msg, "raw": ""}
            raw_content = ""

        if tournament_id is not None:
            _log_llmops(
                tournament_id=tournament_id,
                round_id=round_id,
                team_key=self.team_key,
                member_role=self.role,
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                error=error_msg,
            )

        return {
            "team_key": self.team_key,
            "member_role": self.role,
            "model": self.model,
            "parsed": parsed,
            "raw_content": raw_content if not error_msg else "",
            "tokens_used": prompt_tokens + completion_tokens,
            "latency_ms": latency_ms,
            "error": error_msg,
            "chain_mode": "direct",
        }

    # ──────────────────────────────────────────────────────────────────────────
    # ChainOrchestrator path
    # ──────────────────────────────────────────────────────────────────────────

    def _run_via_bridge(
        self,
        scenario: Dict[str, Any],
        user_prompt: str,
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if self._bridge is None:
            self._bridge = GameDayChainBridge()

        # Merge user_prompt into scenario description for richer context
        enriched = dict(scenario)
        if user_prompt:
            enriched["description"] = (
                enriched.get("description", "") + "\n\nAdditional context:\n" + user_prompt
            )
        if context:
            enriched["description"] = (
                enriched.get("description", "") + "\n\nPrior outputs:\n" + json.dumps(context, indent=2)
            )

        try:
            if self.role in ("bull", "bear", "neutral"):
                result = self._bridge.debate_strategy(
                    scenario=enriched,
                    system_prompt=self.system_prompt,
                )
                chain_mode = "cod"
            else:
                result = self._bridge.reason_strategy(
                    scenario=enriched,
                    system_prompt=self.system_prompt,
                )
                chain_mode = "cot"

            return {
                "team_key": self.team_key,
                "member_role": self.role,
                "model": ",".join(result.get("models_used", ["chain_orchestrator"])),
                "parsed": self._parse_json_response(result.get("judgment", "")),
                "raw_content": result.get("judgment", ""),
                "tokens_used": result.get("total_input_tokens", 0) + result.get("total_output_tokens", 0),
                "latency_ms": result.get("total_duration_ms", 0),
                "error": None,
                "chain_mode": chain_mode,
                "chain_metadata": {
                    "confidence": result.get("confidence", 0.0),
                    "trace_id": result.get("trace_id", ""),
                    "total_cost_usd": result.get("total_cost_usd", 0.0),
                    "stop_reason": result.get("stop_reason", ""),
                },
            }
        except Exception as exc:
            log.warning("[%s/%s] bridge inference error: %s", self.team_key, self.role, exc)
            return {
                "team_key": self.team_key,
                "member_role": self.role,
                "model": "chain_orchestrator",
                "parsed": {"error": str(exc)},
                "raw_content": "",
                "tokens_used": 0,
                "latency_ms": 0,
                "error": str(exc),
                "chain_mode": "bridge_error",
            }

    # ──────────────────────────────────────────────────────────────────────────

    def _parse_json_response(self, content: str) -> Dict[str, Any]:
        content = content.strip()
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

        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass

        return {"raw_text": content, "parse_failed": True}

    def _unavailable(self, reason: str) -> Dict[str, Any]:
        return {
            "team_key": self.team_key,
            "member_role": self.role,
            "model": self.model,
            "parsed": {"error": reason},
            "raw_content": "",
            "tokens_used": 0,
            "latency_ms": 0,
            "error": reason,
            "chain_mode": "unavailable",
        }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _cod_enabled() -> bool:
    return os.environ.get("ICDEV_COD_ENABLED", "").lower() in ("true", "1", "yes")


def _log_llmops(
    tournament_id: int,
    round_id: Optional[int],
    team_key: str,
    member_role: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    error: Optional[str],
) -> None:
    """Best-effort LLMOps logging."""
    try:
        from tools.gameday.db import log_llmops_event
        log_llmops_event(
            tournament_id=tournament_id,
            round_id=round_id,
            team_key=team_key,
            member_role=member_role,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            error=error,
        )
    except Exception:
        pass
