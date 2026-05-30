# CUI // SP-CTI
"""AI Augmentation Canvas — Version Anomaly Detector.

Implements the ``anomaly_detection`` AI paradigm for the ``hardcoded_threshold``
pattern (AAC opportunity 467).  Replaces hard-coded version-range assertions such as

    assert (3, 0, 2) <= (major, minor, patch) < (6, 0, 0)   # requests/__init__.py:78

with LLM-based compatibility reasoning so that bounds do not go stale as
upstream packages evolve.

Public API:
    check_version_compat(package, version, *, context="", timeout=30) -> dict

CLI:
    python tools/ai_augmentation/version_anomaly_detector.py \\
        --package chardet --version 4.0.0 --context "used by requests HTTP library"
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

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
_COMPAT_PROMPT = """\
You are a Python packaging expert assessing dependency version compatibility.

Package : {package}
Version : {version}
Context : {context}

Determine whether this version is likely to be compatible with a typical
Python application that depends on ``{package}`` for production use today.

Respond with a JSON object — no markdown, no extra text — matching this schema:
{{
  "compatible": <true|false>,
  "confidence": <0.0-1.0>,
  "reason": "<one sentence>",
  "recommendation": "<upgrade/downgrade/keep — one sentence>"
}}
"""

# ---------------------------------------------------------------------------
# LLM invocation helpers
# ---------------------------------------------------------------------------


def _llm_assess(package: str, version: str, context: str, timeout: int) -> dict:
    """Call the LLMRouter with the anomaly_detection routing function."""
    from tools.llm.provider import LLMRequest
    from tools.llm.router import LLMRouter

    router = LLMRouter()
    request = LLMRequest(
        messages=[
            {
                "role": "user",
                "content": _COMPAT_PROMPT.format(
                    package=package, version=version, context=context or "general use"
                ),
            }
        ],
        system_prompt=(
            "You are a dependency compatibility analyst. "
            "Reply only with a JSON object as specified — no prose."
        ),
        agent_id="version-anomaly-detector",
        classification="CUI",
        max_tokens=512,
        effort="medium",
    )
    response = router.invoke("anomaly_detection", request)
    return _parse_response(response.content or "")


def _parse_response(raw: str) -> dict:
    """Extract the JSON payload from LLM output."""
    # Strip markdown fences if present
    text = re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Fallback: extract first JSON object from text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                data = {}
        else:
            data = {}

    return {
        "compatible": bool(data.get("compatible", True)),
        "confidence": float(data.get("confidence", 0.5)),
        "reason": str(data.get("reason", "Unable to assess.")),
        "recommendation": str(data.get("recommendation", "Manual review recommended.")),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_version_compat(
    package: str,
    version: str,
    *,
    context: str = "",
    timeout: int = 30,
) -> dict:
    """Assess package version compatibility using LLM-based anomaly detection.

    Replaces hardcoded tuple comparisons (e.g. the bounds in
    ``requests.check_compatibility``) with AI reasoning that remains valid as
    upstream packages release new versions.

    Args:
        package:  Package name (e.g. "chardet").
        version:  Version string to evaluate (e.g. "4.0.0").
        context:  Optional free-text context describing how the package is used.
        timeout:  LLM call timeout in seconds (unused — passed for API symmetry).

    Returns:
        Dict with keys:
            compatible    (bool)   — True if the version appears safe to use
            confidence    (float)  — [0.0, 1.0] model confidence
            reason        (str)    — one-sentence explanation
            recommendation(str)    — upgrade / downgrade / keep guidance
            raw           (str)    — raw LLM output for audit/debug
    """
    if not package or not version:
        raise ValueError("package and version must be non-empty strings")

    return _llm_assess(package, version, context, timeout)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "AAC Version Anomaly Detector — assess package version compatibility "
            "via LLM instead of hardcoded bounds."
        ),
        epilog="CUI // SP-CTI  |  AAC opportunity aac-opp-467",
    )
    p.add_argument("--package", required=True, help="Package name (e.g. chardet)")
    p.add_argument("--version", required=True, help="Version to assess (e.g. 4.0.0)")
    p.add_argument("--context", default="", help="How this package is used (optional)")
    p.add_argument("--timeout", type=int, default=30, help="LLM timeout seconds")
    p.add_argument("--json", dest="output_json", action="store_true",
                   help="Output as JSON")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    result = check_version_compat(
        args.package, args.version,
        context=args.context,
        timeout=args.timeout,
    )
    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        compat = "COMPATIBLE" if result["compatible"] else "INCOMPATIBLE"
        print(f"[{compat}] {args.package}=={args.version}")
        print(f"  Confidence   : {result['confidence']:.2f}")
        print(f"  Reason       : {result['reason']}")
        print(f"  Recommendation: {result['recommendation']}")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
