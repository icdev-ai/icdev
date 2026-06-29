# CUI // SP-CTI
"""LLM Fallback Triage — classifies oracle tasks that no deterministic verifier resolved.

Called by oracle_triage._triage_one() when the result would be "skip" due to
an unknown lens or no matching heuristic. Routes through LLMRouter at scanner
tier (qwen3-local → Claude fallback).

Gate: ICDEV_ORACLE_LLM_FALLBACK=true in .env (default off).
Min confidence: adaptive (mean - z*std from harness_eval history); falls back
to 0.65 when insufficient history exists.
"""
from __future__ import annotations

import json
import statistics

from tools.logging.icdev_logger import get_logger
import os
from typing import Any

LOG = get_logger(__name__)

_MIN_CONFIDENCE_DEFAULT = 0.65  # floor / fallback when history is insufficient
_CONFIDENCE_THRESHOLD_CACHE: float | None = None  # reset at start of each triage run


# ---------------------------------------------------------------------------
# Adaptive confidence threshold (anomaly detection)
# ---------------------------------------------------------------------------

def _fetch_llm_confidence_history(limit: int = 50) -> list[float]:
    """Return recent oracle_triage confidence scores from harness_eval.

    Returns an empty list when the DB is unavailable or the table does not exist.
    """
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT confidence FROM harness_eval
                WHERE reflex = 'oracle_triage'
                  AND confidence IS NOT NULL
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return [float(r[0]) for r in rows if r[0] is not None]
        finally:
            conn.close()
    except Exception as exc:
        LOG.debug("[llm_triage] _fetch_llm_confidence_history failed: %s", exc)
        return []


def _compute_adaptive_min_confidence(
    history: list[float],
    *,
    floor: float = _MIN_CONFIDENCE_DEFAULT,
    z_score: float = 1.5,
    min_samples: int = 10,
) -> float | None:
    """Derive adaptive minimum confidence from historical distribution.

    Computes max(floor, mean - z_score * std). Returns None when history is
    insufficient (< min_samples) or distribution is degenerate (std == 0).
    """
    if len(history) < min_samples or len(history) < 2:
        return None
    std = statistics.stdev(history)
    if std == 0.0:
        return None
    mean = statistics.mean(history)
    return max(floor, round(mean - z_score * std, 4))


def _get_adaptive_min_confidence() -> float:
    """Return cached adaptive min confidence; compute on first call per process.

    Falls back to _MIN_CONFIDENCE_DEFAULT if history is insufficient or the
    distribution is degenerate.
    """
    global _CONFIDENCE_THRESHOLD_CACHE
    if _CONFIDENCE_THRESHOLD_CACHE is not None:
        return _CONFIDENCE_THRESHOLD_CACHE

    z_score = 1.5
    min_samples = 10
    try:
        from tools.genesis.harness.eval_harness import _load_harness_config
        ad = _load_harness_config().get("anomaly_detection", {})
        z_score = float(ad.get("z_score", z_score))
        min_samples = int(ad.get("min_samples", min_samples))
    except Exception:
        pass

    history = _fetch_llm_confidence_history()
    adaptive = _compute_adaptive_min_confidence(
        history,
        floor=_MIN_CONFIDENCE_DEFAULT,
        z_score=z_score,
        min_samples=min_samples,
    )
    _CONFIDENCE_THRESHOLD_CACHE = adaptive if adaptive is not None else _MIN_CONFIDENCE_DEFAULT
    return _CONFIDENCE_THRESHOLD_CACHE


def reset_confidence_threshold_cache() -> None:
    """Reset the adaptive confidence threshold cache (call at start of each triage run)."""
    global _CONFIDENCE_THRESHOLD_CACHE
    _CONFIDENCE_THRESHOLD_CACHE = None

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

        if confidence < _get_adaptive_min_confidence():
            return "skip", f"llm_low_confidence({confidence:.2f}): {reason}", confidence

        return action, f"[llm] {reason}", confidence

    except Exception as exc:
        LOG.debug("[llm_triage] failed: %s", exc)
        return "skip", "llm_unavailable", 0.0
