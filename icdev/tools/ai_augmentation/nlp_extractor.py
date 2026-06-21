# CUI // SP-CTI
"""NLP extractor for regex_user_input patterns.

Uses an LLM (default: claude-haiku-4-5-20251001) to enrich AST/Semgrep-detected
regex call sites with semantic context: intent classification, inferred input
type, user-facing flag, and a one-sentence NLP alternative recommendation.

Public API:
    enrich_regex_patterns(patterns, source_root=None) -> list[dict]

Falls back silently when the LLM is unavailable or ICDEV_NO_LLM is set.
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import yaml

_CONFIG_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "args" / "aac_config.yaml"

_FALLBACK_MAX_ENRICH = 10
_FALLBACK_SNIPPET_CTX = 10
_FALLBACK_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = (
    "You are a code-intelligence assistant. Analyze the provided code snippet "
    "containing a regex operation and return a JSON object with exactly these keys:\n"
    '- "intent": one of "validation", "extraction", "transformation", "matching", "unknown"\n'
    '- "input_type": short label for what the regex operates on, e.g. "email", "url", '
    '"phone", "date", "path", "user_text", "generic"\n'
    '- "is_user_facing": true if the regex likely processes end-user-supplied input, '
    "false otherwise\n"
    '- "nlp_alternative": one sentence describing how NLP/AI could replace or improve '
    "this regex\n"
    "Return ONLY valid JSON with no markdown fences."
)


def _load_config() -> dict[str, Any]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return cfg.get("nlp_extractor", {})
    except Exception:
        return {}


def _read_snippet(module_path: str, line_start: int, context: int) -> str:
    """Return ±context lines around line_start (1-based) from the source file."""
    try:
        lines = pathlib.Path(module_path).read_text(encoding="utf-8", errors="replace").splitlines()
        lo = max(0, line_start - context - 1)
        hi = min(len(lines), line_start + context)
        numbered = [f"{lo + i + 1}: {line}" for i, line in enumerate(lines[lo:hi])]
        return "\n".join(numbered)
    except OSError:
        return ""


def _call_llm(snippet: str, call_expr: str) -> dict[str, Any]:
    """Invoke the LLM router and return the parsed enrichment dict.

    Returns an empty dict on any failure so callers can skip enrichment
    without crashing.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except ImportError:
        return {}

    try:
        router = LLMRouter()
    except Exception:
        return {}

    user_msg = (
        f"Regex call: `{call_expr}`\n\nCode snippet:\n```\n{snippet}\n```\n\n"
        "Return the JSON analysis."
    )
    try:
        req = LLMRequest(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=_SYSTEM_PROMPT,
            agent_id="nlp_extractor",
            effort="low",
            max_tokens=256,
            classification="CUI",
        )
        response = router.invoke("code_analysis", req)
        content = getattr(response, "content", None) or str(response)
        content = content.strip()
        # Strip markdown fences if the model wraps its JSON
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(content)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    return {}


def enrich_regex_patterns(
    patterns: list[dict],
    source_root: str | None = None,
    max_enrich: int | None = None,
    model: str | None = None,
) -> list[dict]:
    """Enrich regex_user_input patterns with LLM-derived semantic context.

    Patterns with pattern_type != "regex_user_input" pass through unchanged.
    When enrichment succeeds, a "nlp" sub-dict is added to pattern_detail
    containing: intent, input_type, is_user_facing, nlp_alternative.

    Args:
        patterns:    Pattern dicts from detect_patterns().
        source_root: Base path used to resolve relative module_path values.
        max_enrich:  Cap on LLM calls per invocation (default from aac_config.yaml).
        model:       Unused — model selection is delegated to LLMRouter config.
                     Accepted for forward-compatibility only.

    Returns:
        The same list with enriched pattern_detail fields (modified in-place).
    """
    if os.environ.get("ICDEV_NO_LLM", "").lower() in ("1", "true", "yes"):
        return patterns

    cfg = _load_config()
    if max_enrich is None:
        max_enrich = int(cfg.get("max_enrich", _FALLBACK_MAX_ENRICH))
    snippet_ctx = int(cfg.get("snippet_context_lines", _FALLBACK_SNIPPET_CTX))

    enriched_count = 0
    for pat in patterns:
        if pat.get("pattern_type") != "regex_user_input":
            continue
        if enriched_count >= max_enrich:
            break

        module_path = pat.get("module_path", "")
        if source_root and module_path and not pathlib.Path(module_path).is_absolute():
            module_path = str(pathlib.Path(source_root) / module_path)

        line_start = pat.get("line_start", 0)
        call_expr = pat.get("pattern_detail", {}).get("call", "re.search")

        snippet = _read_snippet(module_path, line_start, snippet_ctx)
        nlp_data = _call_llm(snippet, call_expr)
        if nlp_data:
            pat.setdefault("pattern_detail", {})["nlp"] = nlp_data
            enriched_count += 1

    return patterns
