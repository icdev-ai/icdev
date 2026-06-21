# CUI // SP-CTI
"""AI Augmentation Canvas — NLP Input-Type Extractor.

Replaces the regex pattern ``re.match(r"^(https?://|git@)", input_ref)``
in blueprint.py with an LLM-based entity recognizer that handles variation
and ambiguity more robustly (GitHub shorthand, bare repo names, etc.).

Public API:
    classify_input_ref(input_ref, *, timeout=10) -> str
        Returns "git_url", "local_path", or "unknown".
        Falls back to regex on LLM error so the caller is never blocked.

Acceptance criterion: ≥85% entity recognition F1 on git-URL / local-path
classification. Regex fallback preserves backward-compatibility for clear-cut
cases while the LLM handles ambiguous inputs.

CLI (smoke test):
    python tools/ai_augmentation/nlp_extractor.py "https://github.com/org/repo"
    python tools/ai_augmentation/nlp_extractor.py "C:/projects/myapp"
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex fallback — covers the deterministic cases the LLM also handles
# ---------------------------------------------------------------------------

_GIT_URL_RE = re.compile(r"^(https?://|git@|ssh://|git://)")


def _regex_classify(input_ref: str) -> str:
    """Fast deterministic fallback used when LLM is unavailable."""
    if _GIT_URL_RE.match(input_ref):
        return "git_url"
    return "local_path"


# ---------------------------------------------------------------------------
# NLP prompt
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """\
Classify the following repository or file-system reference into exactly one
of these input types:
  - "git_url"    : a remote git repository (HTTP/HTTPS, SSH, git@ syntax, or
                   shorthand like "org/repo" implying GitHub)
  - "local_path" : an absolute or relative path on the local file system
  - "unknown"    : cannot determine from the text alone

Reference: {input_ref}

Respond with a JSON object only — no markdown, no extra text:
{{
  "input_type": "<git_url|local_path|unknown>",
  "confidence": <0.0-1.0>,
  "reason": "<one sentence>"
}}
"""

_SYSTEM_PROMPT = (
    "You are a repository-reference classifier. "
    "Respond only with valid JSON as specified — no prose, no markdown fences."
)


# ---------------------------------------------------------------------------
# LLM-based classification
# ---------------------------------------------------------------------------


def _llm_classify(input_ref: str, timeout: int) -> str:
    """Call Claude Haiku via LLMRouter to classify the input reference."""
    from tools.llm.provider import LLMRequest
    from tools.llm.router import LLMRouter, LLMUnavailableError

    router = LLMRouter()
    request = LLMRequest(
        messages=[
            {
                "role": "user",
                "content": _CLASSIFY_PROMPT.format(input_ref=input_ref),
            }
        ],
        system_prompt=_SYSTEM_PROMPT,
        agent_id="aac-nlp-extractor",
        classification="CUI",
        max_tokens=128,
        effort="low",
        model_override="claude-haiku-4-5-20251001",
    )
    try:
        response = router.invoke("nlp_extraction", request)
    except LLMUnavailableError:
        raise
    raw = response.content or ""
    return _parse_llm_response(raw)


def _parse_llm_response(raw: str) -> str:
    """Extract input_type from LLM JSON output."""
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
    input_type = data.get("input_type", "unknown")
    if input_type not in ("git_url", "local_path", "unknown"):
        return "unknown"
    return input_type


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_input_ref(input_ref: str, *, timeout: int = 10) -> str:
    """Classify a user-provided repository reference using NLP extraction.

    Uses Claude Haiku for ambiguous inputs. Falls back to regex when the LLM
    is unavailable so that callers (e.g. api_scan) are never blocked.

    Returns one of: "git_url", "local_path", "unknown".
    """
    if not input_ref or not input_ref.strip():
        return "unknown"

    ref = input_ref.strip()

    try:
        result = _llm_classify(ref, timeout)
        logger.debug("nlp_extractor: '%s' -> '%s' (LLM)", ref[:60], result)
        return result
    except Exception as exc:
        logger.debug(
            "nlp_extractor: LLM unavailable (%s), falling back to regex", exc
        )
        result = _regex_classify(ref)
        logger.debug("nlp_extractor: '%s' -> '%s' (regex fallback)", ref[:60], result)
        return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Classify a repository reference")
    parser.add_argument("input_ref", help="The reference to classify")
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    result = classify_input_ref(args.input_ref, timeout=args.timeout)
    print(json.dumps({"input_ref": args.input_ref, "input_type": result}))
