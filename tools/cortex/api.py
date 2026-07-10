# CUI // SP-CTI
"""ICDEV Cortex facade — complete / classify / extract over the LLM router.

First 3 of the 7 Cortex facade functions (ctx-core-02). Callers import one
namespace (``tools.cortex``) instead of wiring LLMRouter/LLMRequest per call
site. Every function returns a :class:`CortexResult`.

Routing is config-driven: each facade function passes a logical function name
(``cortex_complete`` / ``cortex_classify`` / ``cortex_extract``) to
``LLMRouter.invoke``, which resolves the provider chain from
``args/llm_config.yaml``. This module never names a concrete provider model.

``classify()`` degrades to the deterministic heuristics in
``tools/rag/query_classifier.py`` when the router raises, so it stays usable
in air-gap / no-LLM environments. ``complete()`` and ``extract()`` have no
deterministic equivalent and propagate router errors to the caller.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Union

from tools.logging.icdev_logger import get_logger

from .schemas import CortexContext, CortexResult, GovernanceReport

logger = get_logger("icdev.cortex.api")

# Logical routing function names — resolved via args/llm_config.yaml `routing:`.
CORTEX_COMPLETE_FUNCTION = "cortex_complete"
CORTEX_CLASSIFY_FUNCTION = "cortex_classify"
CORTEX_EXTRACT_FUNCTION = "cortex_extract"

# provider/model markers for the deterministic classify() degradation path
_FALLBACK_PROVIDER = "deterministic"
_FALLBACK_MODEL = "query_classifier"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _get_router():
    """Late-bound router lookup.

    Resolved through the ``tools.llm`` module dict at call time so tests can
    monkeypatch ``tools.llm.get_router`` (shim-aware pattern: patch via
    ``importlib.import_module('tools.llm')`` + ``setattr``).
    """
    from tools.llm import get_router

    return get_router()


def _coerce_context(ctx: Union[CortexContext, dict, None]) -> CortexContext:
    if ctx is None:
        return CortexContext()
    if isinstance(ctx, dict):
        return CortexContext.from_dict(ctx)
    return ctx


def _build_request(
    content: str,
    ctx: Union[CortexContext, dict, None],
    *,
    system_prompt: str = "",
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    output_schema: Optional[Dict] = None,
):
    """Build an LLMRequest with the CortexContext threaded into it."""
    from tools.llm.provider import LLMRequest

    context = _coerce_context(ctx)
    request = LLMRequest(
        messages=[{"role": "user", "content": content}],
        system_prompt=system_prompt,
        tenant_id=context.tenant_id,
        classification=context.classification or "CUI",
    )
    if max_tokens is not None:
        request.max_tokens = int(max_tokens)
    if temperature is not None:
        request.temperature = float(temperature)
    if output_schema is not None:
        request.output_schema = output_schema
    return request


def _result_from_response(response, *, text: Optional[str] = None, elapsed_ms: int = 0) -> CortexResult:
    """Map an LLMResponse's accounting fields into a CortexResult."""
    return CortexResult(
        text=text if text is not None else (response.content or ""),
        citations=[],
        governance=GovernanceReport(),
        provider=response.provider or "",
        model=response.model_id or "",
        cost=float(response.cost_usd or 0.0),
        latency_ms=int(response.duration_ms or elapsed_ms),
        grounded=False,
    )


def _match_label(content: str, labels: List[str]) -> Optional[str]:
    """Map raw LLM output back onto one of the caller's labels."""
    if not content:
        return None
    cleaned = content.strip().strip("\"'`*.,:;!").lower()
    by_lower = {label.lower(): label for label in labels}
    if cleaned in by_lower:
        return by_lower[cleaned]
    lowered = content.lower()
    # Longest label first so overlapping names ("bug", "bug_fix") resolve to
    # the more specific one.
    for label in sorted(labels, key=len, reverse=True):
        if label.lower() in lowered:
            return label
    return None


