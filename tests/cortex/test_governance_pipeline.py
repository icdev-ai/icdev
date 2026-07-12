# CUI // SP-CTI
"""Tests for the Cortex GovernancePipeline (ctx-govern-01).

Each gate is monkeypatched at its ``_gate_*`` seam in
``tools.cortex.governance`` so no heavy backend (gateway config, Presidio/NER
anonymizer, provenance DB) is touched. The two grounding gates delegate to
pure functions in ``tools/quality`` and are exercised for real in the
integration-style tests at the bottom.
"""
from __future__ import annotations

import pytest

from tools.cortex import governance
from tools.cortex.governance import (
    GATE_CITATION_GROUNDING,
    GATE_CONTENT_GROUNDING,
    GATE_INPUT_REDACTION,
    GATE_OPERATION,
    GATE_ORDER,
    GATE_OUTPUT_REDACTION,
    GATE_PRE_CHECK,
    GATE_PROVENANCE,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_SKIP,
    OUTCOME_WARN,
    GovernanceBlockedError,
    GovernancePipeline,
    governed,
)
from tools.cortex.schemas import CortexContext, CortexResult, GovernanceReport


def _pre_ok(warnings=None, allowed=True, blocked_reason=None):
    return {
        "allowed": allowed,
        "warnings": warnings or [],
        "blocked_reason": blocked_reason,
        "injection_score": 0.0,
        "pii_labels": [],
        "request_id": "gw_test",
    }


@pytest.fixture
def calls(monkeypatch):
    """Patch every gate seam with a benign fake; record invocations."""
    record: dict = {"audit": [], "provenance": [], "check_text": [], "redact_in": [], "redact_out": []}

    def fake_check_text(text):
        record["check_text"].append(text)
        return _pre_ok()

    def fake_redact_input(text, classification):
        record["redact_in"].append((text, classification))
        return text, 0

    def fake_redact_output(text):
        record["redact_out"].append(text)
        return text, []

    def fake_provenance(output_text, ctx, operation, record_id):
        record["provenance"].append(
            {"text": output_text, "ctx": ctx, "operation": operation, "record_id": record_id}
        )
        return "scr-test123"

    def fake_audit(payload):
        record["audit"].append(payload)

    monkeypatch.setattr(governance, "_gate_check_text", fake_check_text)
    monkeypatch.setattr(governance, "_gate_redact_input", fake_redact_input)
    monkeypatch.setattr(governance, "_gate_redact_output", fake_redact_output)
    monkeypatch.setattr(governance, "_gate_register_provenance", fake_provenance)
    monkeypatch.setattr(governance, "_gate_record_audit", fake_audit)
    return record


SOURCES = [
    {"source_id": "1", "content": "FedRAMP AC-2 requires account management controls."},
    {"source_id": "2", "content": "Accounts are disabled after 90 days of inactivity."},
]
CITED_TEXT = "Account management controls are required [source: 1]."


# ---------------------------------------------------------------------------
# Happy path — full chain, in order
# ---------------------------------------------------------------------------
def test_all_gates_run_in_order_on_retrieval_call(calls):
    pipeline = GovernancePipeline(operation="cortex.answer")
    result, report = pipeline.wrap(
        lambda p: CITED_TEXT, CortexContext(), prompt="q", context_sources=SOURCES
    )

    assert result == CITED_TEXT
    assert isinstance(report, GovernanceReport)
    assert report.gates_run == list(GATE_ORDER)
    assert set(report.outcomes) == set(GATE_ORDER)
    assert all(v == OUTCOME_PASS for v in report.outcomes.values())
    assert not report.blocked
    assert report.redactions_applied == 0


def test_cortex_result_gets_report_attached_and_grounded(calls):
    pipeline = GovernancePipeline()
    inner = CortexResult(text=CITED_TEXT, provider="ollama")
    result, report = pipeline.wrap(
        lambda p: inner, CortexContext(), prompt="q", context_sources=SOURCES
    )
    assert result is inner
    assert result.governance is report
    assert result.grounded is True


