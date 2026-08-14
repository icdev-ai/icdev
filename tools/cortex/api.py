# CUI // SP-CTI
"""ICDEV Cortex facade — ask / complete / classify / extract over the LLM router.

Import Cortex capabilities from here (or from ``tools.cortex`` directly);
the per-capability modules (``analyst``, …) are implementation detail.

First 3 of the 8 Cortex facade functions (ctx-core-02). Callers import one
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

import functools
import inspect
import json
import time
from typing import Any, Dict, List, Optional, Union

from tools.logging.icdev_logger import get_logger

# Analyst endpoint (ctx-analyst-01/02/03). Imported as the raw impl so the
# public ``ask`` on this module is the governance-wrapped facade (below);
# the error types are re-exported so callers keep one import surface.
from .analyst import CortexAnalystError, CortexQueryBlocked  # noqa: F401 - re-exports
from .analyst import ask as _ask_impl

# The enforced TRUST chain (ctx-govern-01). Every public facade on this module
# runs through it — see _governed_facade / CORTEX_FACADES below.
from .governance import GovernancePipeline, record_llm_call

# Unified search backend (ctx-search-01/02/04). Imported as the raw impl for
# the same reason as ``ask``; the public ``search`` below is governance-wrapped.
from .search_service import search as _search_impl

# Search endpoint (ctx-search-01..04): the public ``cortex_api.search`` the
# intent router / chat surface dispatch to is the GOVERNED facade defined below
# (search = _governed_facade(...)(_search_impl)), not the raw search_service
# import (aliased above as _search_impl). No ungoverned re-export here — that
# would let callers bypass the TRUST gate chain.

# Behavior config + air-gap invariant live in .config (this module must stay
# free of provider/model references — see test_no_model_id_literals_in_module).
# Re-exported here so callers keep importing everything from tools.cortex.api.
from .config import (  # noqa: F401 - re-exports
    AIRGAP_ENV_VAR,
    CORTEX_ROUTING_FUNCTIONS,
    CortexAirgapError,
    airgap_active,
    airgap_exclusions,
    assert_airgap_ready,
    load_cortex_config,
    resolve_cortex_config_path,
)
from .schemas import (  # noqa: F401 - re-exports
    CORTEX_BACKENDS,
    Citation,
    CortexContext,
    CortexResult,
    CortexSearchResult,
    GovernanceReport,
)

logger = get_logger("icdev.cortex.api")

# Logical routing function names — resolved via args/llm_config.yaml `routing:`.
CORTEX_COMPLETE_FUNCTION = "cortex_complete"
CORTEX_CLASSIFY_FUNCTION = "cortex_classify"
CORTEX_EXTRACT_FUNCTION = "cortex_extract"
CORTEX_SEARCH_REWRITE_FUNCTION = "cortex_search_rewrite"
CORTEX_ANALYST_FUNCTION = "cortex_analyst"
CORTEX_REASON_FUNCTION = "cortex_complete"  # reasoning reuses complete's routing
# analyst.ask(summarize=True) prose summary. Declared as its OWN routing
# function rather than reusing "summarization": that name exists only under
# `task_categories:` in args/llm_config.yaml, and LLMRouter resolves
# `routing.get(fn, routing.get("default", {}))` — so an undeclared function
# silently took routing.default, whose chain is cloud-first. See ctx-trust-01.
CORTEX_SUMMARIZE_FUNCTION = "cortex_summarize"

# cortex.reason modes -> LLMRouter multi-step orchestration methods.
_REASON_MODES = {
    "cot": "invoke_chain_of_thought",
    "debate": "invoke_chain_of_debate",
    "council": "invoke_council",
}

# cortex.agent execution modes (hgx-cx-01). Validated exactly like _REASON_MODES:
# dispatch used to be a single `use_team` boolean, so ANY unrecognised mode —
# a typo, or "graph" before it existed — fell through to single-agent execution
# silently. An unknown mode is now a ValueError, not a different execution.
_AGENT_MODES = frozenset({"auto", "team", "single", "graph"})

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


# ---------------------------------------------------------------------------
# Governance enforcement — no bypass path (ctx-govern-04)
# ---------------------------------------------------------------------------
# The names of every public Cortex facade. The introspection guard in
# tests/cortex/test_api_governed.py asserts this set equals the public
# functions defined in this module and that each carries __cortex_governed__,
# so a new facade added without wrapping fails the suite instead of silently
# bypassing the TRUST chain.
CORTEX_FACADES = (
    "complete",
    "classify",
    "extract",
    "search",
    "ask",
    "govern",
    "agent",
    "reason",
)

# Key under which the outer pipeline report is stashed on results whose native
# governance must not be clobbered (``ask`` carries the analyst TRUST report;
# ``search`` returns a list). Distinct from ``CortexResult.governance``.
_OUTER_GOVERNANCE_KEY = "pipeline_governance"


def _stash_outer_report(result: Any, report: GovernanceReport) -> None:
    """Surface the outer pipeline report on a non-attach result.

    ``ask``/``search`` wrap with ``attach=False`` so their native result stays
    pristine (the analyst report on ``result.governance``; the search list
    unchanged). The enforced-pipeline report is still observable here under
    ``_OUTER_GOVERNANCE_KEY`` in the result's metadata.
    """
    report_dict = report.to_dict()
    if isinstance(result, CortexResult):
        result.metadata[_OUTER_GOVERNANCE_KEY] = report_dict
    elif isinstance(result, list):
        for item in result:
            meta = getattr(item, "metadata", None)
            if isinstance(meta, dict):
                meta[_OUTER_GOVERNANCE_KEY] = report_dict


def _governed_facade(
    operation: str,
    *,
    text_param: str,
    retrieval: bool = False,
    attach: bool = True,
    sources_param: Optional[str] = None,
    return_report: bool = False,
):
    """Route a Cortex facade through :class:`GovernancePipeline`.

    ``text_param`` names the argument screened as the governed prompt (the
    user text — ``prompt``/``text``/``query``/``question``/``goal``); the
    pipeline's input redaction masks it before the wrapped function ever sees
    it. ``sources_param`` (when set) names the argument carrying the injected
    context the output is grounded against. Every facade accepts ``ctx``,
    which is threaded into the pipeline for RLS/redaction policy and audit.

    ``retrieval`` is the facade's DEFAULT posture, not a fixed property of it.
    A facade whose groundedness varies per call (``complete``: a chat turn with
    RAG hits is retrieval-backed, the same turn without them is generative)
    declares ``retrieval=False`` and escalates per call by supplying
    ``sources_param``. Escalation is what makes the two grounding gates grade a
    turn that DID inject evidence; without sources the call still skips them,
    because scoring an answer against no context manufactures a defect every
    time (cxo-adopt-02).

    ``attach=True`` writes the report onto the returned CortexResult
    (complete/classify/extract/agent). ``attach=False`` leaves the native
    result untouched and stashes the report via _stash_outer_report
    (ask/search). ``return_report=True`` returns the GovernanceReport itself
    instead of the wrapped result (``govern``).

    The wrapper is stamped ``__cortex_governed__`` so the introspection guard
    can prove no public facade bypasses the chain.
    """

    def decorate(func: Any):
        sig = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            ctx = _coerce_context(bound.arguments.get("ctx"))
            text = str(bound.arguments.get(text_param) or "")
            sources = bound.arguments.get(sources_param) if sources_param else None
            # Per-call escalation (see the decorator docstring). Only a facade
            # that declared sources_param can reach this, and only on the calls
            # that actually passed evidence — every existing retrieval=False
            # facade without sources_param is byte-for-byte unaffected.
            call_retrieval = retrieval or bool(sources)

            # Response cache (opt-in). Serve a prior GOVERNED result for an
            # identical request. The key folds tenant/classification/domain/
            # air_gap so a hit never crosses those boundaries; a hit is still
            # audited (NIST-AU/metrics). Never applies to govern (return_report).
            cache_key = None
            if not return_report:
                try:
                    from . import cache as _rc
                    if _rc.is_enabled() and _rc.cacheable(operation):
                        extra = {k: v for k, v in bound.arguments.items()
                                 if k not in (text_param, "ctx")}
                        cache_key = _rc.make_key(operation, text, ctx, extra)
                        hit = _rc.get_by_key(cache_key)
                        if hit is not None:
                            _rc.audit_hit(operation, ctx)
                            return hit
                except Exception as _cexc:  # noqa: BLE001 — cache must never break a call
                    logger.debug("cortex cache read skipped: %s", _cexc)
                    cache_key = None

            def _operation(governed_text: str):
                bound.arguments[text_param] = governed_text
                return func(*bound.args, **bound.kwargs)

            result, report = GovernancePipeline(operation=operation).wrap(
                _operation,
                ctx,
                prompt=text,
                context_sources=sources,
                retrieval=call_retrieval,
                attach=attach,
            )
            if return_report:
                return report
            if not attach:
                _stash_outer_report(result, report)
            # Store only successful, non-blocked governed results — and never a
            # DEGRADED one. A search whose backend failed carries `.errors`
            # (search_service.BackendResults); caching it would turn a momentary
            # embedding-provider outage into a TTL-long one served from memory
            # after the provider recovered (ctx-perf-04).
            degraded = bool(getattr(result, "errors", None))
            if cache_key is not None and not report.blocked and not degraded:
                try:
                    from . import cache as _rc
                    _rc.put_by_key(cache_key, result, operation)
                except Exception as _cexc:  # noqa: BLE001
                    logger.debug("cortex cache write skipped: %s", _cexc)
            return result

        wrapper.__cortex_governed__ = True
        wrapper.__cortex_operation__ = operation
        # Anchor the wrapper to this module (functools.wraps copies the impl's
        # __module__) so the introspection guard's module filter sees it.
        wrapper.__module__ = __name__
        return wrapper

    return decorate


# ACE / agent-loop seams — one thin patchable indirection per backend so
# tests can inject a stub controller / loop without importing the heavy
# subsystems (mirrors governance._gate_* seams).
def _get_ace_controller():
    """Late-bound ACE controller singleton for team launches."""
    from tools.ace.controller import ACEController

    return ACEController.get_instance()


def _start_graph_run(workflow_id: str, project_id: str, inputs: dict) -> str:
    """Late-bound Studio DAG start for ``mode="graph"`` — returns the run_id.

    Dispatches to the EXISTING durable runtime
    (``tools.studio.workflow_runner.start_run``): restart-safe resume, human
    approval gates and per-node tool authorization are already implemented
    there, so Cortex adds an entry point rather than a second engine. The
    start is non-blocking, exactly like the ACE team launch — the caller polls
    the run.
    """
    from tools.studio.workflow_runner import start_run

    return start_run(workflow_id, project_id=project_id, inputs=inputs)


def _run_single_agent(
    router,
    *,
    system_prompt: str,
    user_prompt: str,
    tools: list,
    tool_handlers: dict,
    llm_function: str,
    max_iterations: int,
    ctx: CortexContext,
    rubric: bool = False,
):
    """Late-bound single-agent loop for goal execution.

    Imports the canonical ``icdev.tools.llm.agent_loop`` directly. The ``icdev.``
    prefix is deliberate and is the form the repo's import convention calls
    canonical: in a source checkout the root ``tools/`` shim and
    ``icdev/tools/`` load as *separate*
    module objects, so only the canonical spelling is guaranteed to bind the same
    object the rest of the platform holds. ``tools/llm/agent_loop.py`` happens to
    preserve identity today (it is a pure re-export shim, added in dba8d4b59
    after the physical copy it replaced had drifted out of sync), but that is a
    property of one file rather than of the namespace. See
    docs/features/cortex-unified-ai-layer.md, "Import namespace".

    When ``rubric`` is true the loop is graded in real time by the
    delivery-pipeline rubric (build -> gates -> revise) via
    ``run_agent_loop_with_rubric``; conformance review is off (no kanban
    acceptance criteria for an ad-hoc goal). Returns an ``AgentLoopResult`` in
    both paths so callers are unchanged.
    """
    from icdev.tools.llm.agent_loop import run_agent_loop

    loop_kwargs = dict(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tools=tools,
        tool_handlers=tool_handlers,
        llm_function=llm_function,
        max_iterations=max_iterations,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
    )

    if not rubric:
        return run_agent_loop(router, **loop_kwargs)

    from icdev.tools.llm.agent_loop import run_agent_loop_with_rubric
    from icdev._paths import get_project_root
    from tools.workflow.pipeline_grader import make_pipeline_grader

    grader = make_pipeline_grader(
        cwd=str(get_project_root()),
        task_id=ctx.session_id or "cortex.agent",
        run_e2e=False,
        run_conformance=False,
    )
    rubric_result = run_agent_loop_with_rubric(
        router,
        grader=grader,
        max_grading_iterations=3,
        **loop_kwargs,
    )
    return rubric_result.result


def _apply_domain_persona(context: CortexContext, system_prompt: str) -> str:
    """Prepend the active domain lens's persona to ``system_prompt``.

    Lazy import (the domains package must not be a hard dependency of the base
    facade). No-op — returns ``system_prompt`` unchanged — when the context has
    no domain, the domain defines no persona, or the lens machinery is
    unavailable. This is the seam that makes ``domains.apply_persona`` actually
    take effect for ``complete``.
    """
    if not getattr(context, "domain", ""):
        return system_prompt
    try:
        from .domains import apply_persona
        return apply_persona(context, system_prompt)
    except Exception as exc:  # noqa: BLE001 — a lens miss must never break completion
        logger.debug("cortex: domain persona not applied: %s", exc)
        return system_prompt


def _sanitize_history(history) -> list:
    """Coerce a caller-supplied history into a clean list of chat turns.

    Keeps only well-formed {role, content} dicts with a known role; the current
    user turn is NOT part of history (it is the governed ``content`` appended
    after). A malformed history degrades to empty rather than raising.
    """
    out: list = []
    for m in history or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).strip().lower()
        if role not in ("user", "assistant", "system"):
            continue
        out.append({"role": role, "content": str(m.get("content", "") or "")})
    return out


def _build_request(
    content: str,
    ctx: Union[CortexContext, dict, None],
    *,
    system_prompt: str = "",
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    output_schema: Optional[Dict] = None,
    history: Optional[List[dict]] = None,
    model: Optional[str] = None,
):
    """Build an LLMRequest with the CortexContext threaded into it.

    ``history`` (prior conversation turns) is prepended so multi-turn callers
    (chat) keep their context; the governed ``content`` is always the final
    user turn, so input screening/redaction still applies to the new input while
    prior — already-governed — turns pass through. ``model`` pins a specific
    model (chat's per-session override) when the caller needs it.
    """
    from tools.llm.provider import LLMRequest

    context = _coerce_context(ctx)
    messages = _sanitize_history(history)
    messages.append({"role": "user", "content": content})
    request = LLMRequest(
        messages=messages,
        system_prompt=system_prompt,
        tenant_id=context.tenant_id,
        classification=context.classification or "CUI",
        # Attribute every Cortex LLM call to a budget/rate key so the router's
        # check_budget()/rate_gate bind (an empty agent_id keys to no budget row).
        agent_id=(context.agent_id
                  or f"cortex:{context.tenant_id or 'default'}"),
    )
    if max_tokens is not None:
        request.max_tokens = int(max_tokens)
    if temperature is not None:
        request.temperature = float(temperature)
    if output_schema is not None:
        request.output_schema = output_schema
    if model and hasattr(request, "model"):
        request.model = model
    # Trusted first-party content skips the router-level injection scan too, so
    # the behaviour matches the pre-facade raw router.invoke(skip_injection_scan=
    # True) call sites exactly. The governance pipeline already skipped its own
    # pre-check/redaction gates for this context; this keeps the provider call
    # consistent. Set defensively (older LLMRequest builds may lack the field).
    if getattr(context, "trusted_content", False) and hasattr(request, "skip_injection_scan"):
        request.skip_injection_scan = True
    return request


def _invoke(function: str, request, context: CortexContext):
    """Invoke the router, forcing local-only resolution when air-gapped.

    When ICDEV_AIRGAP=1 (or context.air_gap) is set, every model_id that only
    resolves through a non-local provider is passed as exclude_model_ids so
    chain-walking skips straight to the local tier. The kwarg is omitted
    entirely otherwise, keeping plain calls signature-compatible.
    """
    router = _get_router()
    exclusions = airgap_exclusions(context)
    if exclusions:
        response = router.invoke(function, request, exclude_model_ids=exclusions)
    else:
        response = router.invoke(function, request)
    # Attribute this provider call to the enclosing governed operation
    # (ctx-obs-01). Facades returning a CortexResult already carry their own
    # accounting and are unaffected; this is what makes spend inside an
    # operation returning something else - the rewrite call inside search -
    # visible in /cortex/metrics. A no-op outside a governed call.
    record_llm_call(response)
    return response


def _num(value, cast, default=0):
    """Coerce a provider accounting field to a number, defaulting on garbage.

    A provider (or a test double) that returns a non-numeric cost/latency/token
    field must not crash the facade — accounting is best-effort telemetry.
    """
    try:
        return cast(value)
    except (TypeError, ValueError):
        return cast(default)


def _result_from_response(response, *, text: Optional[str] = None, elapsed_ms: int = 0) -> CortexResult:
    """Map an LLMResponse's accounting fields into a CortexResult."""
    provider = getattr(response, "provider", "") or ""
    model = getattr(response, "model_id", "") or ""
    return CortexResult(
        text=text if text is not None else (getattr(response, "content", "") or ""),
        citations=[],
        governance=GovernanceReport(),
        provider=provider if isinstance(provider, str) else "",
        model=model if isinstance(model, str) else "",
        cost=_num(getattr(response, "cost_usd", 0.0) or 0.0, float),
        latency_ms=_num(getattr(response, "duration_ms", 0) or elapsed_ms, int),
        input_tokens=_num(getattr(response, "input_tokens", 0) or 0, int),
        output_tokens=_num(getattr(response, "output_tokens", 0) or 0, int),
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


def _validate_against_schema(payload: Any, schema: Optional[Dict]) -> tuple:
    """Best-effort JSON-Schema conformance check for cortex.extract.

    Returns (ok, error_message). If jsonschema is unavailable or no schema was
    supplied, returns (True, "") — validation is a check, never a hard dependency.
    """
    if not schema:
        return True, ""
    try:
        import jsonschema  # noqa: PLC0415 — optional dependency
    except Exception:  # noqa: BLE001
        return True, ""
    try:
        jsonschema.validate(instance=payload, schema=schema)
        return True, ""
    except jsonschema.ValidationError as exc:
        return False, (str(exc).splitlines()[0] if str(exc) else "schema mismatch")[:200]
    except Exception as exc:  # noqa: BLE001 — malformed schema, etc.
        return False, f"schema validation error: {exc}"[:200]


# ---------------------------------------------------------------------------
# Facade functions
# ---------------------------------------------------------------------------
@_governed_facade(
    "cortex.complete",
    text_param="prompt",
    retrieval=False,
    sources_param="context_sources",
)
def complete(
    prompt: str,
    function: str = CORTEX_COMPLETE_FUNCTION,
    ctx: Union[CortexContext, dict, None] = None,
    *,
    system_prompt: str = "",
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    history: Optional[List[dict]] = None,
    model: Optional[str] = None,
    context_sources: Any = None,
) -> CortexResult:
    """Free-form completion via the config-routed LLM chain.

    Args:
        prompt: The current user turn. This is what the governance pipeline
            screens/redacts; it is always the final message sent.
        function: Logical routing function name (args/llm_config.yaml key).
        ctx: CortexContext (or dict) whose tenant_id/classification are
            threaded into the LLMRequest for RLS and redaction policy.
        history: Prior conversation turns ([{role, content}, ...]) for
            multi-turn callers (chat). Prepended before ``prompt``; already
            governed in their own turns, so they pass through as context.
        model: Optional model pin (chat's per-session override).
        context_sources: Evidence the caller ALREADY injected into
            ``system_prompt``/``history`` (RAG hits, KG block, ...), declared so
            the citation and content grounding gates can grade this answer
            against it. Consumed by the governance decorator — this function
            body never reads it, and passing it adds nothing to the request.
            Default None keeps every existing caller generative: the two
            grounding gates record ``skip``, exactly as before. Accepts what
            ``governance._allowed_source_ids`` accepts (an int count, or an
            iterable of id strings / dicts / CortexSearchResult).

    Returns:
        CortexResult with provider/model/cost/latency_ms populated from the
        LLMResponse accounting fields. Router errors propagate.
    """
    context = _coerce_context(ctx)
    # Domain lens: prepend the active domain's persona to the caller's system
    # prompt (a no-op when the domain has no persona / general mode). Only
    # free-form `complete` gets the persona — `classify`/`extract` stay
    # structured so a verbose persona can't derail their terse/JSON output.
    system_prompt = _apply_domain_persona(context, system_prompt)
    request = _build_request(
        prompt,
        context,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        history=history,
        model=model,
    )
    started = time.perf_counter()
    response = _invoke(function, request, context)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return _result_from_response(response, elapsed_ms=elapsed_ms)


@_governed_facade("cortex.reason", text_param="prompt", retrieval=False)
def reason(
    prompt: str,
    mode: str = "cot",
    function: str = CORTEX_REASON_FUNCTION,
    ctx: Union[CortexContext, dict, None] = None,
    *,
    system_prompt: str = "",
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> CortexResult:
    """Multi-step reasoning via the router's chain orchestration, governed.

    ``mode`` selects the strategy:
      - ``"cot"``     — Chain of Thought (single model, step-by-step).
      - ``"debate"``  — Chain of Debate (proposer/critic rounds).
      - ``"council"`` — LLM Council (fixed-perspective advisors + chairman
                         synthesis).

    Same TRUST chain as ``complete`` (input screen/redaction, output redaction,
    provenance, audit) with the reasoning happening inside the wrapped op; the
    domain-lens persona is applied. Unlike ``_invoke``, the orchestration
    methods take no ``exclude_model_ids``, so per-call air-gap relies on the
    global ICDEV_AIRGAP config; ``context.air_gap`` is still recorded for audit.
    Router errors propagate. ``result.metadata['reason_mode']`` records the mode.

    ``result.data`` carries the chain telemetry the orchestration methods hang
    on the response and ``_result_from_response`` drops: ``chain_rounds`` (one
    entry per step — ``step``/``model_id``/tokens/cost/duration, no model prose)
    and ``stop_reason`` (``completed``, ``timeout``, ``all_advisors_failed``, …).
    Callers that need the per-step view read them there — e.g. the
    ``council_query`` MCP tool derives its ``advisor_rounds`` from
    ``chain_rounds``.
    """
    method_name = _REASON_MODES.get((mode or "cot").strip().lower())
    if method_name is None:
        raise ValueError(
            f"unknown reason mode {mode!r}; expected one of {sorted(_REASON_MODES)}"
        )
    context = _coerce_context(ctx)
    system_prompt = _apply_domain_persona(context, system_prompt)
    request = _build_request(
        prompt,
        context,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    started = time.perf_counter()
    router = _get_router()
    response = getattr(router, method_name)(function, request)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    result = _result_from_response(response, elapsed_ms=elapsed_ms)
    result.metadata["reason_mode"] = (mode or "cot").strip().lower()
    rounds = getattr(response, "chain_rounds", None)
    result.data["chain_rounds"] = list(rounds) if isinstance(rounds, list) else []
    result.data["stop_reason"] = getattr(response, "stop_reason", "") or ""
    return result


@_governed_facade("cortex.classify", text_param="text", retrieval=False)
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
    context = _coerce_context(ctx)
    started = time.perf_counter()
    try:
        response = _invoke(function, _build_request(prompt, context), context)
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


@_governed_facade("cortex.extract", text_param="text", retrieval=False)
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
    context = _coerce_context(ctx)
    request = _build_request(prompt, context, output_schema=schema)
    started = time.perf_counter()
    response = _invoke(function, request, context)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    payload = response.structured_output
    if payload is None:
        payload = _parse_json_payload(response.content)
    text_out = json.dumps(payload, ensure_ascii=False) if payload is not None else (response.content or "")
    result = _result_from_response(response, text=text_out, elapsed_ms=elapsed_ms)

    # Validate conformance to the caller's JSON schema. DEGRADE-not-refuse (TRUST):
    # a non-conforming / unparseable payload sets schema_valid=False + grounded=False
    # + a schema_error note, rather than raising — the caller still gets the
    # best-effort output but is told, in-band, that it did not conform.
    result.metadata = dict(result.metadata or {})
    if payload is None:
        result.metadata["schema_valid"] = False
        result.metadata["schema_error"] = "no JSON object could be parsed from the completion"
        result.grounded = False
    else:
        ok, err = _validate_against_schema(payload, schema)
        result.metadata["schema_valid"] = ok
        if not ok:
            result.metadata["schema_error"] = err
            result.grounded = False
    return result


# ---------------------------------------------------------------------------
# search / ask — governance-wrapped facades over the retrieval + analyst
# subsystems. Both carry their own native reporting (search returns a list of
# CortexSearchResult; ask attaches the analyst TRUST report to
# result.governance), so the enforced pipeline wraps with attach=False: its
# gates still run for screening + provenance + audit ("no bypass"), the native
# result is left intact, and the outer report is surfaced under
# _OUTER_GOVERNANCE_KEY (see _stash_outer_report). retrieval=False: neither
# call is an LLM answer graded against injected sources — search PRODUCES the
# sources and ask grounds its own answer internally.
search = _governed_facade(
    "cortex.search", text_param="query", retrieval=False, attach=False
)(_search_impl)

ask = _governed_facade(
    "cortex.ask", text_param="question", retrieval=False, attach=False
)(_ask_impl)


# ---------------------------------------------------------------------------
# govern — the pipeline as a standalone entry (ctx-govern-04)
# ---------------------------------------------------------------------------
@_governed_facade(
    "cortex.govern",
    text_param="text",
    retrieval=True,
    attach=False,
    sources_param="sources",
    return_report=True,
)
def govern(
    text: str,
    sources: Any = None,
    ctx: Union[CortexContext, dict, None] = None,
) -> GovernanceReport:
    """Run the enforced TRUST chain standalone over already-produced ``text``.

    The standalone governance entry for external / non-Cortex callers: pass a
    drafted ``text`` plus the ``sources`` it should be grounded against (an int
    count, or an iterable of id strings / dicts / CortexSearchResult) and
    receive the :class:`GovernanceReport` — pre-check, input/output redaction,
    citation + content grounding against ``sources``, provenance, and audit,
    exactly as the Cortex facades run it. Lets other tools adopt the TRUST
    chain incrementally without importing the pipeline internals.

    Returns the :class:`GovernanceReport` (the caller already holds the text).
    The function body is the identity operation the pipeline governs — the
    decorator supplies all governance behavior.

    DECISION (ctx-reach-03): **external-only surface, deliberately.** ``govern``
    has no in-repo Python caller and is not expected to grow one. Its only entry
    point is the ``cortex_govern`` MCP tool. That is not an adoption gap:

    * Every in-process ICDEV path that produces LLM text through Cortex already
      runs this exact chain, because ``@_governed_facade`` wraps
      complete/reason/classify/extract/search/ask/agent. An internal caller that
      wants TRUST calls a facade. Calling ``govern`` on top of a facade result
      would run the chain a SECOND time over one operation — two gateway
      screens, two redaction passes, two ``source_citation_registry`` rows, two
      ``cortex_audit`` rows, and a double count in ``/cortex/metrics``. That is
      exactly the defect ctx-trust-02 removed from four REST endpoints; adopting
      ``govern`` internally would reintroduce it under a different name.
    * ``/cortex/api/v1/govern`` (``rest_v1.api_v1_govern``) does NOT call this
      function and must not be "fixed" to. It runs a single pipeline over its own
      identity lambda so it can (a) return the governed/redacted TEXT, which a
      ``GovernanceReport`` has no field for, and (b) honour the caller's
      ``retrieval`` flag, which this facade fixes at True. Pinned by
      ``tests/cortex/test_rest_single_governance.py``.
    * The genuine adoption target is a NON-Cortex drafting surface (proposal /
      RFI / DIC / Tech Writer) that today calls ``tools/quality/citation_grounding``
      directly. Migrating one is its own card — it changes what blocks a promote
      or an export — not a drive-by here.

    Reversing this decision means naming the internal consumer and proving it is
    not already governed. See ``docs/features/phase-ctx-reach-03-cortex-reach-decisions.md``.
    """
    return text


# ---------------------------------------------------------------------------
# agent — governed team / single-agent execution (ctx-govern-04)
# ---------------------------------------------------------------------------
@_governed_facade("cortex.agent", text_param="goal", retrieval=False)
def agent(
    goal: str,
    roles: Optional[List[str]] = None,
    ctx: Union[CortexContext, dict, None] = None,
    *,
    mode: str = "auto",
    graph: Optional[dict] = None,
    trigger_source: str = "cortex.agent",
    trigger_ref: str = "",
    webhook_url: str = "",
    system_prompt: str = "You are an ICDEV autonomous agent. Complete the goal.",
    tools: Optional[List[dict]] = None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    llm_function: str = "code_generation",
    max_iterations: int = 12,
    rubric: bool = False,
) -> CortexResult:
    """Run a goal through the multi-agent stack, governed end to end.

    Three execution modes, selected by ``mode`` (default ``"auto"``):

    - **team** (``mode="team"`` or ``"auto"`` with ``roles``): launches an ACE
      run via ``ACEController.get_instance().launch(...)``. The launch is
      non-blocking and returns an ``instance_id``; the CortexResult ``text``
      names the run and ``data`` carries ``instance_id``/``roles`` for the
      caller to poll ``/coworker/<id>``.
    - **single** (``mode="single"`` or ``"auto"`` without ``roles``): runs
      ``tools.llm.agent_loop.run_agent_loop`` for one agent over the provided
      ``tools``/``tool_handlers``; the CortexResult ``text`` is the loop's
      final content and ``data`` carries the loop accounting.
    - **graph** (``mode="graph"``, hgx-cx-01): starts a Studio workflow run —
      the platform's durable DAG runtime, with human gates, restart-safe resume
      and per-node tool authorization. Requires ``graph={"workflow_id": ...}``
      (optional ``project_id``, optional ``inputs`` dict). Like team mode the
      start is non-blocking: ``data`` carries ``run_id`` for the caller to poll.
      ``goal`` is recorded on the run's ``inputs`` under ``"goal"`` — a caller's
      own ``inputs["goal"]`` wins — so the run row answers "what was this
      started with". Never selected by ``"auto"``: a graph run names a workflow,
      it cannot be inferred.

    ``mode`` is validated against :data:`_AGENT_MODES` and an unknown value
    raises ``ValueError`` (as ``reason`` does). Dispatch was previously a single
    ``use_team`` boolean, so an unrecognised mode silently ran a single agent —
    the caller got a real, billed, ungated-by-its-own-intent execution instead
    of an error.

    ``rubric`` (single mode only, opt-in, default off) grades the loop in real
    time against the delivery pipeline — the agent builds, the gates run, and it
    revises in-session until the pipeline passes or the iteration cap is hit —
    instead of trusting the loop's own ``done``. Conformance review is off (an
    ad-hoc goal has no kanban acceptance criteria). No effect in team mode.

    ``ctx`` threads tenant_id/user_id into the launch/loop (cost attribution,
    RLS) and into the governance pipeline. Because this facade is governed
    (``retrieval=False``), the goal is injection-screened and input-redacted
    before dispatch and the agent output passes through the output-redaction,
    provenance, and audit gates — the report is attached to
    ``result.governance``.

    DECISION (ctx-reach-03): **adopted; it is the ONLY sanctioned way to launch
    an agent under governance, and its scope stays ungranted.** Three live
    consumers, all pinned by ``tests/cortex/test_cortex_reach_decisions.py``:

    * ``tools/cortex/blueprint.py::_launch_confirmed_agent`` — the canvas
      confirm-then-launch chat path. This is a first-party in-repo Python
      consumer; the card's premise that ``agent`` has none was wrong.
    * ``tools/mcp/cortex_server.py::handle_cortex_agent_launch`` — the
      ``cortex_agent_launch`` MCP tool. Its ungoverned ``_agent_launch_fallback``
      (ACEController / run_agent_loop reached directly, dispatched off the old
      ``use_team`` boolean) was DELETED in ctx-reach-03; a second implementation
      of "launch an agent" that skipped the TRUST chain is the thing this facade
      exists to prevent.
    * ``tools/cortex/rest_v1.py::api_v1_agent`` — the remote surface. Guarded by
      the ``cortex:agent`` scope, which ``service_keys.AGENT_SCOPES`` never
      grants by default. That stays: this is the one endpoint that makes the
      platform ACT rather than answer.

    What is deliberately NOT adopted: an internal tool that wants a team run
    calls ``ACEController.launch`` directly, and Studio nodes call the agent loop
    directly. ``agent`` is the governed entry for a goal arriving from OUTSIDE a
    trusted call path (a chat turn, an MCP client, a remote key) — text that must
    be injection-screened before it is allowed to authorise action. An internal
    caller that already holds a trusted, structured task does not need it, and
    routing every ACE launch through it would put a governance screen on
    first-party control flow.
    """
    mode = (mode or "auto").strip().lower()
    if mode not in _AGENT_MODES:
        raise ValueError(
            f"unknown agent mode {mode!r}; expected one of {sorted(_AGENT_MODES)}"
        )
    context = _coerce_context(ctx)

    if mode == "graph":
        spec = graph or {}
        if not isinstance(spec, dict):
            raise ValueError(f"graph must be a dict, not {type(spec).__name__}")
        workflow_id = str(spec.get("workflow_id") or "").strip()
        if not workflow_id:
            raise ValueError('mode="graph" requires graph={"workflow_id": ...}')
        caller_inputs = spec.get("inputs")
        if caller_inputs is not None and not isinstance(caller_inputs, dict):
            raise ValueError(
                f"graph['inputs'] must be a dict, not {type(caller_inputs).__name__}"
            )
        project_id = str(spec.get("project_id") or context.tenant_id or "default")
        run_id = _start_graph_run(
            workflow_id,
            project_id,
            {"goal": goal, **(caller_inputs or {})},
        )
        return CortexResult(
            text=f"Started Studio graph run {run_id} for goal: {goal}",
            provider="studio",
            data={
                "mode": "graph",
                "run_id": run_id,
                "workflow_id": workflow_id,
                "project_id": project_id,
                "goal": goal,
            },
        )

    use_team = mode == "team" or (mode == "auto" and bool(roles))

    if use_team:
        controller = _get_ace_controller()
        instance_id = controller.launch(
            problem_text=goal,
            trigger_source=trigger_source,
            trigger_ref=trigger_ref or (context.tenant_id or "cortex"),
            user_id=context.user_id or "system",
            project_id=context.tenant_id or "",
            webhook_url=webhook_url,
            role_ids=list(roles) if roles else None,
        )
        return CortexResult(
            text=f"Launched ACE team run {instance_id} for goal: {goal}",
            provider="ace",
            data={
                "mode": "team",
                "instance_id": instance_id,
                "roles": list(roles) if roles else [],
                "goal": goal,
            },
        )

    router = _get_router()
    loop = _run_single_agent(
        router,
        system_prompt=system_prompt,
        user_prompt=goal,
        tools=tools or [],
        tool_handlers=tool_handlers or {},
        llm_function=llm_function,
        max_iterations=max_iterations,
        ctx=context,
        rubric=rubric,
    )
    return CortexResult(
        text=loop.final_content or "",
        provider=loop.provider or "agent_loop",
        model=loop.model_id or "",
        cost=float(loop.total_cost_usd or 0.0),
        data={
            "mode": "single",
            "result_subtype": loop.result_subtype,
            "turns": loop.turns,
            "done": loop.done,
            "truncated": loop.truncated,
            "session_id": loop.session_id,
            "tool_call_log": loop.tool_call_log,
        },
    )


# ---------------------------------------------------------------------------
# Day-one air-gap invariant (ctx-core-03): every cortex_* routing chain in
# args/llm_config.yaml must keep a local-tier fallback. Fail at import, not
# at the first offline call.
# ---------------------------------------------------------------------------
assert_airgap_ready()