def _parse_json_payload(content: str) -> Optional[Any]:
    """Parse a JSON object out of LLM text, tolerating markdown code fences."""
    if not content:
        return None
    text = content.strip()
    if "```" in text:
        start = text.index("```")
        start = text.index("\n", start) + 1 if "\n" in text[start:] else start + 3
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Facade functions
# ---------------------------------------------------------------------------
def complete(
    prompt: str,
    function: str = CORTEX_COMPLETE_FUNCTION,
    ctx: Union[CortexContext, dict, None] = None,
    *,
    system_prompt: str = "",
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> CortexResult:
    """Free-form completion via the config-routed LLM chain.

    Args:
        prompt: User prompt text.
        function: Logical routing function name (args/llm_config.yaml key).
        ctx: CortexContext (or dict) whose tenant_id/classification are
            threaded into the LLMRequest for RLS and redaction policy.

    Returns:
        CortexResult with provider/model/cost/latency_ms populated from the
        LLMResponse accounting fields. Router errors propagate.
    """
    request = _build_request(
        prompt,
        ctx,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    started = time.perf_counter()
    response = _get_router().invoke(function, request)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return _result_from_response(response, elapsed_ms=elapsed_ms)


def classify(
    text: str,
    labels: List[str],
    ctx: Union[CortexContext, dict, None] = None,
    function: str = CORTEX_CLASSIFY_FUNCTION,
) -> CortexResult:
    """Classify ``text`` into exactly one of ``labels``.

    Tries the config-routed LLM chain first. When the router raises (offline,
    air-gap, exhausted chain) — or the LLM answer maps to none of the labels —
    degrades to the deterministic heuristics in tools/rag/query_classifier.py.

    Returns:
        CortexResult whose ``text`` is the chosen label. The degradation path
        is marked provider="deterministic", model="query_classifier".
    """
    labels = [str(label).strip() for label in labels if str(label).strip()]
    if not labels:
        raise ValueError("classify() requires at least one non-empty label")

    prompt = (
        "Classify the following text into exactly one of these labels: "
        f"{', '.join(labels)}.\n"
        "Respond with the chosen label only — no explanation, no punctuation.\n\n"
        f"TEXT:\n{text}"
    )
    started = time.perf_counter()
    try:
        response = _get_router().invoke(function, _build_request(prompt, ctx))
        label = _match_label(response.content, labels)
        if label is not None:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return _result_from_response(response, text=label, elapsed_ms=elapsed_ms)
        logger.warning("cortex.classify: LLM answer matched no label — degrading to heuristics")
    except Exception as exc:
        logger.warning("cortex.classify: router unavailable (%s) — degrading to heuristics", exc)

    # Deterministic degradation: query_classifier's taxonomy heuristics.
    from tools.rag.query_classifier import classify_query

    heuristic = classify_query(text)
    label = _match_label(heuristic.get("label", ""), labels) or _match_label(text, labels) or labels[0]
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return CortexResult(
        text=label,
        citations=[],
        governance=GovernanceReport(),
        provider=_FALLBACK_PROVIDER,
        model=_FALLBACK_MODEL,
        cost=0.0,
        latency_ms=elapsed_ms,
        grounded=False,
    )


def extract(
    text: str,
    schema: Dict,
    ctx: Union[CortexContext, dict, None] = None,
    function: str = CORTEX_EXTRACT_FUNCTION,
) -> CortexResult:
    """Extract structured data from ``text`` conforming to a JSON ``schema``.

    The schema is passed both as ``LLMRequest.output_schema`` (for providers
    with native structured output) and inline in the prompt (for providers
    without). Returns a CortexResult whose ``text`` is the extracted JSON
    object serialized as a string; falls back to the raw completion text when
    no JSON payload can be parsed. Router errors propagate.
    """
    prompt = (
        "Extract structured data from the text below as a single JSON object "
        "conforming to the JSON schema. Respond with the JSON object only.\n\n"
        f"JSON SCHEMA:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"TEXT:\n{text}"
    )
    request = _build_request(prompt, ctx, output_schema=schema)
    started = time.perf_counter()
    response = _get_router().invoke(function, request)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    payload = response.structured_output
    if payload is None:
        payload = _parse_json_payload(response.content)
    text_out = json.dumps(payload, ensure_ascii=False) if payload is not None else (response.content or "")
    return _result_from_response(response, text=text_out, elapsed_ms=elapsed_ms)
