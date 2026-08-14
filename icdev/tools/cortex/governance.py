# CUI // SP-CTI
"""ICDEV Cortex governance pipeline — the single enforced TRUST chain.

Every Cortex invocation (complete/classify/extract/search-synthesize) runs
through :class:`GovernancePipeline` instead of each caller wiring its own
ad-hoc governance. The chain, in order:

1. ``pre_check``          — ``tools/llm/gateway.check_text()`` (injection/PII/
                            length/rate/cost). A block fails CLOSED: audit +
                            typed :class:`GovernanceBlockedError`, the wrapped
                            operation never runs.
2. ``input_redaction``    — ``tools/redaction`` anonymizer masks PII in the
                            prompt before it reaches any provider.
3. ``operation``          — the wrapped callable itself (LLM budget guardrails
                            apply automatically inside ``LLMRouter``).
4. ``citation_grounding`` — shared ``tools/quality/citation_grounding``
                            validation of ``[source: N]`` tags against the
                            injected sources (full promote/export policy lands
                            in ctx-govern-02; hallucinated citations already
                            fail here).
5. ``content_grounding``  — ``tools/quality/content_grounding`` semantic
                            claim-vs-context grounding: when the call is
                            retrieval-backed AND context snippets are present,
                            ``ground_content`` scores how well each output
                            sentence is supported by the injected snippets and
                            the score is warned/blocked against the SHARED
                            confidence bands from ``citation_grounding``
                            (``CONF_ABSTAIN``). When no snippet text is
                            available it falls back to the placeholder scan.
6. ``output_redaction``   — ``tools/llm/output_redactor`` masks PII/secrets in
                            the response text.
7. ``provenance``         — ``tools/provenance/registry.register_citation``
                            record + one append-only ``cortex_audit`` row
                            (ctx-govern-03) written through the RLS-aware storage
                            shim, plus a structured logger record for observability.

Non-retrieval calls (``retrieval=False``) may skip the two grounding gates —
NEVER redaction or provenance/audit — and the skip is recorded explicitly in
the :class:`GovernanceReport` (outcome ``"skip"``) so governance stays
observable, not implied.

Profiles (hgx-gov-01): a caller may name a *governance profile* — a subset of
the chain declared as data under ``governance.profiles`` in
``args/cortex_config.yaml`` — so a node doing internal diligence need not pay
the same seven gates as one emitting a customer-facing artifact. A caller that
names none resolves to ``default``, the full chain, so behaviour is unchanged.
``output_redaction`` and ``provenance`` are in :data:`MANDATORY_GATES` and no
profile may omit them; attempting it is a config error at load, not a quiet
per-call downgrade. Profile-driven skips are recorded the same way every other
skip is.

Fail-open/fail-closed: gate errors degrade to ``"warn"`` by default (matching
``args/redaction_config.yaml`` ``fail_closed: false``); when
``CortexContext.fail_closed`` is True, any gate error or ``"fail"`` outcome
blocks the response instead.

Test seams: every external module call goes through a module-level
``_gate_*`` function so tests monkeypatch ``governance._gate_check_text`` etc.
without importing the heavy backends.
"""
from __future__ import annotations

import contextvars
import functools
import hashlib
import importlib
import json
import time
import uuid
from typing import Callable, Optional

from tools.logging.icdev_logger import get_logger

from .config import (
    load_cortex_config,
    resolve_fail_closed,
    skip_grounding_for_plain_complete,
)
from .schemas import CortexContext, CortexResult, GovernanceReport

logger = get_logger(__name__)

# "tools" when loaded via the shim namespace, "icdev.tools" when canonical.
_NS = __name__.rsplit(".cortex.", 1)[0]

# Gate names, in enforced execution order.
GATE_PRE_CHECK = "pre_check"
GATE_INPUT_REDACTION = "input_redaction"
GATE_OPERATION = "operation"
GATE_CITATION_GROUNDING = "citation_grounding"
GATE_CONTENT_GROUNDING = "content_grounding"
GATE_OUTPUT_REDACTION = "output_redaction"
GATE_PROVENANCE = "provenance"

GATE_ORDER = (
    GATE_PRE_CHECK,
    GATE_INPUT_REDACTION,
    GATE_OPERATION,
    GATE_CITATION_GROUNDING,
    GATE_CONTENT_GROUNDING,
    GATE_OUTPUT_REDACTION,
    GATE_PROVENANCE,
)

OUTCOME_PASS = "pass"
OUTCOME_WARN = "warn"
OUTCOME_FAIL = "fail"
OUTCOME_SKIP = "skip"

_CLASSIFICATION_IL = {"CUI": "IL4", "CUI//SP-CTI": "IL5", "SECRET": "IL6"}


# ---------------------------------------------------------------------------
# Governance profiles (hgx-gov-01)
# ---------------------------------------------------------------------------
# GATE_ORDER above is the full chain, and until now it was also the ONLY chain:
# a node doing internal diligence paid the same seven gates as one emitting a
# customer-facing artifact. A *profile* names a subset of it, declared as data in
# ``args/cortex_config.yaml`` under ``governance.profiles``, and a caller (a
# Studio agent node, a facade) may name one.
#
# Two gates are NOT negotiable in any profile:
#   ``output_redaction`` is the egress guarantee — the last thing between model
#   output and a caller, and the only gate that runs for every result shape.
#   ``provenance``       is the NIST-AU append-only audit row.
# A profile able to drop either would turn a latency optimisation into a
# compliance hole, so omitting one is a config error at LOAD time (loudly, once,
# where the operator can see it) rather than a quiet per-call downgrade.
# ``operation`` is listed alongside them because it IS the wrapped call: a
# profile that "skips" it has skipped the work, not a gate.

