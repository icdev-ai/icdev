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

Fail-open/fail-closed: gate errors degrade to ``"warn"`` by default (matching
``args/redaction_config.yaml`` ``fail_closed: false``); when
``CortexContext.fail_closed`` is True, any gate error or ``"fail"`` outcome
blocks the response instead.

Test seams: every external module call goes through a module-level
``_gate_*`` function so tests monkeypatch ``governance._gate_check_text`` etc.
without importing the heavy backends.
"""
from __future__ import annotations

import functools
import hashlib
import importlib
import json
import uuid
from typing import Callable, Optional

from tools.logging.icdev_logger import get_logger

from .config import load_cortex_config, resolve_fail_closed
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
    """

    def __init__(self, operation: str = "cortex", agent_id: str = "cortex"):
        self.operation = operation
        self.agent_id = agent_id

    # -- gate bookkeeping ---------------------------------------------------
    @staticmethod
    def _record(report: GovernanceReport, gate: str, outcome: str, detail: str = "") -> None:
        if outcome != OUTCOME_SKIP:
            report.gates_run.append(gate)
        report.outcomes[gate] = outcome
        if detail:
            logger.debug("cortex governance gate %s=%s: %s", gate, outcome, detail)

    def _degrade(
        self, report: GovernanceReport, ctx: CortexContext, gate: str, exc: Exception
    ) -> None:
        """Gate error: warn and continue (fail-open) or block on fail_closed."""
        if resolve_fail_closed(ctx):
            self._block(report, ctx, gate, f"{gate} unavailable: {exc}")
        logger.warning("cortex governance gate %s degraded (fail-open): %s", gate, exc)
        self._record(report, gate, OUTCOME_WARN, str(exc))

    def _block(
        self, report: GovernanceReport, ctx: CortexContext, gate: str, reason: str
    ) -> None:
        report.outcomes[gate] = OUTCOME_FAIL
        if gate not in report.gates_run:
            report.gates_run.append(gate)
        report.blocked = True
        report.blocked_reason = reason
        self._audit(report, ctx, blocked_gate=gate)
        raise GovernanceBlockedError(gate, reason, report)

    def _audit(
        self,
        report: GovernanceReport,
        ctx: CortexContext,
        blocked_gate: str = "",
        provenance_id: str = "",
        result=None,
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
                "gates_run": list(report.gates_run),
                "outcomes": dict(report.outcomes),
                "redactions_applied": report.redactions_applied,
                "provenance_id": provenance_id,
            }
            # Accounting for observability. cost/latency/provider/model live on
            # the CortexResult, not on cortex_audit columns — carry them in the
            # free-form gates_json blob so /cortex/metrics can aggregate spend
            # without a schema migration. This is INSERT-only; the append-only
            # audit invariant is untouched.
            if isinstance(result, CortexResult):
                payload["cost_usd"] = float(getattr(result, "cost", 0.0) or 0.0)
                payload["latency_ms"] = int(getattr(result, "latency_ms", 0) or 0)
                payload["provider"] = getattr(result, "provider", "") or ""
                payload["model"] = getattr(result, "model", "") or ""
                payload["input_tokens"] = int(getattr(result, "input_tokens", 0) or 0)
                payload["output_tokens"] = int(getattr(result, "output_tokens", 0) or 0)
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
        """
        ctx = ctx or CortexContext()
        report = GovernanceReport()

        # 1. Gateway pre-invoke check — a block ALWAYS fails closed.
        #    SKIPPED for trusted first-party content (ctx.trusted_content): the
        #    injection screen exists to defend against untrusted user input, and
        #    trusted callers (e.g. docgen ingesting a document already inside the
        #    tenant boundary) mirror the router's skip_injection_scan contract.
        #    The skip is recorded so it stays observable in the audit report.
        if ctx.trusted_content:
            self._record(report, GATE_PRE_CHECK, OUTCOME_SKIP, "trusted content")
        else:
            try:
                pre = _gate_check_text(prompt)
            except Exception as exc:
                pre = None
                self._degrade(report, ctx, GATE_PRE_CHECK, exc)
            if pre is not None:
                if not pre.get("allowed", True):
                    self._block(
                        report, ctx, GATE_PRE_CHECK,
                        pre.get("blocked_reason") or "blocked by LLM gateway pre-check",
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
        if ctx.trusted_content:
            self._record(report, GATE_INPUT_REDACTION, OUTCOME_SKIP, "trusted content")
        else:
            try:
                governed_prompt, masked = _gate_redact_input(prompt, ctx.classification)
                report.redactions_applied += masked
                self._record(report, GATE_INPUT_REDACTION, OUTCOME_PASS)
            except Exception as exc:
                self._degrade(report, ctx, GATE_INPUT_REDACTION, exc)

        # 3. The wrapped operation. Errors are recorded then re-raised —
        #    the pipeline governs, it does not swallow provider failures.
        try:
            result = fn(governed_prompt)
        except GovernanceBlockedError:
            raise
        except Exception:
            self._record(report, GATE_OPERATION, OUTCOME_FAIL)
            self._audit(report, ctx, blocked_gate=GATE_OPERATION)
            raise
        self._record(report, GATE_OPERATION, OUTCOME_PASS)

        is_cortex_result = isinstance(result, CortexResult)
        text = result.text if is_cortex_result else (result if isinstance(result, str) else str(result))

        # 4. Citation grounding (retrieval calls only; skip recorded).
        grounded = True
        if retrieval:
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
                            self._block(report, ctx, GATE_CITATION_GROUNDING, detail)
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
                    self._degrade(report, ctx, GATE_CITATION_GROUNDING, exc)
        else:
            grounded = False
            self._record(report, GATE_CITATION_GROUNDING, OUTCOME_SKIP, "non-retrieval call")

        # 5. Content grounding — semantic claim-vs-context grounding when the
        #    call is retrieval-backed AND snippet text is available; otherwise a
        #    placeholder scan. The score/method/floor are recorded on the report
        #    (report.content_grounding) so the gate is observable, and the
        #    warn/block threshold is the SHARED citation_grounding band, not a
        #    local constant. Fail-open/ctx.fail_closed semantics are preserved.
        if retrieval:
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
                        self._block(report, ctx, GATE_CONTENT_GROUNDING, detail)
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
                self._degrade(report, ctx, GATE_CONTENT_GROUNDING, exc)
        else:
            self._record(report, GATE_CONTENT_GROUNDING, OUTCOME_SKIP, "non-retrieval call")

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
            self._degrade(report, ctx, GATE_OUTPUT_REDACTION, exc)

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
        self._audit(report, ctx, provenance_id=registry_id or "", result=result)

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
        pipe = pipeline or GovernancePipeline(operation=operation)

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
