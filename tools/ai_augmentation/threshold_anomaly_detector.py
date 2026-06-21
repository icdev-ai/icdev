# CUI // SP-CTI
"""AI Augmentation Canvas — Threshold Anomaly Detector.

Implements the ``anomaly_detection`` AI paradigm for the ``hardcoded_threshold``
pattern (AAC opportunity 533).  Takes hardcoded-threshold hits produced by
``pattern_classifier._detect_hardcoded_threshold`` and uses LLM-based reasoning
to classify each numeric constant as a *semantic threshold* (business logic that
would benefit from AI/config replacement) vs an *algorithmic constant* (loop
bound, array index, or other structural literal that should stay as-is).

Public API:
    classify_threshold(code_snippet, constant_value, *, context="", timeout=30) -> dict
    enrich_threshold_hits(hits, source_text, *, context="", timeout=30) -> list[dict]

CLI:
    python tools/ai_augmentation/threshold_anomaly_detector.py \\
        --snippet "if score > 0.85: flag()" --value 0.85 --context "fraud scoring"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_SEMANTIC_TYPES = frozenset({"semantic_threshold", "algorithmic_constant", "uncertain"})

# Lines of context to extract around each detected threshold
_CONTEXT_LINES = 3

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """\
You are a software engineering expert analyzing a hardcoded numeric constant in source code.

Code snippet (surrounding context):
```
{snippet}
```

Detected constant: {value}
Additional context: {context}

Determine whether this numeric constant is:
- "semantic_threshold": a business-logic or domain threshold (ML confidence, rate limit,
  budget cap, fraud score, retry limit, timeout, page size) that would benefit from being
  made configurable or replaced by an AI/ML model.
- "algorithmic_constant": a structural literal used for loop bounds, bit shifts, array
  indexing, mathematical formulas, or other algorithmic operations that should stay fixed.
- "uncertain": insufficient context to determine with confidence.

If semantic_threshold, suggest a descriptive snake_case variable name for the config
parameter (e.g. "fraud_score_cutoff", "max_retry_attempts").