#: Gates no profile may omit. Enforced by :func:`load_governance_profiles`.
MANDATORY_GATES = (GATE_OPERATION, GATE_OUTPUT_REDACTION, GATE_PROVENANCE)

#: The gates a profile may leave out — screening and grounding, in chain order.
SKIPPABLE_GATES = (
    GATE_PRE_CHECK,
    GATE_INPUT_REDACTION,
    GATE_CITATION_GROUNDING,
    GATE_CONTENT_GROUNDING,
)

#: Profile every caller that names none resolves to: the whole chain. Built into
#: code, not read from YAML, so a missing/unreadable config cannot silently
#: narrow governance and an existing caller's behaviour never depends on config.
DEFAULT_PROFILE = "default"


class GovernanceProfileError(ValueError):
    """A governance profile is undeclared, or declared in a way that cannot load.

    Raised at profile-resolution time — before any gate runs — for an unknown
    profile name, a malformed ``governance.profiles`` block, an unknown gate
    name, or a profile omitting one of :data:`MANDATORY_GATES`.
    """


def load_governance_profiles(config_path=None) -> dict:
    """Validated ``{profile_name: frozenset(gate_names)}`` from Cortex config.

    ``default`` is always present and is always the full :data:`GATE_ORDER`;
    operators add named subsets under ``governance.profiles`` in
    ``args/cortex_config.yaml``::

        governance:
          profiles:
            internal_diligence:
              gates: [operation, output_redaction, provenance]

    Raises:
        GovernanceProfileError: the block is not a mapping, a profile is not a
            mapping, its ``gates`` is not a non-empty list, it names a gate that
            does not exist, it omits a :data:`MANDATORY_GATES` entry, or it tries
            to redefine ``default`` (which would change the behaviour of every
            caller that names no profile — the one thing profiles must not do).
    """
    raw = (load_cortex_config(config_path).get("governance") or {}).get("profiles")
    profiles = {DEFAULT_PROFILE: frozenset(GATE_ORDER)}
    if raw is None:
        return profiles
    if not isinstance(raw, dict):
        raise GovernanceProfileError(
            f"governance.profiles must be a mapping of profile name -> "
            f"{{gates: [...]}}, got {type(raw).__name__}."
        )

    for name, spec in raw.items():
        key = str(name).strip()
        if key == DEFAULT_PROFILE:
            raise GovernanceProfileError(
                "governance.profiles may not redefine 'default': it is the full "
                "gate chain and is what every caller that names no profile gets. "
                "Declare a differently named profile instead."
            )
        if not isinstance(spec, dict):
            raise GovernanceProfileError(
                f"governance profile '{key}' must be a mapping with a 'gates' "
                f"list, got {type(spec).__name__}."
            )
        gates = spec.get("gates")
        if not isinstance(gates, (list, tuple)) or not gates:
            raise GovernanceProfileError(
                f"governance profile '{key}' must declare a non-empty 'gates' "
                f"list naming the gates it runs (one or more of "
                f"{', '.join(GATE_ORDER)})."
            )
        named = [str(gate).strip() for gate in gates]
        unknown = [gate for gate in named if gate not in GATE_ORDER]
        if unknown:
            raise GovernanceProfileError(
                f"governance profile '{key}' names unknown gate(s) "
                f"{', '.join(unknown)}. Valid gates: {', '.join(GATE_ORDER)}."
            )
        missing = [gate for gate in MANDATORY_GATES if gate not in named]
        if missing:
            raise GovernanceProfileError(
                f"governance profile '{key}' omits non-skippable gate(s) "
                f"{', '.join(missing)}. output_redaction is the egress guarantee "
                f"and provenance is the NIST-AU audit row; operation is the "
                f"wrapped call itself. Every profile must list all of "
                f"{', '.join(MANDATORY_GATES)}."
            )
        profiles[key] = frozenset(named)
    return profiles


def resolve_profile(name: str = "", config_path=None) -> frozenset:
    """Gates enabled for ``name``; the full chain when it is blank.

    Raises:
        GovernanceProfileError: ``name`` is not declared (a typo'd profile must
            not silently fall back to the full chain and look like it worked, nor
            to a narrower one), or the profiles block itself cannot load.
    """
    key = (name or "").strip()
    if not key or key == DEFAULT_PROFILE:
        return frozenset(GATE_ORDER)
    profiles = load_governance_profiles(config_path)
    try:
        return profiles[key]
    except KeyError:
        raise GovernanceProfileError(
            f"unknown governance profile '{key}'. Declared profiles: "
            f"{', '.join(sorted(profiles))}. Add it under governance.profiles in "
            f"args/cortex_config.yaml."
        ) from None


def _content_grounding_floor(config_path=None) -> float:
    """Pass/warn floor for the content_grounding gate — the SHARED band.

    Single source of truth: derived from ``citation_grounding.CONF_ABSTAIN``
    (the same >=0.7 include / 0.4 abstain bands the citation gate uses), NOT a
    hardcoded local threshold. An operator may override it under
    ``governance.content_grounding.min_score`` in ``args/cortex_config.yaml``;
    absent that key the shared constant wins, so the two TRUST gates cannot
    silently drift apart.
    """
    from tools.quality.citation_grounding import CONF_ABSTAIN
    cfg = (load_cortex_config(config_path).get("governance") or {}).get(
        "content_grounding"
    ) or {}
    override = cfg.get("min_score")
    return float(override) if override is not None else float(CONF_ABSTAIN)


class GovernanceBlockedError(RuntimeError):
    """Typed refusal raised when a governance gate blocks the invocation."""

    def __init__(self, gate: str, reason: str, report: GovernanceReport):
        super().__init__(f"[{gate}] {reason}")
        self.gate = gate
        self.reason = reason
        self.report = report


