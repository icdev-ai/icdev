# CUI // SP-CTI
"""AI Augmentation Canvas — LLM-based Threshold Replacement Advisor.

Implements the ``llm_generation`` AI paradigm for ``hardcoded_threshold``
patterns detected by the AAC pattern classifier.  Given a detected numeric
threshold, uses the LLM to suggest a configuration-driven or context-aware
replacement.

Public API:
    generate_threshold_recommendation(pattern: dict, context: dict | None) -> dict

Returns a dict with keys:
    config_key        — suggested environment/config variable name
    default_value     — recommended default (same as detected literal)
    rationale         — plain-language explanation
    migration_snippet — minimal code change example
    feasibility       — 'low' | 'medium' | 'high'
    source            — 'llm' | 'heuristic' (fallback when LLM unavailable)
"""
from __future__ import annotations

import json
import re
from typing import Any

try:
    from tools.llm.router import LLMRouter, LLMUnavailableError
    from tools.llm.provider import LLMRequest
    _LLM_AVAILABLE = True
except ImportError:
    LLMRouter = None  # type: ignore[assignment,misc]
    LLMUnavailableError = Exception  # type: ignore[assignment,misc]
    LLMRequest = None  # type: ignore[assignment]
    _LLM_AVAILABLE = False


_SYSTEM_PROMPT = (
    "You are an expert software engineer specializing in configuration management "
    "and AI-driven adaptive thresholds.  Given a detected hardcoded numeric threshold "
    "in source code, suggest the best way to make it configurable or adaptive.\n\n"
    "Respond with a valid JSON object containing exactly these keys:\n"
    '  "config_key"        — SCREAMING_SNAKE_CASE env/config variable name\n'
    '  "default_value"     — numeric default (keep the original value)\n'
    '  "rationale"         — one sentence explaining why this should be configurable\n'
    '  "migration_snippet" — minimal before/after Python code change (string, ≤4 lines)\n'
    '  "feasibility"       — one of: "low", "medium", "high"\n'
    "Respond with JSON only — no markdown fences, no extra text."
)


def _heuristic_recommendation(threshold_value: float | int, module_path: str) -> dict:
    """Fallback recommendation when the LLM is unavailable."""
    raw = str(module_path).replace("\\", "/").split("/")[-1].replace(".py", "")
    base = re.sub(r"[^a-zA-Z0-9]", "_", raw).upper()
    config_key = f"{base}_THRESHOLD" if base else "THRESHOLD_VALUE"

    return {
        "config_key": config_key,
        "default_value": threshold_value,
        "rationale": (
            f"The literal {threshold_value} limits adaptability; "
            "externalise to a config variable so it can be tuned per deployment."
        ),
        "migration_snippet": (
            f"# Before\nif value > {threshold_value}:\n"
            f"# After\nimport os\n"
            f"_THRESH = float(os.getenv('{config_key}', {threshold_value}))\n"
            f"if value > _THRESH:"
        ),
        "feasibility": "medium",
        "source": "heuristic",
    }


def generate_threshold_recommendation(
    pattern: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a replacement recommendation for a hardcoded threshold.

    Args:
        pattern:  A pattern dict produced by the AAC pattern classifier with
                  ``pattern_type == "hardcoded_threshold"``.  Expected fields:
                  ``pattern_detail.constants`` (list of numeric values),
                  ``module_path``, ``function_name``.
        context:  Optional extra context (e.g. ``il_level``, ``project_name``).

    Returns:
        Recommendation dict.  When the LLM is unavailable the ``source``
        field is ``"heuristic"`` and the recommendation is rule-based.
    """
    if context is None:
        context = {}

    detail = pattern.get("pattern_detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except (ValueError, TypeError):
            detail = {}

    constants: list = detail.get("constants", [])
    threshold_value: float | int = constants[0] if constants else 0
    module_path: str = pattern.get("module_path", "<unknown>")
    function_name: str = pattern.get("function_name", "<unknown>")

    if not _LLM_AVAILABLE or LLMRouter is None or LLMRequest is None:
        return _heuristic_recommendation(threshold_value, module_path)

    user_message = (
        f"File: {module_path}\n"
        f"Function: {function_name}\n"
        f"Detected threshold value: {threshold_value}\n"
        f"Detection kind: {detail.get('kind', 'compare')}\n"
        f"IL level: {context.get('il_level', 'il4')}\n\n"
        "Suggest a configuration-driven replacement for this hardcoded threshold."
    )

    request = LLMRequest(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=_SYSTEM_PROMPT,
        max_tokens=512,
        temperature=0.2,
        classification="CUI",
        skip_injection_scan=True,
    )

    try:
        router = LLMRouter()
        response = router.invoke("code_generation", request)
        raw = response.content.strip()
        recommendation = json.loads(raw)
        recommendation.setdefault("source", "llm")
        return recommendation
    except LLMUnavailableError:
        return _heuristic_recommendation(threshold_value, module_path)
    except (json.JSONDecodeError, ValueError, TypeError):
        return _heuristic_recommendation(threshold_value, module_path)
    except Exception:  # noqa: BLE001
        return _heuristic_recommendation(threshold_value, module_path)
