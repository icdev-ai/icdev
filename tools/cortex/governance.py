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
5. ``content_grounding``  — ``tools/quality/content_grounding`` placeholder
                            scan plus token-overlap cross-check of the output
                            against the injected context.
6. ``output_redaction``   — ``tools/llm/output_redactor`` masks PII/secrets in
                            the response text.
7. ``provenance``         — ``tools/provenance/registry.register_citation``
                            record + audit row. The dedicated cortex audit
                            table lands in ctx-govern-03; until then the audit
                            row is a structured logger record (stub).

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

# Minimum token-overlap recall for the output to count as grounded in the
# injected context (content_grounding gate). Deliberately low: the gate warns
# on *zero* overlap, it does not demand extractive answers.
_ATTRIBUTION_FLOOR = 0.05

_CLASSIFICATION_IL = {"CUI": "IL4", "CUI//SP-CTI": "IL5", "SECRET": "IL6"}


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


def _gate_attribution_score(chunk_text: str, output_text: str) -> float:
    """Gate 5b: token-overlap recall of one context chunk in the output."""
    return _mod("quality.citation_grounding").compute_attribution_score(
        chunk_text, output_text
    )


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
    """Gate 7b: append-only audit row.

    Logger stub until the cortex audit table lands in ctx-govern-03 — the
    payload is already the exact row shape that migration will persist.
    """
    logger.info("cortex_governance_audit %s", json.dumps(payload, default=str))


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
        if ctx.fail_closed:
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
        self, report: GovernanceReport, ctx: CortexContext, blocked_gate: str = ""
    ) -> None:
        try:
            _gate_record_audit(
                {
                    "record_id": f"cgov-{uuid.uuid4().hex[:16]}",
                    "operation": self.operation,
                    "agent_id": self.agent_id,
                    "tenant_id": ctx.tenant_id,
                    "user_id": ctx.user_id,
                    "classification": ctx.classification,
                    "blocked": report.blocked,
                    "blocked_gate": blocked_gate,
                    "blocked_reason": report.blocked_reason,
                    "gates_run": list(report.gates_run),
                    "outcomes": dict(report.outcomes),
                    "redactions_applied": report.redactions_applied,
                }
            )
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
    ) -> tuple:
        """Run ``fn(governed_prompt)`` inside the full TRUST chain.

        Returns ``(result, GovernanceReport)``. Raises
        :class:`GovernanceBlockedError` when a gate blocks (always for a
        pre-check block; for downstream gate failures only when
        ``ctx.fail_closed`` is True).
        """
        ctx = ctx or CortexContext()
        report = GovernanceReport()

        # 1. Gateway pre-invoke check — a block ALWAYS fails closed.
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

        # 2. Input redaction — never skipped.
        governed_prompt = prompt
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
                        if ctx.fail_closed:
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

        # 5. Content grounding cross-check against the injected context.
        if retrieval:
            try:
                issues = []
                placeholders = _gate_find_placeholders(text)
                if placeholders:
                    issues.append(f"unresolved placeholders: {placeholders}")
                chunks = _context_texts(context_sources)
                if chunks and text:
                    best = max(_gate_attribution_score(c, text) for c in chunks)
                    if best < _ATTRIBUTION_FLOOR:
                        issues.append(f"output shares no content with injected context (recall={best})")
                if issues:
                    grounded = False
                    detail = "; ".join(issues)
                    if ctx.fail_closed:
                        self._block(report, ctx, GATE_CONTENT_GROUNDING, detail)
                    self._record(report, GATE_CONTENT_GROUNDING, OUTCOME_WARN, detail)
                else:
                    self._record(report, GATE_CONTENT_GROUNDING, OUTCOME_PASS)
            except GovernanceBlockedError:
                raise
            except Exception as exc:
                grounded = False
                self._degrade(report, ctx, GATE_CONTENT_GROUNDING, exc)
        else:
            self._record(report, GATE_CONTENT_GROUNDING, OUTCOME_SKIP, "non-retrieval call")

        # 6. Output redaction — never skipped.
        try:
            masked_text, hits = _gate_redact_output(text)
            report.redactions_applied += len(hits)
            if masked_text != text:
                text = masked_text
                if is_cortex_result:
                    result.text = masked_text
                elif isinstance(result, str):
                    result = masked_text
            self._record(
                report, GATE_OUTPUT_REDACTION,
                OUTCOME_WARN if hits else OUTCOME_PASS,
                f"patterns: {hits}" if hits else "",
            )
        except Exception as exc:
            self._degrade(report, ctx, GATE_OUTPUT_REDACTION, exc)

        # 7. Provenance record + audit row — never skipped, never blocking.
        record_id = f"cgov-{uuid.uuid4().hex[:16]}"
        try:
            registry_id = _gate_register_provenance(text, ctx, self.operation, record_id)
            self._record(
                report, GATE_PROVENANCE,
                OUTCOME_PASS if registry_id else OUTCOME_WARN,
                registry_id or "registry insert returned no id",
            )
        except Exception as exc:
            logger.warning("cortex governance provenance record failed: %s", exc)
            self._record(report, GATE_PROVENANCE, OUTCOME_WARN, str(exc))
        self._audit(report, ctx)

        if is_cortex_result:
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