def _mod(module: str):
    """Import a platform module from this module's own namespace root."""
    return importlib.import_module(f"{_NS}.{module}")


# ---------------------------------------------------------------------------
# Gate seams — one thin patchable function per external dependency
# ---------------------------------------------------------------------------
def _gate_check_text(text: str) -> dict:
    """Gate 1: gateway dry-run check. Returns the pre_invoke result dict."""
    return _mod("llm.gateway").check_text(text)["pre_invoke"]


def _gate_redact_input(text: str, classification: str) -> tuple:
    """Gate 2: anonymize PII in the prompt. Returns (masked_text, count)."""
    anonymizer_mod = _mod("redaction.anonymizer")
    anonymizer = anonymizer_mod.RedactionAnonymizer()
    result = anonymizer.anonymize(
        text, impact_level=_CLASSIFICATION_IL.get(classification, "IL4")
    )
    return result.anonymized_text, len(result.replacements)


def _gate_validate_citations(text: str, allowed_sources) -> dict:
    """Gate 4: shared citation validation report."""
    return _mod("quality.citation_grounding").validate_citations(text, allowed_sources)


def _gate_find_placeholders(text: str) -> list:
    """Gate 5a: unresolved [PLACEHOLDER] tokens in the output."""
    return _mod("quality.content_grounding").find_placeholders(text)


def _gate_ground_content(output_text: str, context_snippets, ctx) -> dict:
    """Gate 5b: semantic claim-vs-context grounding of the output.

    Delegates to the shared ``content_grounding.ground_content``. The LLM-
    assisted pass is OFF unless ``governance.content_grounding.llm_assisted``
    is set in ``args/cortex_config.yaml``; when on, an ``llm_invoke`` closure
    over the platform ``LLMRouter`` routing chains is injected (no model ids
    are named here) and any failure degrades to the deterministic heuristic.
    """
    grounding_mod = _mod("quality.content_grounding")
    cfg = (load_cortex_config().get("governance") or {}).get("content_grounding") or {}
    method = "heuristic"
    llm_invoke = None
    if cfg.get("llm_assisted"):
        llm_invoke = _build_grounding_llm_invoke(cfg, ctx)
        if llm_invoke is not None:
            method = "llm"
    return grounding_mod.ground_content(
        output_text,
        context_snippets,
        method=method,
        support_floor=_content_grounding_floor(),
        llm_invoke=llm_invoke,
    )


def _build_grounding_llm_invoke(cfg: dict, ctx):
    """Build an ``(prompt) -> str`` closure routed through LLMRouter, or None.

    The routing FUNCTION name comes from config (default ``cortex_complete``,
    an existing chain) — never a model id. Any wiring failure returns None so
    the grounding falls back to the LLM-free heuristic.
    """
    try:
        router_mod = _mod("llm.router")
        provider_mod = _mod("llm.provider")
        router = router_mod.LLMRouter()
        function = cfg.get("routing_function") or "cortex_complete"

        def _invoke(prompt: str) -> str:
            request = provider_mod.LLMRequest(
                prompt=prompt,
                agent_id=getattr(ctx, "agent_id", "") or "cortex-grounding",
                temperature=0.0,
            )
            response = router.invoke(function, request)
            return getattr(response, "content", "") or ""

        return _invoke
    except Exception as exc:  # noqa: BLE001 — heuristic is always the floor
        logger.debug("content grounding LLM invoke unavailable: %s", exc)
        return None


def _gate_redact_output(text: str) -> tuple:
    """Gate 6: mask PII/secrets in the response. Returns (masked, hits)."""
    result = _mod("llm.output_redactor").redact(text)
    return result.redacted_text, list(result.pattern_hits)


def _gate_register_provenance(
    output_text: str, ctx: CortexContext, operation: str, record_id: str
) -> str:
    """Gate 7a: unified source_citation_registry row. Returns registry id."""
    return _mod("provenance.registry").register_citation(
        citation_type="cortex",
        source_table="cortex_governance",
        source_record_id=record_id,
        source_doc=operation,
        source_hash=hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
        classification=ctx.classification or "CUI",
        project_id=ctx.tenant_id or None,
    )


def _gate_record_audit(payload: dict) -> None:
    """Gate 7b: append-only audit row (NIST AU).

    Persists the call's ``cortex_sessions`` row and its one INSERT-only
    ``cortex_audit`` row (ctx-govern-03) through the RLS-aware storage shim, both
    over a SINGLE connection (cxo-perf-03), and also emits a structured log line
    so the audit is observable even when the DB write degrades. A persistence
    failure is logged, never raised — the outer ``_audit`` guard already isolates
    it from the real operation outcome, and governance must never fail because
    bookkeeping did.
    """
    logger.info("cortex_governance_audit %s", json.dumps(payload, default=str))
    try:
        _mod("cortex.db.init_db").record_governed_call(payload)
    except Exception as exc:  # audit persistence must never mask the real outcome
        logger.error("cortex governance audit persistence failed: %s", exc)


# ---------------------------------------------------------------------------
# Context-source normalization
# ---------------------------------------------------------------------------
def _allowed_source_ids(context_sources) -> Optional[list]:
    """Map injected context to the source-id set citations may reference.

    Accepts an int count (RAG convention: ids "1".."N"), an iterable of id
    strings, dicts, or CortexSearchResult-like objects (``citation.source_id``
    used when present, else 1-based position).
    """
    if context_sources is None:
        return None
    if isinstance(context_sources, int):
        return [str(i + 1) for i in range(max(context_sources, 0))]
    ids = []
    for i, src in enumerate(context_sources, 1):
        if isinstance(src, str):
            ids.append(src)
        elif isinstance(src, dict):
            ids.append(str(src.get("source_id") or src.get("id") or i))
        else:
            citation = getattr(src, "citation", None)
            ids.append(str(getattr(citation, "source_id", "") or i))
    return ids


