# CUI // SP-CTI
"""AI Augmentation Canvas — Configuration Threshold Anomaly Detector.

Implements the ``anomaly_detection`` AI paradigm for the ``hardcoded_threshold``
pattern found in tools/ai_augmentation/agent_readiness/pillars/configuration.py
(AAC opportunity aac-opp-500).

Replaces hard-coded minimum counts such as::

    targets >= 3      # Makefile target count
    len(scripts) >= 3 # npm scripts count

with LLM-based sufficiency reasoning that adapts to project context.

Public API:
    assess_task_runner_completeness(tool_type, count, *, context="", timeout=30) -> dict

CLI:
    python tools/ai_augmentation/config_threshold_detector.py \\
        --tool-type makefile --count 2 --context "small utility library"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Default threshold used when LLM is unavailable (mirrors the original hardcoded value).
_DEFAULT_MIN_COUNT = 3

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
_SUFFICIENCY_PROMPT = """\
You are a DevOps expert assessing whether a project has sufficient automation tasks configured.

Tool type : {tool_type}
Task count: {count}
Context   : {context}

Determine whether this count of {tool_type} tasks is sufficient for a well-maintained project.
A project with too few tasks likely lacks key automation (test, lint, build, deploy, clean).

Respond with a JSON object — no markdown, no extra text — matching this schema:
{{
  "sufficient": <true|false>,
  "confidence": <0.0-1.0>,
  "reason": "<one sentence explaining the assessment>",
  "recommendation": "<add more tasks / keep / consolidate — one sentence>"
}}
"""

# ---------------------------------------------------------------------------
# LLM invocation helpers
# ---------------------------------------------------------------------------


def _llm_assess(tool_type: str, count: int, context: str, timeout: int) -> dict:
    """Call the LLMRouter with the anomaly_detection routing function."""
    from tools.llm.provider import LLMRequest
    from tools.llm.router import LLMRouter

    router = LLMRouter()
    request = LLMRequest(
        messages=[
            {
                "role": "user",
                "content": _SUFFICIENCY_PROMPT.format(
                    tool_type=tool_type,
                    count=count,
                    context=context or "general software project",
                ),
            }
        ],
        system_prompt=(
            "You are a DevOps configuration analyst. "
            "Reply only with a JSON object as specified — no prose."
        ),
        agent_id="config-threshold-detector",
        classification="CUI",
        max_tokens=256,
        effort="low",
    )
    response = router.invoke("anomaly_detection", request)
    return _parse_response(response.content or "")


def _parse_response(raw: str) -> dict:
    """Extract the JSON payload from LLM output."""
    text = re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                data = {}
        else:
            data = {}

    return {
        "sufficient": bool(data.get("sufficient", True)),
        "confidence": float(data.get("confidence", 0.5)),
        "reason": str(data.get("reason", "Unable to assess.")),
        "recommendation": str(data.get("recommendation", "Manual review recommended.")),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assess_task_runner_completeness(
    tool_type: str,
    count: int,
    *,
    context: str = "",
    timeout: int = 30,
) -> dict:
    """Assess whether a task runner has sufficient tasks using LLM-based anomaly detection.

    Replaces hardcoded minimum-count comparisons (e.g. ``targets >= 3`` in
    the configuration pillar) with AI reasoning that adapts to project type.

    Args:
        tool_type: Task runner type — "makefile", "npm_scripts", "taskfile", etc.
        count:     Number of tasks/targets/scripts observed.
        context:   Optional project context (type, purpose, size).
        timeout:   LLM call timeout in seconds.

    Returns:
        Dict with keys:
            sufficient    (bool)  — True if the count appears adequate
            confidence    (float) — [0.0, 1.0] model confidence
            reason        (str)   — one-sentence explanation
            recommendation(str)   — add more / keep / consolidate guidance
            raw           (str)   — raw LLM output (empty string on fallback)
    """
    if not tool_type:
        raise ValueError("tool_type must be a non-empty string")
    if count < 0:
        raise ValueError("count must be non-negative")

    try:
        return _llm_assess(tool_type, count, context, timeout)
    except Exception:  # noqa: BLE001 — any LLM failure → static fallback
        sufficient = count >= _DEFAULT_MIN_COUNT
        return {
            "sufficient": sufficient,
            "confidence": 0.5,
            "reason": (
                f"{tool_type} has {count} task(s); "
                f"{'sufficient' if sufficient else 'below minimum of'} {_DEFAULT_MIN_COUNT}."
            ),
            "recommendation": (
                "keep" if sufficient
                else f"add at least {_DEFAULT_MIN_COUNT - count} more task(s)."
            ),
            "raw": "",
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "AAC Configuration Threshold Detector — assess task runner completeness "
            "via LLM instead of hardcoded minimum counts."
        ),
        epilog="CUI // SP-CTI  |  AAC opportunity aac-opp-500",
    )
    p.add_argument("--tool-type", required=True,
                   help="Task runner type (e.g. makefile, npm_scripts)")
    p.add_argument("--count", required=True, type=int,
                   help="Number of tasks/targets observed")
    p.add_argument("--context", default="",
                   help="Optional project context (e.g. 'small utility library')")
    p.add_argument("--timeout", type=int, default=30, help="LLM timeout seconds")
    p.add_argument("--json", dest="output_json", action="store_true",
                   help="Output as JSON")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    result = assess_task_runner_completeness(
        args.tool_type, args.count,
        context=args.context,
        timeout=args.timeout,
    )
    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        status = "SUFFICIENT" if result["sufficient"] else "INSUFFICIENT"
        print(f"[{status}] {args.tool_type} ({args.count} tasks)")
        print(f"  Confidence     : {result['confidence']:.2f}")
        print(f"  Reason         : {result['reason']}")
        print(f"  Recommendation : {result['recommendation']}")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
