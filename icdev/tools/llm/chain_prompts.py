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

    # ------------------------------------------------------------------
    # Council -- fixed-perspective panel + anonymized peer review + chairman
    # synthesis. Distinct from CoD: debaters argue a generated FOR/AGAINST
    # stance across multiple rounds toward a judge picking a winner; council
    # advisors each give ONE independent pass from a FIXED cognitive lens
    # (contrarian, first-principles, ...), then anonymously peer-review each
    # other before a chairman synthesizes agreement/clash/blind-spots/
    # recommendation. Built for decision-quality analysis, not debate-to-a-
    # winner. Adapted from the community "LLM Council" skill (methodology by
    # Andrej Karpathy).
    # ------------------------------------------------------------------

    @staticmethod
    def council_advisor(
        user_prompt: str,
        advisor_name: str,
        advisor_style: str,
        *,
        system_prompt: str = "",
        output_schema: Optional[Dict] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for one council advisor's
        independent pass. No prior arguments are shown -- unlike a debater,
        each advisor analyzes the question cold, from their fixed lens."""
        sys = _CUI_BANNER + (
            f"You are {advisor_name} on an LLM Council.\n\n"
            f"Your thinking style: {advisor_style}\n\n"
            "Respond independently. Do not hedge or try to be balanced. Lean fully "
            "into your assigned angle. The other advisors will cover the angles "
            "you're not covering.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"
        sys += _schema_hint(output_schema)

        usr = (
            f"[QUESTION BROUGHT TO THE COUNCIL]\n{user_prompt}\n\n"
            "[INSTRUCTION] Respond from your perspective. Be direct and specific. "
            "Keep your response between 150-300 words. No preamble -- go straight "
            "into your analysis."
        )
        return sys, usr

    @staticmethod
    def divergence_branch(
        user_prompt: str,
        frame_name: str,
        frame_instruction: str,
        *,
        system_prompt: str = "",
        output_schema: Optional[Dict] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for one divergence branch.

        The GENERATIVE counterpart to council_advisor(): each branch receives the
        problem plus ONE generative frame and is instructed to WIDEN the option
        space -- produce candidate ideas and explicitly NOT evaluate, rank, or
        self-critique. Unlike debater(), no prior_arguments are ever threaded in:
        branches run in strict isolation (one round, no cross-reading), because
        serializing or cross-feeding them collapses divergence into a single
        wider thought. Scoring/clustering/deepening is a SEPARATE invocation
        (dvg-critic-*) with an opposing critic system prompt -- the
        generator/critic split is intentionally mechanical, never one unified
        response.
        """
        sys = _CUI_BANNER + (
            f"You are a divergent idea generator working under the '{frame_name}' frame.\n\n"
            f"Frame: {frame_instruction}\n\n"
            "Your ONLY job is to GENERATE candidate ideas under this frame. Widen "
            "the option space. Do NOT evaluate, rank, score, self-critique, hedge, "
            "or pick a 'best' -- a separate critic pass does all of that. Lean fully "
            "into your frame; other branches cover the angles you don't.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"
        sys += _schema_hint(output_schema)

        usr = (
            f"[PROBLEM]\n{user_prompt}\n\n"
            "[INSTRUCTION] Generate 3-6 DISTINCT candidate ideas under your frame. "
            "For each, give a short title then 1-2 sentences on the core approach. "
            "Number them. No evaluation, no ranking, no 'recommended' -- just the raw "
            "options. End with [IDEAS]."
        )
        return sys, usr

    @staticmethod
    def council_peer_review(
        user_prompt: str,
        anonymized_responses: List[Dict[str, str]],
        *,
        system_prompt: str = "",
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for a council peer-review pass.

        Args:
            anonymized_responses: [{"label": "A", "response": "..."}, ...] --
                shuffled so the reviewer judges the argument on merit instead
                of deferring to a persona.
        """
        sys = _CUI_BANNER + (
            "You are reviewing the outputs of an LLM Council. Several advisors "
            "independently answered the question below. Their responses are "
            "anonymized -- evaluate on merit, not on who you think said what.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"

        usr = f"[QUESTION BROUGHT TO THE COUNCIL]\n{user_prompt}\n\n"
        for entry in anonymized_responses:
            usr += f"[RESPONSE {entry['label']}]\n{entry['response']}\n\n"
        usr += (
            "[INSTRUCTION] Answer these three questions. Be specific. Reference "
            "responses by letter.\n"
            "1. Which response is the strongest? Why?\n"
            "2. Which response has the biggest blind spot? What is it missing?\n"
            "3. What did ALL responses miss that the council should consider?\n"
            "Keep your review under 200 words. Be direct. End with [PEER REVIEW]."
        )
        return sys, usr

    @staticmethod
    def council_chairman(
        user_prompt: str,
        advisor_responses: List[Dict[str, str]],
        peer_reviews: List[str],
        *,
        system_prompt: str = "",
        output_schema: Optional[Dict] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) for the council chairman
        synthesis.

        Args:
            advisor_responses: [{"name": "The Contrarian", "response": "..."}, ...]
                -- de-anonymized (the chairman sees who said what).
            peer_reviews: Raw peer-review text blocks (already anonymized
                internally; the chairman doesn't need the anonymization map).
        """
        sys = _CUI_BANNER + (
            "You are the Chairman of an LLM Council. Your job is to synthesize "
            "the work of the council's advisors and their peer reviews into a "
            "final verdict. Be direct. Don't hedge. The whole point of the "
            "council is to give clarity that a single perspective couldn't "
            "provide.\n"
        )
        if system_prompt:
            sys += f"\n[ORIGINAL SYSTEM CONTEXT]\n{system_prompt}\n"
        sys += _schema_hint(output_schema)

        usr = f"[QUESTION BROUGHT TO THE COUNCIL]\n{user_prompt}\n\n[ADVISOR RESPONSES]\n\n"
        for entry in advisor_responses:
            usr += f"**{entry['name']}:**\n{entry['response']}\n\n"
        usr += "[PEER REVIEWS]\n\n"
        for i, review in enumerate(peer_reviews, 1):
            usr += f"Reviewer {i}:\n{review}\n\n"
        usr += (
            "[INSTRUCTION] Produce the council verdict using this exact structure:\n\n"
            "## Where the Council Agrees\n"
            "[Points multiple advisors converged on independently. High-confidence signals.]\n\n"
            "## Where the Council Clashes\n"
            "[Genuine disagreements. Present both sides. Explain why reasonable advisors disagree.]\n\n"
            "## Blind Spots the Council Caught\n"
            "[Things that only emerged through peer review -- things individual "
            "advisors missed that others flagged.]\n\n"
            "## The Recommendation\n"
            "[A clear, direct recommendation. Not \"it depends.\" A real answer with reasoning.]\n\n"
            "## The One Thing to Do First\n"
            "[A single concrete next step. Not a list. One thing.]\n\n"
            "End with [COUNCIL VERDICT]."
        )
        return sys, usr