def _context_texts(context_sources) -> list:
    """Extract the raw text of each injected context source, best effort."""
    if context_sources is None or isinstance(context_sources, int):
        return []
    texts = []
    for src in context_sources:
        if isinstance(src, str):
            continue  # bare source id, carries no text
        elif isinstance(src, dict):
            text = src.get("content") or src.get("text") or ""
        else:
            text = getattr(src, "content", "") or ""
        if text:
            texts.append(text)
    return texts


# ---------------------------------------------------------------------------
# Accounting capture (ctx-obs-01)
# ---------------------------------------------------------------------------
# The audit row's cost/latency/token fields used to be populated ONLY when the
# operation happened to return a CortexResult. Two facades do not: ``search``
# returns a list of CortexSearchResult and ``govern`` returns a str. Both showed
# up in ``calls`` and contributed nothing to ``cost_usd``, ``avg_latency_ms`` or
# ``by_model`` — and ``search`` is the most expensive facade there is (backend
# fan-out, an optional CRAG re-retrieval, plus a rewrite LLM call), so the
# biggest consumer was invisible in exactly the panel used to decide what to
# optimise.
#
# The capture is now keyed on the CALL, not on the return type:
#
# ``operation_ms``  wall-clock inside the wrapped callable, measured by the
#                   pipeline. Always available, whatever ``fn`` returns.
# ``pipeline_ms``   wall-clock across the whole governed call (gates included).
#                   This is the only meaningful figure for ``govern``, whose
#                   operation body is the identity function and whose real cost
#                   IS the gate chain.
# ``latency_ms``    the headline the panel averages: the result's own
#                   provider-reported latency when it has one (unchanged for
#                   every facade returning a CortexResult), else pipeline_ms.
#                   NOT operation_ms — ``govern``'s body is the identity
#                   function, so its operation time is a few microseconds and
#                   reporting that as the call's latency would be a more
#                   precise-looking lie than the zero it replaced.
#
# Provider spend inside an operation that does not surface it on its return
# value is collected through ``record_llm_call``: ``api._invoke`` reports every
# router call to the enclosing governed operation. A ContextVar (not a global)
# scopes that to the call, so concurrent and nested governed calls each keep
# their own tally.
_llm_accounting: contextvars.ContextVar = contextvars.ContextVar(
    "cortex_llm_accounting", default=None
)


def _new_accounting() -> dict:
    return {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
            "provider": "", "model": ""}


def _elapsed_ms(started: float) -> float:
    """Milliseconds since ``started`` (perf_counter), sub-millisecond preserved.

    Rounded rather than truncated to int: an operation that takes 400us is not
    a zero-latency operation, and ``metrics`` treats 0 as "no measurement" and
    drops the row from the average entirely.
    """
    return round((time.perf_counter() - started) * 1000.0, 3)


def record_llm_call(response) -> None:
    """Attribute one provider call to the enclosing governed operation.

    Called by ``api._invoke`` after every ``LLMRouter.invoke``. A no-op outside
    a governed operation (ungoverned callers have nothing to attribute it to),
    and best-effort throughout: telemetry must never break a provider call.
    """
    tally = _llm_accounting.get()
    if tally is None:
        return
    try:
        tally["calls"] += 1
        tally["cost_usd"] += float(getattr(response, "cost_usd", 0.0) or 0.0)
        tally["input_tokens"] += int(getattr(response, "input_tokens", 0) or 0)
        tally["output_tokens"] += int(getattr(response, "output_tokens", 0) or 0)
        # Last writer wins: a single audit row carries one model, and the last
        # call is the one that produced the answer the caller sees.
        tally["provider"] = str(getattr(response, "provider", "") or "") or tally["provider"]
        tally["model"] = str(getattr(response, "model_id", "") or "") or tally["model"]
    except Exception as exc:  # noqa: BLE001 — accounting is never load-bearing
        logger.debug("cortex llm accounting skipped: %s", exc)