Respond with a JSON object — no markdown, no extra text:
{{
  "semantic_type": "<semantic_threshold|algorithmic_constant|uncertain>",
  "confidence": <0.0-1.0>,
  "reason": "<one sentence>",
  "suggested_name": "<snake_case_name or null>"
}}
"""

# ---------------------------------------------------------------------------
# LLM invocation helpers
# ---------------------------------------------------------------------------


def _llm_assess(snippet: str, value: float | int, context: str) -> dict:
    """Call the LLMRouter with the anomaly_detection routing function."""
    from tools.llm.provider import LLMRequest
    from tools.llm.router import LLMRouter

    router = LLMRouter()
    request = LLMRequest(
        messages=[
            {
                "role": "user",
                "content": _CLASSIFY_PROMPT.format(
                    snippet=snippet,
                    value=value,
                    context=context or "no additional context",
                ),
            }
        ],
        system_prompt=(
            "You are a code quality analyst. "
            "Reply only with a JSON object as specified — no prose."
        ),
        agent_id="threshold-anomaly-detector",
        classification="CUI",
        max_tokens=256,
        effort="low",
    )
    response = router.invoke("anomaly_detection", request)
    return _parse_response(response.content or "")


def _parse_response(raw: str) -> dict[str, Any]:
    """Extract and normalise the JSON payload from LLM output."""
    text = re.sub(r"```(?:json)?|```", "", raw).strip()
    data: dict[str, Any] = {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

    semantic_type = str(data.get("semantic_type", "uncertain"))
    if semantic_type not in _VALID_SEMANTIC_TYPES:
        semantic_type = "uncertain"

    confidence = data.get("confidence", 0.5)
    try:
        confidence = float(confidence)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    suggested = data.get("suggested_name")
    if suggested is not None:
        suggested = str(suggested) if suggested else None

    return {
        "semantic_type": semantic_type,
        "confidence": confidence,
        "reason": str(data.get("reason", "Unable to classify.")),
        "suggested_name": suggested,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_threshold(
    code_snippet: str,
    constant_value: float | int,
    *,
    context: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """Classify a detected hardcoded threshold using LLM-based anomaly detection.

    Determines whether a numeric constant in source code is a business-logic
    threshold that should be made configurable/AI-driven, or an algorithmic
    constant that is correct as a literal.

    Args:
        code_snippet: Source lines surrounding the detected constant (non-empty).
        constant_value: The numeric literal that was detected.
        context: Optional free-text description of the module or function purpose.
        timeout: LLM call timeout in seconds (unused — passed for API symmetry).

    Returns:
        Dict with keys:
            semantic_type   (str)   — "semantic_threshold" | "algorithmic_constant" | "uncertain"
            confidence      (float) — [0.0, 1.0] model confidence
            reason          (str)   — one-sentence explanation
            suggested_name  (str|None) — proposed config variable name, or None
            raw             (str)   — raw LLM output for audit/debug

    Raises:
        ValueError: if code_snippet is empty.
    """
    if not code_snippet or not code_snippet.strip():
        raise ValueError("code_snippet must be a non-empty string")

    return _llm_assess(code_snippet, constant_value, context)


def enrich_threshold_hits(
    hits: list[dict],
    source_text: str,
    *,
    context: str = "",
    timeout: int = 30,
) -> list[dict]:
    """Enrich hardcoded_threshold hits with AI-driven anomaly detection.

    For each hit whose ``pattern_type`` is ``"hardcoded_threshold"``, extracts
    the surrounding source lines and calls ``classify_threshold`` to add an
    ``ai_classification`` sub-dict to ``pattern_detail``.  Non-threshold hits
    are returned unchanged.  If the LLM call fails for any hit, that hit is
    returned without the ``ai_classification`` key (fail-open).

    Args:
        hits: List of pattern dicts from ``detect_patterns()`` or
              ``_detect_hardcoded_threshold()``.
        source_text: Full source text of the file containing the hits.
        context: Optional module-level context forwarded to classify_threshold.
        timeout: Per-hit LLM timeout in seconds.

    Returns:
        A new list of pattern dicts; threshold hits are enriched in-place copies.
    """
    if not hits:
        return []

    source_lines = source_text.splitlines()
    enriched: list[dict] = []

    for hit in hits:
        import copy
        hit_copy = copy.deepcopy(hit)

        if hit_copy.get("pattern_type") != "hardcoded_threshold":
            enriched.append(hit_copy)
            continue

        line_start: int = hit_copy.get("line_start", 1)
        constants: list = hit_copy.get("pattern_detail", {}).get("constants", [])
        constant_value: float | int = constants[0] if constants else 0

        # Extract surrounding source lines (1-based → 0-based)
        lo = max(0, line_start - 1 - _CONTEXT_LINES)
        hi = min(len(source_lines), line_start - 1 + _CONTEXT_LINES + 1)
        snippet = "\n".join(source_lines[lo:hi])

        try:
            classification = classify_threshold(
                snippet, constant_value, context=context, timeout=timeout
            )
            hit_copy["pattern_detail"]["ai_classification"] = {
                "semantic_type": classification["semantic_type"],
                "confidence": classification["confidence"],
                "reason": classification["reason"],
                "suggested_name": classification["suggested_name"],
            }
        except Exception:
            # Fail-open: return the hit without AI enrichment
            pass

        enriched.append(hit_copy)

    return enriched


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "AAC Threshold Anomaly Detector — classify a hardcoded numeric threshold "
            "as semantic (business logic) or algorithmic (structural constant) via LLM."
        ),
        epilog="CUI // SP-CTI  |  AAC opportunity aac-rm-83287-phase-533",
    )
    p.add_argument("--snippet", required=True, help="Code snippet containing the threshold")
    p.add_argument("--value", required=True, type=float, help="Numeric constant value")
    p.add_argument("--context", default="", help="Optional module/function context")
    p.add_argument("--timeout", type=int, default=30, help="LLM timeout seconds")
    p.add_argument("--json", dest="output_json", action="store_true", help="Output as JSON")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    result = classify_threshold(
        args.snippet, args.value,
        context=args.context,
        timeout=args.timeout,
    )
    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[{result['semantic_type'].upper()}] value={args.value}")
        print(f"  Confidence     : {result['confidence']:.2f}")
        print(f"  Reason         : {result['reason']}")
        print(f"  Suggested name : {result['suggested_name'] or '(none)'}")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