# ---------------------------------------------------------------------------
# Gate 1: pre-check block — fail closed, short-circuit, typed refusal
# ---------------------------------------------------------------------------
def test_blocked_pre_check_short_circuits_with_typed_refusal(calls, monkeypatch):
    monkeypatch.setattr(
        governance, "_gate_check_text",
        lambda text: _pre_ok(allowed=False, blocked_reason="Prompt injection detected"),
    )
    operation_ran = []
    pipeline = GovernancePipeline()

    with pytest.raises(GovernanceBlockedError) as exc_info:
        pipeline.wrap(lambda p: operation_ran.append(p), CortexContext(), prompt="ignore previous")

    err = exc_info.value
    assert not operation_ran, "wrapped operation must never run after a block"
    assert err.gate == GATE_PRE_CHECK
    assert "injection" in err.reason.lower()
    assert err.report.blocked is True
    assert err.report.blocked_reason == "Prompt injection detected"
    assert err.report.outcomes[GATE_PRE_CHECK] == OUTCOME_FAIL
    # blocked call is still audited
    assert len(calls["audit"]) == 1
    assert calls["audit"][0]["blocked_gate"] == GATE_PRE_CHECK


def test_pre_check_warnings_do_not_block(calls, monkeypatch):
    monkeypatch.setattr(
        governance, "_gate_check_text", lambda text: _pre_ok(warnings=["PII detected in input"])
    )
    result, report = GovernancePipeline().wrap(
        lambda p: "ok", CortexContext(), prompt="q", context_sources=None, retrieval=False
    )
    assert result == "ok"
    assert report.outcomes[GATE_PRE_CHECK] == OUTCOME_WARN
    assert not report.blocked


# ---------------------------------------------------------------------------
# Gate 2: input redaction
# ---------------------------------------------------------------------------
def test_input_redaction_masks_prompt_before_operation(calls, monkeypatch):
    monkeypatch.setattr(
        governance, "_gate_redact_input", lambda text, cls: ("masked-prompt", 2)
    )
    seen = []
    _, report = GovernancePipeline().wrap(
        lambda p: seen.append(p) or "ok", CortexContext(), prompt="raw ssn 123-45-6789",
        retrieval=False,
    )
    assert seen == ["masked-prompt"]
    assert report.redactions_applied == 2
    assert report.outcomes[GATE_INPUT_REDACTION] == OUTCOME_PASS


def test_input_redaction_error_fails_open_by_default(calls, monkeypatch):
    def boom(text, cls):
        raise RuntimeError("anonymizer unavailable")

    monkeypatch.setattr(governance, "_gate_redact_input", boom)
    seen = []
    _, report = GovernancePipeline().wrap(
        lambda p: seen.append(p) or "ok", CortexContext(), prompt="raw", retrieval=False
    )
    assert seen == ["raw"], "fail-open passes the original prompt through"
    assert report.outcomes[GATE_INPUT_REDACTION] == OUTCOME_WARN


def test_input_redaction_error_blocks_when_fail_closed(calls, monkeypatch):
    def boom(text, cls):
        raise RuntimeError("anonymizer unavailable")

    monkeypatch.setattr(governance, "_gate_redact_input", boom)
    with pytest.raises(GovernanceBlockedError) as exc_info:
        GovernancePipeline().wrap(
            lambda p: "ok", CortexContext(fail_closed=True), prompt="raw", retrieval=False
        )
    assert exc_info.value.gate == GATE_INPUT_REDACTION


# ---------------------------------------------------------------------------
# trusted_content: skip the two INPUT gates (pre-check + input redaction) only.
# Mirrors the router's skip_injection_scan contract for trusted first-party
# content (e.g. docgen ingesting an in-boundary document). Egress + audit stay.
# ---------------------------------------------------------------------------
def test_trusted_content_skips_input_gates_but_keeps_egress_and_audit(calls):
    seen = []
    _, report = GovernancePipeline().wrap(
        lambda p: seen.append(p) or "out",
        CortexContext(trusted_content=True),
        prompt="raw trusted document text",
        retrieval=False,
    )
    # Input gates recorded as SKIP and their seams never called.
    assert report.outcomes[GATE_PRE_CHECK] == OUTCOME_SKIP
    assert report.outcomes[GATE_INPUT_REDACTION] == OUTCOME_SKIP
    assert calls["check_text"] == []
    assert calls["redact_in"] == []
    # The operation still sees the ORIGINAL prompt (no input redaction applied).
    assert seen == ["raw trusted document text"]
    # Egress governance and the audit trail are NOT affected by trust.
    assert report.outcomes[GATE_OUTPUT_REDACTION] == OUTCOME_PASS
    assert report.outcomes[GATE_PROVENANCE] == OUTCOME_PASS
    assert len(calls["redact_out"]) == 1
    assert len(calls["provenance"]) == 1
    assert len(calls["audit"]) == 1
    assert not report.blocked