def _accounting_fields(result, timing: Optional[dict], tally: Optional[dict]) -> dict:
    """Accounting for one governed call, independent of the operation's return type."""
    timing = timing or {}
    tally = tally or {}
    operation_ms = float(timing.get("operation_ms") or 0.0)
    pipeline_ms = (
        _elapsed_ms(timing["started"]) if timing.get("started") is not None else 0.0
    )
    out = {
        "cost_usd": 0.0, "latency_ms": 0.0, "provider": "", "model": "",
        "input_tokens": 0, "output_tokens": 0,
        "operation_ms": operation_ms, "pipeline_ms": pipeline_ms,
    }
    if isinstance(result, CortexResult):
        out["cost_usd"] = float(getattr(result, "cost", 0.0) or 0.0)
        out["latency_ms"] = float(getattr(result, "latency_ms", 0) or 0)
        out["provider"] = getattr(result, "provider", "") or ""
        out["model"] = getattr(result, "model", "") or ""
        out["input_tokens"] = int(getattr(result, "input_tokens", 0) or 0)
        out["output_tokens"] = int(getattr(result, "output_tokens", 0) or 0)
    # Provider calls the result did not report (search's CRAG rewrite; any
    # non-CortexResult shape). Never overwrites a figure the result DID report,
    # so every existing CortexResult facade is byte-for-byte unchanged.
    if tally.get("calls") and not (
        out["cost_usd"] or out["input_tokens"] or out["output_tokens"]
    ):
        out["cost_usd"] = float(tally.get("cost_usd") or 0.0)
        out["input_tokens"] = int(tally.get("input_tokens") or 0)
        out["output_tokens"] = int(tally.get("output_tokens") or 0)
        out["provider"] = out["provider"] or (tally.get("provider") or "")
        out["model"] = out["model"] or (tally.get("model") or "")
    # Latency is ALWAYS recorded — see the precedence note above.
    if out["latency_ms"] <= 0:
        out["latency_ms"] = pipeline_ms
    return out


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
class GovernancePipeline:
    """Enforced pre/post TRUST chain around one Cortex operation.

    Usage::

        pipeline = GovernancePipeline(operation="cortex.complete")
        result, report = pipeline.wrap(
            lambda prompt: router_call(prompt),
            ctx,
            prompt=user_prompt,
            context_sources=search_results,   # None + retrieval=False for
            retrieval=True,                   # ungrounded complete() calls
        )

    ``wrap`` returns ``(result, GovernanceReport)``; when the operation
    returns a :class:`CortexResult` the report is also attached to
    ``result.governance`` and ``result.grounded`` is set from the grounding
    gates. A blocked pre-check raises :class:`GovernanceBlockedError` — the
    wrapped operation is never invoked.

    ``profile`` (hgx-gov-01) names a subset of the chain from
    ``governance.profiles`` in ``args/cortex_config.yaml``; blank means the whole
    chain, which is what every existing caller gets. A gate a profile leaves out
    is recorded ``"skip"`` with the profile as the reason, so a narrowed chain is
    as observable in the audit as a full one. ``output_redaction`` and
    ``provenance`` are not narrowable — see :data:`MANDATORY_GATES`.
    """

    def __init__(
        self,
        operation: str = "cortex",
        agent_id: str = "cortex",
        profile: str = "",
    ):
        self.operation = operation
        self.agent_id = agent_id
        self.profile = (profile or "").strip()

    # -- gate bookkeeping ---------------------------------------------------
    @staticmethod
    def _record(report: GovernanceReport, gate: str, outcome: str, detail: str = "") -> None:
        if outcome != OUTCOME_SKIP:
            report.gates_run.append(gate)
        report.outcomes[gate] = outcome
        if detail:
            logger.debug("cortex governance gate %s=%s: %s", gate, outcome, detail)

    def _profile_skip(
        self, report: GovernanceReport, gate: str, enabled, profile_name: str
    ) -> bool:
        """True — and recorded as ``skip`` — when the profile leaves ``gate`` out.

        Only ever consulted for :data:`SKIPPABLE_GATES`; the mandatory three are
        guaranteed present by :func:`load_governance_profiles`, so no call site
        for them exists and none should be added.
        """
        if gate in enabled:
            return False
        self._record(report, gate, OUTCOME_SKIP, f"profile '{profile_name}'")
        return True

    def _degrade(
        self, report: GovernanceReport, ctx: CortexContext, gate: str, exc: Exception,
        *, result=None, timing: Optional[dict] = None, tally: Optional[dict] = None,
    ) -> None:
        """Gate error: warn and continue (fail-open) or block on fail_closed."""
        if resolve_fail_closed(ctx):
            self._block(report, ctx, gate, f"{gate} unavailable: {exc}",
                        result=result, timing=timing, tally=tally)
        logger.warning("cortex governance gate %s degraded (fail-open): %s", gate, exc)
        self._record(report, gate, OUTCOME_WARN, str(exc))

    def _block(
        self, report: GovernanceReport, ctx: CortexContext, gate: str, reason: str,
        *, result=None, timing: Optional[dict] = None, tally: Optional[dict] = None,
    ) -> None:
        report.outcomes[gate] = OUTCOME_FAIL
        if gate not in report.gates_run:
            report.gates_run.append(gate)
        report.blocked = True
        report.blocked_reason = reason
        # A call blocked at a POST-operation gate still ran the operation, so it
        # still cost money and time. Carry its accounting like any other row.
        self._audit(report, ctx, blocked_gate=gate, result=result,
                    timing=timing, tally=tally)
        raise GovernanceBlockedError(gate, reason, report)

    def _audit(
        self,
        report: GovernanceReport,
        ctx: CortexContext,
        blocked_gate: str = "",
        provenance_id: str = "",
        result=None,
        timing: Optional[dict] = None,
        tally: Optional[dict] = None,
    ) -> None:
        try:
            payload = {
                "record_id": f"cgov-{uuid.uuid4().hex[:16]}",
                "operation": self.operation,
                "agent_id": self.agent_id,
                "session_id": getattr(ctx, "session_id", "") or "",
                "tenant_id": ctx.tenant_id,
                "user_id": ctx.user_id,
                "classification": ctx.classification,
                "domain": getattr(ctx, "domain", "") or "",
                # Carried for the cortex_sessions row that shares this call's
                # connection; cortex_audit has no air_gap column of its own.
                "air_gap": bool(getattr(ctx, "air_gap", False)),
                "blocked": report.blocked,
                "blocked_gate": blocked_gate,
                "blocked_reason": report.blocked_reason,
                "profile": report.profile or DEFAULT_PROFILE,
                "gates_run": list(report.gates_run),
                "outcomes": dict(report.outcomes),
                "redactions_applied": report.redactions_applied,
                "provenance_id": provenance_id,
            }
            # Accounting for observability. cost/latency/provider/model have no
            # cortex_audit columns — carry them in the free-form gates_json blob
            # so /cortex/metrics can aggregate spend without a schema migration.
            # This is INSERT-only; the append-only audit invariant is untouched.
            #
            # Derived from the CALL, not from the return type (ctx-obs-01): the
            # pipeline's own timing and the provider calls made inside the
            # operation are recorded whatever `fn` handed back, so search (list)
            # and govern (str) are no longer holes in the spend/latency panels.
            payload.update(_accounting_fields(result, timing, tally))
            _gate_record_audit(payload)
        except Exception as exc:  # audit stub must never mask the real outcome
            logger.error("cortex governance audit record failed: %s", exc)

    # -- the chain ------------------------------------------------------------
    def wrap(
        self,
        fn: Callable,
        ctx: Optional[CortexContext] = None,
        *,
        prompt: str = "",
        context_sources=None,
        retrieval: bool = True,
        attach: bool = True,
        profile: Optional[str] = None,
    ) -> tuple:
        """Run ``fn(governed_prompt)`` inside the full TRUST chain.

        Returns ``(result, GovernanceReport)``. Raises
        :class:`GovernanceBlockedError` when a gate blocks (always for a
        pre-check block; for downstream gate failures only when
        ``ctx.fail_closed`` is True).

        ``attach`` (default True) writes the report onto a returned
        :class:`CortexResult` (``result.governance``/``result.grounded``) and
        lets output redaction rewrite ``result.text``. Facades whose result
        already carries a native governance report (the Cortex Analyst
        ``ask``) or whose result is not a CortexResult (``search`` returns a
        list) wrap with ``attach=False``: every gate still runs (screening +
        audit + provenance are the "no bypass" guarantee), but the native
        result is left byte-for-byte intact and the outer report is returned
        separately for the caller to surface.

        ``profile`` overrides the pipeline's own profile for this one call;
        ``None`` (the default) means "use the pipeline's". Resolution happens
        BEFORE any gate runs, so an unknown profile name raises
        :class:`GovernanceProfileError` instead of running an unintended chain.
        """
        ctx = ctx or CortexContext()
        profile_name = (
            self.profile if profile is None else (profile or "").strip()
        ) or DEFAULT_PROFILE
        enabled = resolve_profile(profile_name)
        report = GovernanceReport(profile=profile_name)
        # Accounting state for THIS call. Local, not on `self` — the `governed`
        # decorator shares one pipeline instance across every call it wraps.
        timing = {"started": time.perf_counter(), "operation_ms": 0.0}
        tally = _new_accounting()

        # 1. Gateway pre-invoke check — a block ALWAYS fails closed.
        #    SKIPPED for trusted first-party content (ctx.trusted_content): the
        #    injection screen exists to defend against untrusted user input, and
        #    trusted callers (e.g. docgen ingesting a document already inside the
        #    tenant boundary) mirror the router's skip_injection_scan contract.
        #    The skip is recorded so it stays observable in the audit report.
        if self._profile_skip(report, GATE_PRE_CHECK, enabled, profile_name):
            pass
        elif ctx.trusted_content:
            self._record(report, GATE_PRE_CHECK, OUTCOME_SKIP, "trusted content")
        else:
            try:
                pre = _gate_check_text(prompt)
            except Exception as exc:
                pre = None
                self._degrade(report, ctx, GATE_PRE_CHECK, exc,
                              timing=timing, tally=tally)
            if pre is not None:
                if not pre.get("allowed", True):
                    self._block(
                        report, ctx, GATE_PRE_CHECK,
                        pre.get("blocked_reason") or "blocked by LLM gateway pre-check",
                        timing=timing, tally=tally,
                    )
                self._record(
                    report, GATE_PRE_CHECK,
                    OUTCOME_WARN if pre.get("warnings") else OUTCOME_PASS,
                    "; ".join(pre.get("warnings") or []),
                )

        # 2. Input redaction — skipped ONLY for trusted content (same rationale
        #    as gate 1). Output redaction below is NOT affected: egress is always
        #    screened regardless of trust.
        governed_prompt = prompt
        if self._profile_skip(report, GATE_INPUT_REDACTION, enabled, profile_name):
            pass
        elif ctx.trusted_content:
            self._record(report, GATE_INPUT_REDACTION, OUTCOME_SKIP, "trusted content")
        else:
            try:
                governed_prompt, masked = _gate_redact_input(prompt, ctx.classification)
                report.redactions_applied += masked
                self._record(report, GATE_INPUT_REDACTION, OUTCOME_PASS)
            except Exception as exc:
                self._degrade(report, ctx, GATE_INPUT_REDACTION, exc,
                              timing=timing, tally=tally)

        # 3. The wrapped operation. Errors are recorded then re-raised —
        #    the pipeline governs, it does not swallow provider failures.
        #    Timed here, and every router call made inside it reports into
        #    `tally` (see record_llm_call), so the accounting below never
        #    depends on what the operation chose to return.
        token = _llm_accounting.set(tally)
        op_started = time.perf_counter()
        try:
            result = fn(governed_prompt)
        except GovernanceBlockedError:
            raise
        except Exception:
            timing["operation_ms"] = _elapsed_ms(op_started)
            self._record(report, GATE_OPERATION, OUTCOME_FAIL)
            self._audit(report, ctx, blocked_gate=GATE_OPERATION,
                        timing=timing, tally=tally)
            raise
        finally:
            _llm_accounting.reset(token)
        timing["operation_ms"] = _elapsed_ms(op_started)
        self._record(report, GATE_OPERATION, OUTCOME_PASS)

        is_cortex_result = isinstance(result, CortexResult)
        text = result.text if is_cortex_result else (result if isinstance(result, str) else str(result))

        # 4. Citation grounding (retrieval calls only; skip recorded). A profile
        #    that leaves this gate out also leaves the answer un-attested, so
        #    `grounded` is False — a skipped gate never certifies its own subject.
        grounded = True
        if self._profile_skip(report, GATE_CITATION_GROUNDING, enabled, profile_name):
            grounded = False
        elif retrieval:
            allowed = _allowed_source_ids(context_sources)
            if allowed is None:
                # Nothing to validate against — a retrieval call should
                # always inject its sources; surface that, don't fail.
                grounded = False
                self._record(
                    report, GATE_CITATION_GROUNDING, OUTCOME_WARN,
                    "retrieval call without injected sources",
                )
            else:
                try:
                    citation_report = _gate_validate_citations(text, allowed)
                    if citation_report.get("hallucinated_citations"):
                        grounded = False
                        detail = f"hallucinated citations: {citation_report['hallucinated_citations']}"
                        if resolve_fail_closed(ctx):
                            self._block(report, ctx, GATE_CITATION_GROUNDING, detail,
                                        result=result, timing=timing, tally=tally)
                        self._record(report, GATE_CITATION_GROUNDING, OUTCOME_FAIL, detail)
                    elif not citation_report.get("cited_count"):
                        # Presence policy hardens in ctx-govern-02; today it warns.
                        grounded = False
                        self._record(report, GATE_CITATION_GROUNDING, OUTCOME_WARN, "no citations in output")
                    else:
                        self._record(report, GATE_CITATION_GROUNDING, OUTCOME_PASS)
                except GovernanceBlockedError:
                    raise
                except Exception as exc:
                    grounded = False
                    self._degrade(report, ctx, GATE_CITATION_GROUNDING, exc,
                                  result=result, timing=timing, tally=tally)
        elif skip_grounding_for_plain_complete():
            grounded = False
            self._record(report, GATE_CITATION_GROUNDING, OUTCOME_SKIP, "non-retrieval call")
        else:
            # governance.skip_grounding_for_plain_complete: false — a plain
            # completion injects NO sources, so the allowed set is empty by
            # construction and any [source: N] tag the model emitted is
            # fabricated. That is the one citation defect a non-retrieval call
            # can actually commit, and until ctx-enf-03 nothing looked for it.
            grounded = False
            try:
                citation_report = _gate_validate_citations(text, [])
                if citation_report.get("hallucinated_citations"):
                    detail = (
                        "citations in a non-retrieval call: "
                        f"{citation_report['hallucinated_citations']}"
                    )
                    if resolve_fail_closed(ctx):
                        self._block(report, ctx, GATE_CITATION_GROUNDING, detail)
                    self._record(report, GATE_CITATION_GROUNDING, OUTCOME_FAIL, detail)
                else:
                    self._record(
                        report, GATE_CITATION_GROUNDING, OUTCOME_PASS,
                        "non-retrieval call cites nothing",
                    )
            except GovernanceBlockedError:
                raise
            except Exception as exc:
                self._degrade(report, ctx, GATE_CITATION_GROUNDING, exc)

        # 5. Content grounding — semantic claim-vs-context grounding when the
        #    call is retrieval-backed AND snippet text is available; otherwise a
        #    placeholder scan. The score/method/floor are recorded on the report
        #    (report.content_grounding) so the gate is observable, and the
        #    warn/block threshold is the SHARED citation_grounding band, not a
        #    local constant. Fail-open/ctx.fail_closed semantics are preserved.
        if self._profile_skip(report, GATE_CONTENT_GROUNDING, enabled, profile_name):
            pass
        elif retrieval:
            try:
                issues = []
                placeholders = _gate_find_placeholders(text)
                if placeholders:
                    issues.append(f"unresolved placeholders: {placeholders}")
                chunks = _context_texts(context_sources)
                if chunks and text:
                    grounding = _gate_ground_content(text, chunks, ctx)
                    floor = _content_grounding_floor()
                    report.content_grounding = {
                        "score": grounding.get("score"),
                        "method": grounding.get("method"),
                        "ungrounded_claims": grounding.get("ungrounded_claims", []),
                        "floor": floor,
                    }
                    score = grounding.get("score")
                    if score is not None and score < floor:
                        issues.append(
                            f"output weakly grounded in injected context "
                            f"(score={score} < floor={floor}, method="
                            f"{grounding.get('method')}, ungrounded="
                            f"{grounding.get('ungrounded_claims')})"
                        )
                else:
                    # No snippet text to ground against — the placeholder scan is
                    # the only available signal. Record the fallback method.
                    report.content_grounding = {
                        "score": None,
                        "method": "placeholder",
                        "ungrounded_claims": [],
                        "floor": _content_grounding_floor(),
                    }
                if issues:
                    grounded = False
                    detail = "; ".join(issues)
                    if resolve_fail_closed(ctx):
                        self._block(report, ctx, GATE_CONTENT_GROUNDING, detail,
                                    result=result, timing=timing, tally=tally)
                    self._record(report, GATE_CONTENT_GROUNDING, OUTCOME_WARN, detail)
                else:
                    self._record(
                        report, GATE_CONTENT_GROUNDING, OUTCOME_PASS,
                        f"method={report.content_grounding.get('method')} "
                        f"score={report.content_grounding.get('score')}",
                    )
            except GovernanceBlockedError:
                raise
            except Exception as exc:
                grounded = False
                self._degrade(report, ctx, GATE_CONTENT_GROUNDING, exc,
                              result=result, timing=timing, tally=tally)
        elif skip_grounding_for_plain_complete():
            self._record(report, GATE_CONTENT_GROUNDING, OUTCOME_SKIP, "non-retrieval call")
        else:
            # governance.skip_grounding_for_plain_complete: false — there is no
            # context to ground against, so the placeholder scan is the whole
            # gate: unresolved [PLACEHOLDER]/TBD tokens in free-form drafting
            # output are a defect whether or not evidence was injected.
            try:
                placeholders = _gate_find_placeholders(text)
                report.content_grounding = {
                    "score": None,
                    "method": "placeholder",
                    "ungrounded_claims": [],
                    "floor": _content_grounding_floor(),
                }
                if placeholders:
                    grounded = False
                    detail = f"unresolved placeholders: {placeholders}"
                    if resolve_fail_closed(ctx):
                        self._block(report, ctx, GATE_CONTENT_GROUNDING, detail)
                    self._record(report, GATE_CONTENT_GROUNDING, OUTCOME_WARN, detail)
                else:
                    self._record(
                        report, GATE_CONTENT_GROUNDING, OUTCOME_PASS, "method=placeholder",
                    )
            except GovernanceBlockedError:
                raise
            except Exception as exc:
                grounded = False
                self._degrade(report, ctx, GATE_CONTENT_GROUNDING, exc)

        # 6. Output redaction — never skipped, and applied to the CALLER-VISIBLE
        #    content of EVERY result shape. Egress PII/CUI masking is not optional
        #    and must NOT depend on `attach` (which only controls whether the
        #    governance report is attached, gate 482): the retrieval facades
        #    (search=list, ask=CortexResult) wrap attach=False, yet they surface
        #    retrieved corpus content that must be masked before it leaves.
        try:
            hits: list = []
            if is_cortex_result:
                masked_text, hits = _gate_redact_output(text)
                if masked_text != text:
                    text = masked_text
                    result.text = masked_text
            elif isinstance(result, list):
                # search: mask each hit's content in place; rebuild `text` from the
                # masked parts so the provenance hash (below) covers masked content.
                masked_parts: list = []
                for _item in result:
                    _content = getattr(_item, "content", None)
                    if isinstance(_content, str) and _content:
                        _masked, _h = _gate_redact_output(_content)
                        if _masked != _content:
                            try:
                                _item.content = _masked
                            except Exception:  # noqa: BLE001 — immutable item; skip
                                pass
                        hits.extend(_h)
                        masked_parts.append(_masked)
                if masked_parts:
                    text = "\n".join(masked_parts)
            elif isinstance(result, str):
                masked_text, hits = _gate_redact_output(text)
                if masked_text != text:
                    text = masked_text
                    result = masked_text
            else:
                masked_text, hits = _gate_redact_output(text)
                if masked_text != text:
                    text = masked_text
            report.redactions_applied += len(hits)
            self._record(
                report, GATE_OUTPUT_REDACTION,
                OUTCOME_WARN if hits else OUTCOME_PASS,
                f"patterns: {hits}" if hits else "",
            )
        except Exception as exc:
            self._degrade(report, ctx, GATE_OUTPUT_REDACTION, exc,
                          result=result, timing=timing, tally=tally)

        # 7. Provenance record + audit row — never skipped, never blocking.
        record_id = f"cgov-{uuid.uuid4().hex[:16]}"
        registry_id = ""
        try:
            registry_id = _gate_register_provenance(text, ctx, self.operation, record_id)
            self._record(
                report, GATE_PROVENANCE,
                OUTCOME_PASS if registry_id else OUTCOME_WARN,
                registry_id or "registry insert returned no id",
            )
        except ValueError as exc:
            # A ValueError from register_citation means the citation_type is not
            # in CITATION_TYPES — a PROGRAMMING error, not a runtime degradation,
            # and the two must not look alike. Recorded as "fail" rather than
            # "warn" so the audit trail distinguishes "provenance is misconfigured"
            # from "provenance was briefly unavailable".
            #
            # This is what hid the cxo-trust-01 bug: the gate recorded warn for a
            # bad vocabulary value, warn reads as degradation, and the subsystem
            # wrote 0 of 285 registry rows for its entire existence while looking
            # merely flaky. cxo-trust-02's linter now catches this at authoring
            # time; this is the runtime backstop for anything that slips past.
            #
            # Still NOT blocking. governance.fail_closed stays false and this
            # gate is documented as never blocking — changing that is a separate,
            # platform-wide decision. The fix is to make the failure legible, not
            # to start refusing traffic.
            logger.error(
                "cortex governance provenance MISCONFIGURED (not a transient "
                "failure): %s", exc,
            )
            self._record(report, GATE_PROVENANCE, OUTCOME_FAIL, str(exc))
        except Exception as exc:
            # Genuine operational failure — connection refused, timeout, table
            # missing. Degrades to warn, which is the fail-open posture.
            logger.warning("cortex governance provenance record failed: %s", exc)
            self._record(report, GATE_PROVENANCE, OUTCOME_WARN, str(exc))
        self._audit(report, ctx, provenance_id=registry_id or "", result=result,
                    timing=timing, tally=tally)

        if attach and is_cortex_result:
            result.governance = report
            result.grounded = grounded and not any(
                report.outcomes.get(g) in (OUTCOME_FAIL, OUTCOME_WARN)
                for g in (GATE_CITATION_GROUNDING, GATE_CONTENT_GROUNDING)
            )
        return result, report


def governed(
    fn: Optional[Callable] = None,
    *,
    retrieval: bool = True,
    operation: str = "cortex",
    pipeline: Optional[GovernancePipeline] = None,
    profile: str = "",
):
    """Decorator form of :meth:`GovernancePipeline.wrap`.

    The decorated function must take the (governed) prompt as its first
    positional argument. Callers may pass ``ctx=`` and ``context_sources=``
    keywords; both are consumed by the pipeline, not forwarded::

        @governed(operation="cortex.complete", retrieval=False)
        def complete(prompt, temperature=0.2): ...

        result, report = complete("draft a summary", ctx=ctx)
    """

    def decorate(func: Callable):
        pipe = pipeline or GovernancePipeline(operation=operation, profile=profile)

        @functools.wraps(func)
        def inner(prompt: str, *args, ctx: Optional[CortexContext] = None,
                  context_sources=None, **kwargs):
            return pipe.wrap(
                lambda governed_prompt: func(governed_prompt, *args, **kwargs),
                ctx,
                prompt=prompt,
                context_sources=context_sources,
                retrieval=retrieval,
            )

        return inner

    return decorate(fn) if callable(fn) else decorate
