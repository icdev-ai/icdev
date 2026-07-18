# [TEMPLATE: CUI // SP-CTI]
"""Governed LLM adapter for the Security Canvas.

The Security Canvas historically called Ollama directly with hardcoded model
IDs (in agent.py and nl_query.py), bypassing the config-driven routing,
governance, redaction, and budget caps that every other ICDEV(TM) LLM call
site flows through. This adapter is the single, thin seam onto
``tools.llm.router.LLMRouter`` — no model IDs live here; routing, provider
selection, and fallbacks are resolved from ``args/llm_config.yaml`` under the
``security_canvas`` function name.

Design contract (shx-llm-01):
  * ``generate()`` returns the model's text on success and ``None`` on ANY
    router/provider failure. Callers keep their deterministic fallbacks — the
    adapter NEVER raises into a request path.
  * One ``LLMRouter`` is constructed lazily and cached process-wide.
  * The routing function name is fixed to ``security_canvas`` (the ``purpose``
    argument is advisory metadata only, not a routing key), so governance is
    always applied.

Follow-up tasks shx-llm-02 / shx-llm-03 migrate agent.py and nl_query.py onto
``generate()``.
"""

from __future__ import annotations

from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.security_canvas.llm_adapter")

# ICDEV(TM) routing function name — resolves to the chain declared under
# routing.security_canvas in args/llm_config.yaml. NOT a model ID.
_ROUTING_FUNCTION = "security_canvas"

# Process-wide cached router (lazy). Constructed on first use so importing this
# module never forces config load / provider probing.
_router = None


def _get_router():
    """Return the cached :class:`LLMRouter`, constructing it once.

    Returns ``None`` if the router cannot even be imported/constructed (e.g.
    a stripped-down deployment) so ``generate()`` degrades to ``None`` rather
    than raising.
    """
    global _router
    if _router is not None:
        return _router
    try:
        from tools.llm.router import LLMRouter

        _router = LLMRouter()
    except Exception as exc:  # pragma: no cover - defensive import/construct guard
        logger.warning("security_canvas LLM adapter: router unavailable: %s", exc)
        _router = None
    return _router


def generate(
    prompt: str,
    purpose: str = "security_canvas",
    system: Optional[str] = None,
    **opts,
) -> Optional[str]:
    """Generate text for a Security Canvas call via the governed LLM router.

    Args:
        prompt: The user prompt / question text.
        purpose: Advisory label for logging/telemetry only — does NOT change
            routing (the routing function name is always ``security_canvas``).
        system: Optional system prompt.
        **opts: Optional overrides forwarded to the ``LLMRequest`` when present.
            Recognized keys: ``max_tokens`` (int), ``temperature`` (float),
            ``effort`` (str). Unknown keys are ignored.

    Returns:
        The model's text response, or ``None`` on any router/provider failure
        (missing config, no available provider, network error, etc.). Callers
        MUST treat ``None`` as "LLM unavailable" and use their deterministic
        fallback.
    """
    router = _get_router()
    if router is None:
        return None

    try:
        from tools.llm.provider import LLMRequest

        req_kwargs = {
            "messages": [{"role": "user", "content": prompt or ""}],
            "system_prompt": system or "",
        }
        if "max_tokens" in opts and opts["max_tokens"] is not None:
            req_kwargs["max_tokens"] = int(opts["max_tokens"])
        if "temperature" in opts and opts["temperature"] is not None:
            req_kwargs["temperature"] = float(opts["temperature"])
        if opts.get("effort"):
            req_kwargs["effort"] = str(opts["effort"])

        request = LLMRequest(**req_kwargs)
        response = router.invoke(_ROUTING_FUNCTION, request)
        text = getattr(response, "content", None)
        if not text:
            logger.debug(
                "security_canvas LLM adapter: empty response for purpose=%s", purpose
            )
            return None
        return text
    except Exception as exc:
        # Any failure — unavailable provider, redaction refusal, network fault,
        # budget block — degrades to None so the caller's deterministic path runs.
        logger.warning(
            "security_canvas LLM adapter: generate failed (purpose=%s): %s",
            purpose,
            exc,
        )
        return None
