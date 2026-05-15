# [TEMPLATE: CUI // SP-CTI]
"""Jinja2-rendered prompt templates for Chain of Thought / Chain of Debate roles.

All templates include:
  - CUI // SP-CTI classification banner
  - Output schema hint if request.output_schema present
  - Tool schema hint if request.tools present
  - Configurable temperature override per role
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


_CUI_BANNER = (
    "// CLASSIFICATION: CUI // SP-CTI // DISTRIBUTION: ICDEV INTERNAL USE ONLY\n"
    "// Unclassified when removed from ICDEV™ system context\n"
)


def _schema_hint(output_schema: Optional[Dict]) -> str:
    if not output_schema:
        return ""
    import json

    return (
        f"\n[OUTPUT SCHEMA] Respond with a JSON object conforming to this schema:\n"
        f"{json.dumps(output_schema, indent=2)}\n"
    )


def _tool_hint(tools: Optional[List[Dict]]) -> str:
    if not tools:
        return ""
    import json

    return (
        f"\n[AVAILABLE TOOLS] You may call these functions if needed:\n"
        f"{json.dumps(tools, indent=2)}\n"
    )


class ChainPrompts:
    """Factory for CoT/CoD role-specific prompts."""

    @staticmethod
    def reasoner(
        user_prompt: str,
        *,
        system_prompt: str = "",
        output_schema: Optional[Dict] = None,
        tools: Optional[List[Dict]] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for the reasoner role.

        The reasoner thinks step-by-step before answering.
        """
        sys = _CUI_BANNER + (
            "You are a careful reasoning assistant. Think step by step. "
            "Show your full reasoning before giving the final answer. "
            "Number each step clearly. Do not skip intermediate reasoning.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"
        sys += _schema_hint(output_schema)
        sys += _tool_hint(tools)

        usr = (
            f"[TASK]\n{user_prompt}\n\n"
            "[INSTRUCTION] Break this down step by step. "
            "Show your reasoning, then provide the final answer clearly labeled as [FINAL ANSWER]."
        )
        return sys, usr

    @staticmethod
    def critic(
        user_prompt: str,
        reasoning: str,
        *,
        system_prompt: str = "",
        output_schema: Optional[Dict] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for the critic role.

        The critic reviews reasoning and identifies errors, gaps, assumptions.
        """
        sys = _CUI_BANNER + (
            "You are a rigorous critic. Review the reasoning below. "
            "Identify errors, logical gaps, unstated assumptions, and missed considerations. "
            "Be specific. Suggest corrections or additional steps needed.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"
        sys += _schema_hint(output_schema)

        usr = (
            f"[ORIGINAL TASK]\n{user_prompt}\n\n"
            f"[REASONING TO REVIEW]\n{reasoning}\n\n"
            "[INSTRUCTION] Identify errors, gaps, assumptions. "
            "Suggest specific corrections. End with [CRITIQUE SUMMARY]."
        )
        return sys, usr

    @staticmethod
    def synthesizer(
        user_prompt: str,
        reasoning: str,
        critique: str,
        *,
        system_prompt: str = "",
        output_schema: Optional[Dict] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for the synthesizer role.

        The synthesizer combines reasoning and critique into a final polished answer.
        """
        sys = _CUI_BANNER + (
            "You are a synthesis expert. Given the reasoning and critique below, "
            "produce the best possible final answer. Incorporate valid corrections. "
            "Be direct and concise.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"
        sys += _schema_hint(output_schema)

        usr = (
            f"[ORIGINAL TASK]\n{user_prompt}\n\n"
            f"[REASONING]\n{reasoning}\n\n"
            f"[CRITIQUE]\n{critique}\n\n"
            "[INSTRUCTION] Produce the final, corrected answer. "
            "Incorporate valid corrections from the critique. End with [FINAL ANSWER]."
        )
        return sys, usr

    @staticmethod
    def debater(
        user_prompt: str,
        debater_number: int,
        position: str,
        prior_arguments: Optional[List[str]] = None,
        *,
        system_prompt: str = "",
        output_schema: Optional[Dict] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for a debater role.

        Args:
            debater_number: 1-indexed debater identifier
            position: The position this debater argues for
            prior_arguments: Previous arguments from other debaters (optional)
        """
        sys = _CUI_BANNER + (
            f"You are Debater {debater_number}. You are arguing the position: {position}. "
            "Make your case strongly but honestly. Address prior arguments directly. "
            "Cite evidence and reasoning. Be respectful but firm.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"
        sys += _schema_hint(output_schema)

        usr = f"[TASK]\n{user_prompt}\n\n"
        usr += f"[YOUR POSITION]\n{position}\n\n"
        if prior_arguments:
            for i, arg in enumerate(prior_arguments, 1):
                usr += f"[PRIOR ARGUMENT {i}]\n{arg}\n\n"
            usr += (
                "[INSTRUCTION] Argue your position. Address the prior arguments above. "
                "Explain why your position is strongest. End with [ARGUMENT]."
            )
        else:
            usr += (
                "[INSTRUCTION] Argue your position. Provide reasoning and evidence. "
                "End with [ARGUMENT]."
            )
        return sys, usr

    @staticmethod
    def judge(
        user_prompt: str,
        arguments: List[Dict[str, Any]],
        *,
        system_prompt: str = "",
        output_schema: Optional[Dict] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for the judge role.

        Args:
            arguments: List of {"debater": int, "position": str, "argument": str}
        """
        sys = _CUI_BANNER + (
            "You are a neutral judge. Evaluate all positions fairly. "
            "State which argument is strongest and why. Provide a confidence score (0.0–1.0). "
            "Do not simply average — choose the best reasoning.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"
        sys += _schema_hint(output_schema)

        usr = f"[TASK]\n{user_prompt}\n\n"
        for arg in arguments:
            usr += (
                f"[DEBATER {arg['debater']}] Position: {arg['position']}\n"
                f"{arg['argument']}\n\n"
            )
        usr += (
            "[INSTRUCTION] Evaluate all positions. State the strongest argument "
            "with confidence score 0.0–1.0. End with [JUDGMENT]."
        )
        return sys, usr

    @staticmethod
    def self_consistency_voter(
        answers: List[str],
        *,
        system_prompt: str = "",
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for the majority-vote synthesizer.

        Used in self-consistency mode when running CoT multiple times.
        """
        sys = _CUI_BANNER + (
            "You are a consistency checker. Given multiple answers to the same question, "
            "identify the most common or most correct answer. If answers conflict, "
            "choose the one with the best reasoning.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"

        usr = "[ANSWERS TO REVIEW]\n"
        for i, ans in enumerate(answers, 1):
            usr += f"Answer {i}:\n{ans}\n\n"
        usr += (
            "[INSTRUCTION] Identify the majority or best answer. "
            "Return the consensus answer with brief justification. End with [CONSENSUS]."
        )
        return sys, usr
