# CUI // SP-CTI
"""LLM Fallback Triage — classifies oracle tasks that no deterministic verifier resolved.

Called by oracle_triage._triage_one() when the result would be "skip" due to
an unknown lens or no matching heuristic. Routes through LLMRouter at scanner
tier (qwen3-local → Claude fallback).

Gate: ICDEV_ORACLE_LLM_FALLBACK=true in .env (default off).
Min confidence: 0.65 — below this the original "skip" is preserved.
"""
from __future__ import annotations

import json

from tools.logging.icdev_logger import get_logger
import os
from typing import Any

LOG = get_logger(__name__)

_MIN_CONFIDENCE = 0.65

_SYSTEM_PROMPT = """\
You are the Oracle Triage classifier for ICDEV™, a DevSecOps platform.

The Oracle gap-detection system scans the codebase and creates "suggested" tasks for \
potential gaps. Deterministic verifiers already handled the clear cases — you are \
seeing the ambiguous remainder.

Classify each task into one of three actions:

  promote  — A real implementation gap. The task references a specific file path, \
route, table, or component that is genuinely missing from the codebase. Actionable work.

  dismiss  — A false positive. The "gap" appears only in documentation, plans, \
comments, or already-built code the Oracle missed. Closing creates no risk.

  skip     — Genuinely ambiguous. You cannot determine from the task text alone \
whether the gap is real. Human judgment required.

Bias rules:
- Concrete artifact (file path, route URL, DB table name) that should exist → lean promote
- "planned", "future", "TODO", "docs-only", reference to a spec section not code → lean dismiss
- Reserve skip for cases where you truly cannot tell

Return ONLY valid JSON on one line:
{"action": "promote"|"dismiss"|"skip", "reason": "<one concise sentence>", "confidence": 0.0-1.0}
"""

_FEW_SHOT: list[dict] = [
    {
        "role": "user",
        "content": (
            "Task: tool_not_in_manifest gap: tools/genesis/harness/eval_harness.py\n"
            "Description: Oracle detected this file exists but has no manifest entry.\n"
            "Lens: tool_not_in_manifest"
        ),
    },
    {
        "role": "assistant",
        "content": '{"action": "promote", "reason": "File exists but unregistered in manifest — real gap", "confidence": 0.91}',
    },
    {
        "role": "user",
        "content": (
            "Task: Add Redis caching layer for dashboard API responses\n"
            "Description: The dashboard API makes DB queries on every request; a cache would help.\n"
            "Lens: null"
        ),
    },
    {
        "role": "assistant",
        "content": '{"action": "skip", "reason": "Feature suggestion with no concrete missing artifact — needs human scoping", "confidence": 0.80}',
    },
    {
        "role": "user",
        "content": (
            "Task: [FR] Support dark mode in dashboard UI\n"
            "Description: Users have requested a dark mode toggle in the dashboard settings.\n"
            "Lens: null"
        ),
    },
    {
        "role": "assistant",
        "content": '{"action": "dismiss", "reason": "Feature request with no concrete gap — should be tracked as a backlog item, not a gap card", "confidence": 0.73}',
    },
]


def llm_triage_task(task: dict[str, Any]) -> tuple[str, str, float]:
    """Classify a task that no deterministic verifier could resolve.

    Returns (action, reason, confidence).
    Always returns ("skip", ..., 0.0) on error or when gate is off.
    """
    if os.getenv("ICDEV_ORACLE_LLM_FALLBACK", "").lower() not in ("true", "1"):
        return "skip", "llm_fallback_disabled", 0.0

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        title = task.get("title", "")
        description = (task.get("description") or "")[:600]
        lens = task.get("oracle_lens") or "null"

        user_content = f"Task: {title}\nDescription: {description}\nLens: {lens}"

        router = LLMRouter()
        request = LLMRequest(
            messages=_FEW_SHOT + [{"role": "user", "content": user_content}],
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=128,
            temperature=0.0,
            skip_injection_scan=True,
        )
        response = router.invoke("oracle_triage_llm", request)
        if not response or not response.content:
            return "skip", "llm_empty_response", 0.0

        raw = response.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()

        parsed = json.loads(raw)
        action = parsed.get("action", "skip")
        reason = str(parsed.get("reason", ""))
        confidence = float(parsed.get("confidence", 0.0))

        if action not in ("promote", "dismiss", "skip"):
            action = "skip"
            confidence = 0.0

        if confidence < _MIN_CONFIDENCE:
            return "skip", f"llm_low_confidence({confidence:.2f}): {reason}", confidence

        return action, f"[llm] {reason}", confidence

    except Exception as exc:
        LOG.debug("[llm_triage] failed: %s", exc)
        return "skip", "llm_unavailable", 0.0