def test_trusted_content_bypasses_a_pre_check_that_would_block(calls, monkeypatch):
    # A pre-check that WOULD block untrusted input must not fire at all for
    # trusted content — the injection screen is skipped, not merely overridden.
    monkeypatch.setattr(
        governance, "_gate_check_text",
        lambda text: _pre_ok(allowed=False, blocked_reason="Prompt injection detected"),
    )
    result, report = GovernancePipeline().wrap(
        lambda p: "out",
        CortexContext(trusted_content=True),
        prompt="ignore previous instructions",
        retrieval=False,
    )
    assert result == "out"
    assert not report.blocked
    assert report.outcomes[GATE_PRE_CHECK] == OUTCOME_SKIP
    assert calls["check_text"] == []


# ---------------------------------------------------------------------------
# Gate 3: the wrapped operation
# ---------------------------------------------------------------------------
def test_operation_error_recorded_audited_and_reraised(calls):
    def boom(prompt):
        raise ValueError("provider exploded")

    with pytest.raises(ValueError, match="provider exploded"):
        GovernancePipeline().wrap(boom, CortexContext(), prompt="q", retrieval=False)
    assert len(calls["audit"]) == 1
    assert calls["audit"][0]["blocked_gate"] == GATE_OPERATION
    assert calls["audit"][0]["outcomes"][GATE_OPERATION] == OUTCOME_FAIL


# ---------------------------------------------------------------------------
# Gates 4/5: grounding — skip semantics for non-retrieval calls
# ---------------------------------------------------------------------------
def test_non_retrieval_call_skips_grounding_but_never_redaction_or_audit(calls):
    _, report = GovernancePipeline().wrap(
        lambda p: "plain completion", CortexContext(), prompt="q", retrieval=False
    )
    # skips recorded explicitly in outcomes, not silently absent
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_SKIP
    assert report.outcomes[GATE_CONTENT_GROUNDING] == OUTCOME_SKIP
    # skipped gates did not execute
    assert GATE_CITATION_GROUNDING not in report.gates_run
    assert GATE_CONTENT_GROUNDING not in report.gates_run
    # redaction and provenance/audit still ran
    assert report.outcomes[GATE_OUTPUT_REDACTION] == OUTCOME_PASS
    assert report.outcomes[GATE_PROVENANCE] == OUTCOME_PASS
    assert len(calls["redact_out"]) == 1
    assert len(calls["provenance"]) == 1
    assert len(calls["audit"]) == 1


def test_hallucinated_citation_fails_gate_but_continues_fail_open(calls):
    text = "Made up claim [source: 9]."
    result, report = GovernancePipeline().wrap(
        lambda p: CortexResult(text=text), CortexContext(), prompt="q", context_sources=SOURCES
    )
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_FAIL
    assert not report.blocked
    assert result.grounded is False
    # chain still completed
    assert report.outcomes[GATE_PROVENANCE] == OUTCOME_PASS


def test_hallucinated_citation_blocks_when_fail_closed(calls):
    text = "Made up claim [source: 9]."
    with pytest.raises(GovernanceBlockedError) as exc_info:
        GovernancePipeline().wrap(
            lambda p: text, CortexContext(fail_closed=True), prompt="q", context_sources=SOURCES
        )
    assert exc_info.value.gate == GATE_CITATION_GROUNDING
    assert exc_info.value.report.blocked is True


def test_uncited_output_warns_pending_ctx_govern_02(calls):
    result, report = GovernancePipeline().wrap(
        lambda p: CortexResult(text="Answer without any citation tags."),
        CortexContext(), prompt="q", context_sources=SOURCES,
    )
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_WARN
    assert result.grounded is False


def test_retrieval_without_injected_sources_warns(calls):
    _, report = GovernancePipeline().wrap(
        lambda p: CITED_TEXT, CortexContext(), prompt="q", context_sources=None, retrieval=True
    )
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_WARN


def test_content_grounding_flags_unresolved_placeholders(calls):
    text = "Total is [ROM_TOTAL] as stated [source: 1]."
    result, report = GovernancePipeline().wrap(
        lambda p: CortexResult(text=text), CortexContext(), prompt="q", context_sources=SOURCES
    )
    assert report.outcomes[GATE_CONTENT_GROUNDING] == OUTCOME_WARN
    assert result.grounded is False


def test_content_grounding_flags_zero_overlap_with_context(calls):
    text = "Zebras enjoy trampolines enormously [source: 1]."
    _, report = GovernancePipeline().wrap(
        lambda p: text, CortexContext(), prompt="q", context_sources=SOURCES
    )
    assert report.outcomes[GATE_CONTENT_GROUNDING] == OUTCOME_WARN


