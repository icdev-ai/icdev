# CUI // SP-CTI
"""AI Game Engine — Chain of Thought / Chain of Debate bridge for GameDay.

Wraps ChainOrchestrator with GameDay-specific positions and prompts.
Use CoD for AI league debates (Bull vs Bear vs Neutral).
Use CoT for step-by-step strategy generation.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from typing import Any, Dict, List, Optional

from tools.llm.chain_orchestrator import ChainOrchestrator, ChainResult
from tools.llm.provider import LLMRequest
from tools.llm.router import LLMRouter

log = get_logger(__name__)


class GameDayChainBridge:
    """Wraps ChainOrchestrator for GameDay-specific CoT/CoD workflows."""

    def __init__(self, router: Optional[LLMRouter] = None) -> None:
        self.router = router or LLMRouter()
        self.orchestrator = ChainOrchestrator(router=self.router)

    # ──────────────────────────────────────────────────────────────────────────
    # Chain of Debate — AI league debates (Bull vs Bear vs Neutral)
    # ──────────────────────────────────────────────────────────────────────────

    def debate_strategy(
        self,
        scenario: Dict[str, Any],
        positions: Optional[List[str]] = None,
        system_prompt: str = "",
    ) -> Dict[str, Any]:
        """Run a Chain of Debate over a scenario and return the judged strategy.

        Args:
            scenario: GameDay scenario dict with at least "name" and "description".
            positions: Override debater positions. Defaults to Bull / Bear / Neutral.
            system_prompt: Optional system prompt override.

        Returns:
            Dict with keys:
                - judgment: str — final judged strategy
                - confidence: float
                - transcript: List[Dict] — per-round debate history
                - models_used: List[str]
                - total_cost_usd: float
                - total_duration_ms: int
        """
        prompt = _format_scenario_prompt(scenario)
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt or _DEFAULT_DEBATE_SYSTEM,
            max_tokens=2048,
            temperature=0.7,
            skip_injection_scan=True,
        )

        # Inject custom positions into orchestrator config via chain_config
        if positions:
            req.chain_config = {"debate_positions": positions}

        result = self.orchestrator.invoke_chain_of_debate("gameday_debate", req)
        return _chain_result_to_dict(result)

    # ──────────────────────────────────────────────────────────────────────────
    # Chain of Thought — step-by-step strategy generation
    # ──────────────────────────────────────────────────────────────────────────

    def reason_strategy(
        self,
        scenario: Dict[str, Any],
        system_prompt: str = "",
    ) -> Dict[str, Any]:
        """Run Chain of Thought to produce a step-by-step strategy plan.

        Args:
            scenario: GameDay scenario dict.
            system_prompt: Optional system prompt override.

        Returns:
            Dict with keys:
                - plan: str — final synthesized strategy
                - reasoning: str — raw step-by-step reasoning
                - confidence: float
                - rounds: List[Dict] — CoT round telemetry
                - models_used: List[str]
                - total_cost_usd: float
                - total_duration_ms: int
        """
        prompt = _format_scenario_prompt(scenario)
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt or _DEFAULT_REASON_SYSTEM,
            max_tokens=2048,
            temperature=0.6,
            skip_injection_scan=True,
        )

        result = self.orchestrator.invoke_chain_of_thought("gameday_reason", req)
        out = _chain_result_to_dict(result)
        # Surface raw reasoning for inspection
        reasoning = ""
        for rnd in result.rounds:
            if rnd.get("step") == "reason":
                reasoning = rnd.get("content", "")
                break
        out["reasoning"] = reasoning
        out["plan"] = out.get("judgment", out.get("content", ""))
        return out


# ── Helpers ──────────────────────────────────────────────────────────────────

_DEFAULT_DEBATE_SYSTEM = (
    "You are participating in a structured debate about cyber-security strategy. "
    "Argue your assigned position strongly but honestly. Cite reasoning and evidence."
)

_DEFAULT_REASON_SYSTEM = (
    "You are a strategic planning assistant for cyber-security wargames. "
    "Think step by step before producing the final strategy plan. "
    "Number each step clearly."
)


def _format_scenario_prompt(scenario: Dict[str, Any]) -> str:
    name = scenario.get("name", "Unknown Scenario")
    description = scenario.get("description", "No description available.")
    briefs = []
    for key in ("attack_brief", "defense_brief", "innovation_brief", "compliance_brief"):
        val = scenario.get(key)
        if val:
            briefs.append(f"{key.replace('_', ' ').title()}: {val}")
    prompt = f"Scenario: {name}\n\n{description}"
    if briefs:
        prompt += "\n\n" + "\n".join(briefs)
    prompt += "\n\nProduce a structured strategy response."
    return prompt


def _chain_result_to_dict(result: ChainResult) -> Dict[str, Any]:
    return {
        "judgment": result.content,
        "content": result.content,
        "confidence": result.confidence,
        "transcript": result.rounds,
        "models_used": result.models_used,
        "total_cost_usd": result.total_cost_usd,
        "total_duration_ms": result.total_duration_ms,
        "total_input_tokens": result.total_input_tokens,
        "total_output_tokens": result.total_output_tokens,
        "stop_reason": result.stop_reason,
        "trace_id": result.trace_id,
        "chain_mode": result.chain_mode,
    }
