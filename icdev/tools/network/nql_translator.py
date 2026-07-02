# [CUI // SP-CTI]
"""NQL Translator — convert natural-language text to NQL query strings.

Public API:
- ``nl_to_nql(text, context=None) -> str``
"""
from __future__ import annotations

import re

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are an NQL (Network Query Language) translator. "
    "Convert the natural-language description into a valid NQL query for network device inventory. "
    "NQL syntax: foreach d in network.devices [where <predicate>] select { d.hostname, "
    "d.hardware.platform, d.os.version, d.vendor, d.role } "
    "Return only the NQL query, no explanation or markdown fences."
)

_FALLBACK_NQL = (
    "foreach d in network.devices\n"
    "select { d.hostname, d.hardware.platform, d.os.version, d.vendor, d.role }"
)


def nl_to_nql(text: str, context: dict | None = None) -> str:
    """Convert a natural-language description to an NQL query.

    Args:
        text:    Natural-language description of what to query (e.g. advisory impact text).
        context: Optional structured advisory context with keys:
                 ``vendor``, ``affected_models`` (list), ``affected_versions`` (list).
                 When supplied, deterministic construction is attempted first.

    Returns:
        NQL query string.  Falls back to a broad device inventory query on failure.
    """
    if not text or not text.strip():
        return _FALLBACK_NQL

    # Try deterministic translation from structured context first
    if context:
        nql = _context_to_nql(context)
        if nql:
            return nql

    # LLM translation
    try:
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest
    except ImportError:
        logger.debug("nl_to_nql: LLM unavailable, using fallback")
        return _FALLBACK_NQL

    request = LLMRequest(
        messages=[{"role": "user", "content": f"Translate to NQL:\n\n{text}"}],
        system_prompt=_SYSTEM_PROMPT,
        max_tokens=256,
        temperature=0.0,
        skip_injection_scan=True,
    )

    try:
        router = get_router()
        response = router.invoke("nql_translation", request)
        content = (response.content or "").strip()
        # Strip code fences if present
        match = re.search(r"```(?:nql)?\s*(.*?)```", content, re.DOTALL)
        if match:
            content = match.group(1).strip()
        if content.lower().startswith("foreach"):
            return content
        logger.warning("nl_to_nql: LLM response is not NQL; using fallback")
        return _FALLBACK_NQL
    except Exception as exc:
        logger.error("nl_to_nql: LLM invocation failed: %s", exc)
        return _FALLBACK_NQL


def _context_to_nql(context: dict) -> str:
    """Build an NQL query deterministically from a structured advisory context dict."""
    vendor = (context.get("vendor") or "").strip().lower()
    models: list[str] = context.get("affected_models") or []
    versions: list[str] = context.get("affected_versions") or []

    if not models and not versions:
        return ""

    predicates: list[str] = []
    if vendor and vendor not in ("", "other", "unknown"):
        predicates.append(f'd.vendor == "{vendor}"')
    if models:
        models_lit = "[" + ", ".join(f'"{m}"' for m in models) + "]"
        predicates.append(f"d.hardware.platform in {models_lit}")
    if versions:
        versions_lit = "[" + ", ".join(f'"{v}"' for v in versions) + "]"
        predicates.append(f"d.os.version in {versions_lit}")

    if not predicates:
        return ""

    where = " and ".join(predicates)
    return (
        "foreach d in network.devices\n"
        f"where {where}\n"
        "select { d.hostname, d.hardware.platform, d.os.version, d.role, d.management.ip }"
    )