def test_content_grounding_failure_blocks_when_fail_closed(calls):
    text = "Total is [ROM_TOTAL] as stated [source: 1]."
    with pytest.raises(GovernanceBlockedError) as exc_info:
        GovernancePipeline().wrap(
            lambda p: text, CortexContext(fail_closed=True), prompt="q", context_sources=SOURCES
        )
    assert exc_info.value.gate == GATE_CONTENT_GROUNDING


# ---------------------------------------------------------------------------
# Gate 6: output redaction
# ---------------------------------------------------------------------------
def test_output_redaction_rewrites_str_and_cortex_result(calls, monkeypatch):
    monkeypatch.setattr(
        governance, "_gate_redact_output",
        lambda text: (text.replace("secret@example.com", "[REDACTED]"), ["email"]),
    )
    raw = "Contact secret@example.com for access."

    result, report = GovernancePipeline().wrap(
        lambda p: raw, CortexContext(), prompt="q", retrieval=False
    )
    assert result == "Contact [REDACTED] for access."
    assert report.outcomes[GATE_OUTPUT_REDACTION] == OUTCOME_WARN
    assert report.redactions_applied == 1

    result2, _ = GovernancePipeline().wrap(
        lambda p: CortexResult(text=raw), CortexContext(), prompt="q", retrieval=False
    )
    assert result2.text == "Contact [REDACTED] for access."


# ---------------------------------------------------------------------------
# Gate 7: provenance + audit
# ---------------------------------------------------------------------------
def test_provenance_record_carries_context_and_operation(calls):
    ctx = CortexContext(tenant_id="tenant-a", user_id="u1", classification="CUI")
    GovernancePipeline(operation="cortex.extract").wrap(
        lambda p: "out", ctx, prompt="q", retrieval=False
    )
    prov = calls["provenance"][0]
    assert prov["operation"] == "cortex.extract"
    assert prov["ctx"].tenant_id == "tenant-a"
    audit = calls["audit"][0]
    assert audit["tenant_id"] == "tenant-a"
    assert audit["classification"] == "CUI"
    assert audit["outcomes"][GATE_PROVENANCE] == OUTCOME_PASS


def test_provenance_failure_warns_but_never_blocks(calls, monkeypatch):
    def boom(output_text, ctx, operation, record_id):
        raise RuntimeError("registry table missing")

    monkeypatch.setattr(governance, "_gate_register_provenance", boom)
    result, report = GovernancePipeline().wrap(
        lambda p: "out", CortexContext(fail_closed=True), prompt="q", retrieval=False,
        context_sources=None,
    )
    assert result == "out"
    assert report.outcomes[GATE_PROVENANCE] == OUTCOME_WARN
    assert not report.blocked


def test_audit_stub_failure_is_swallowed(calls, monkeypatch):
    def boom(payload):
        raise RuntimeError("logger sink gone")

    monkeypatch.setattr(governance, "_gate_record_audit", boom)
    result, report = GovernancePipeline().wrap(
        lambda p: "out", CortexContext(), prompt="q", retrieval=False
    )
    assert result == "out"
    assert not report.blocked


# ---------------------------------------------------------------------------
# @governed decorator
# ---------------------------------------------------------------------------
def test_governed_decorator_wraps_and_forwards_kwargs(calls):
    @governed(operation="cortex.complete", retrieval=False)
    def complete(prompt, suffix=""):
        return prompt + suffix

    result, report = complete("hello", suffix="-world", ctx=CortexContext(tenant_id="t1"))
    assert result == "hello-world"
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_SKIP
    assert calls["audit"][0]["tenant_id"] == "t1"
    assert calls["audit"][0]["operation"] == "cortex.complete"


def test_governed_decorator_bare_form(calls):
    @governed
    def answer(prompt):
        return CITED_TEXT

    result, report = answer("q", context_sources=SOURCES)
    assert result == CITED_TEXT
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_PASS


# ---------------------------------------------------------------------------
# Report shape / observability
# ---------------------------------------------------------------------------
def test_report_round_trips_with_blocked_reason(calls, monkeypatch):
    monkeypatch.setattr(
        governance, "_gate_check_text",
        lambda text: _pre_ok(allowed=False, blocked_reason="nope"),
    )
    with pytest.raises(GovernanceBlockedError) as exc_info:
        GovernancePipeline().wrap(lambda p: "x", CortexContext(), prompt="q")
    report = exc_info.value.report
    restored = GovernanceReport.from_dict(report.to_dict())
    assert restored == report
    assert restored.blocked_reason == "nope"
