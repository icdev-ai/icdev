# CUI // SP-CTI
"""Tests for ctx-govern-02: citation_gate integration, [SOURCE-N] citation
normalization, and fail-closed redaction toggles in the GovernancePipeline.

Covers the task's acceptance criteria:
  - citation-free retrieval answers are gated (rejected or visibly
    downgraded per args/cortex_config.yaml governance.uncited_policy)
  - [SOURCE-N] answers validate after normalization to [source: N], with
    tools/quality/citation_grounding.py staying the single validator
  - a redaction module ImportError + fail_closed=True refuses with an
    audited error; fail_closed=False passes with the skip noted in the
    GovernanceReport; unset fail_closed defaults CLOSED for CUI
"""
from __future__ import annotations

import pytest

from tools.cortex import governance
from tools.cortex.governance import (
    GATE_CITATION_GROUNDING,
    GATE_INPUT_REDACTION,
    GATE_OUTPUT_REDACTION,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_WARN,
    UNGROUNDED_BANNER,
    GovernanceBlockedError,
    GovernancePipeline,
    normalize_citation_format,
)
from tools.cortex.schemas import CortexContext, CortexResult


def _pre_ok():
    return {"allowed": True, "warnings": [], "blocked_reason": None}


@pytest.fixture
def calls(monkeypatch):
    """Benign fakes for every non-citation gate seam; record invocations."""
    record: dict = {"audit": [], "provenance": []}
    monkeypatch.setattr(governance, "_gate_check_text", lambda text: _pre_ok())
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))
    monkeypatch.setattr(
        governance, "_gate_register_provenance",
        lambda output_text, ctx, operation, record_id: record["provenance"].append(record_id)
        or "scr-test",
    )
    monkeypatch.setattr(
        governance, "_gate_record_audit", lambda payload: record["audit"].append(payload)
    )
    return record


SOURCES = [
    {"source_id": "1", "content": "FedRAMP AC-2 requires account management controls."},
    {"source_id": "2", "content": "Accounts are disabled after 90 days of inactivity."},
]


# ---------------------------------------------------------------------------
# [SOURCE-N] -> [source: N] normalization
# ---------------------------------------------------------------------------
def test_normalize_translates_source_n_refs():
    assert normalize_citation_format("Claim [SOURCE-1].") == "Claim [source: 1]."
    assert normalize_citation_format("A [SOURCE-1] b [SOURCE-12].") == (
        "A [source: 1] b [source: 12]."
    )


def test_normalize_is_case_insensitive_and_preserves_shared_format():
    assert normalize_citation_format("x [Source-3] y") == "x [source: 3] y"
    # already-shared-format tags pass through untouched
    text = "Cited [source: chunk ab12] and [source: 2]."
    assert normalize_citation_format(text) == text
    assert normalize_citation_format("") == ""


def test_source_n_answer_validates_after_normalization(calls):
    result, report = GovernancePipeline().wrap(
        lambda p: CortexResult(text="Accounts are disabled after 90 days [SOURCE-2]."),
        CortexContext(), prompt="q", context_sources=SOURCES,
    )
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_PASS
    assert result.grounded is True
    assert result.banner == ""


def test_source_n_ref_to_unavailable_source_is_hallucinated(calls):
    result, report = GovernancePipeline().wrap(
        lambda p: CortexResult(text="Account management controls are required [SOURCE-9]."),
        CortexContext(), prompt="q", context_sources=SOURCES,
    )
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_FAIL
    assert result.grounded is False
    assert result.banner == UNGROUNDED_BANNER


# ---------------------------------------------------------------------------
# Zero-valid-citation policy: downgrade vs reject (args/cortex_config.yaml)
# ---------------------------------------------------------------------------
def test_uncited_answer_downgraded_with_banner_under_downgrade_policy(calls, monkeypatch):
    monkeypatch.setattr(
        governance, "_governance_config", lambda: {"uncited_policy": "downgrade"}
    )
    result, report = GovernancePipeline().wrap(
        lambda p: CortexResult(text="Accounts are disabled after 90 days of inactivity."),
        CortexContext(), prompt="q", context_sources=SOURCES,
    )
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_WARN
    assert result.grounded is False
    assert result.banner == UNGROUNDED_BANNER
    assert not report.blocked


def test_uncited_answer_rejected_under_reject_policy(calls, monkeypatch):
    monkeypatch.setattr(
        governance, "_governance_config", lambda: {"uncited_policy": "reject"}
    )
    with pytest.raises(GovernanceBlockedError) as exc_info:
        GovernancePipeline().wrap(
            lambda p: CortexResult(text="Uncited retrieval answer."),
            CortexContext(), prompt="q", context_sources=SOURCES,
        )
    assert exc_info.value.gate == GATE_CITATION_GROUNDING
    assert exc_info.value.report.blocked is True
    # refusal is audited
    assert calls["audit"][0]["blocked_gate"] == GATE_CITATION_GROUNDING


def test_uncited_answer_rejected_when_ctx_fail_closed_regardless_of_policy(calls, monkeypatch):
    monkeypatch.setattr(
        governance, "_governance_config", lambda: {"uncited_policy": "downgrade"}
    )
    with pytest.raises(GovernanceBlockedError):
        GovernancePipeline().wrap(
            lambda p: "Uncited retrieval answer.",
            CortexContext(fail_closed=True), prompt="q", context_sources=SOURCES,
        )


def test_cited_answer_passes_and_carries_no_banner(calls):
    result, report = GovernancePipeline().wrap(
        lambda p: CortexResult(text="Controls are required [source: 1]."),
        CortexContext(), prompt="q", context_sources=SOURCES,
    )
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_PASS
    assert result.grounded is True
    assert result.banner == ""


# ---------------------------------------------------------------------------
# Fail-closed redaction: outage must refuse for CUI, never pass unredacted
# ---------------------------------------------------------------------------
@pytest.fixture
def redaction_import_error(monkeypatch):
    """Simulate the redaction module failing to import at call time."""

    def boom(*args, **kwargs):
        raise ImportError("No module named 'presidio_analyzer'")

    monkeypatch.setattr(governance, "_gate_check_text", lambda text: _pre_ok())
    monkeypatch.setattr(governance, "_gate_redact_input", boom)
    monkeypatch.setattr(governance, "_gate_redact_output", boom)
    monkeypatch.setattr(
        governance, "_gate_register_provenance", lambda *a, **k: "scr-test"
    )
    audits: list = []
    monkeypatch.setattr(governance, "_gate_record_audit", audits.append)
    return audits


def test_redaction_import_error_refuses_when_fail_closed_true(redaction_import_error):
    operation_ran = []
    with pytest.raises(GovernanceBlockedError) as exc_info:
        GovernancePipeline().wrap(
            lambda p: operation_ran.append(p) or "out",
            CortexContext(fail_closed=True), prompt="ssn 123-45-6789", retrieval=False,
        )
    assert not operation_ran, "unredacted prompt must never reach the operation"
    err = exc_info.value
    assert err.gate == GATE_INPUT_REDACTION
    assert "presidio" in err.reason
    # refusal is audited
    audits = redaction_import_error
    assert len(audits) == 1
    assert audits[0]["blocked_gate"] == GATE_INPUT_REDACTION
    assert audits[0]["blocked"] is True


def test_redaction_import_error_passes_with_skip_noted_when_fail_closed_false(
    redaction_import_error,
):
    result, report = GovernancePipeline().wrap(
        lambda p: "out", CortexContext(fail_closed=False), prompt="q", retrieval=False
    )
    assert result == "out"
    assert not report.blocked
    # GovernanceReport notes the skip on both redaction gates
    assert report.outcomes[GATE_INPUT_REDACTION] == OUTCOME_WARN
    assert report.outcomes[GATE_OUTPUT_REDACTION] == OUTCOME_WARN
    assert "skipped" in report.details[GATE_INPUT_REDACTION]
    assert "skipped" in report.details[GATE_OUTPUT_REDACTION]


def test_unset_fail_closed_defaults_closed_for_cui(redaction_import_error, monkeypatch):
    monkeypatch.setattr(
        governance, "_governance_config",
        lambda: {"redaction_fail_closed_classifications": ["CUI", "SECRET"]},
    )
    with pytest.raises(GovernanceBlockedError) as exc_info:
        GovernancePipeline().wrap(
            lambda p: "out", CortexContext(), prompt="q", retrieval=False
        )
    assert exc_info.value.gate == GATE_INPUT_REDACTION


def test_unset_fail_closed_matches_cui_subcategories_by_prefix(
    redaction_import_error, monkeypatch
):
    monkeypatch.setattr(
        governance, "_governance_config",
        lambda: {"redaction_fail_closed_classifications": ["CUI", "SECRET"]},
    )
    with pytest.raises(GovernanceBlockedError):
        GovernancePipeline().wrap(
            lambda p: "out",
            CortexContext(classification="CUI//SP-CTI"), prompt="q", retrieval=False,
        )


def test_unset_fail_closed_stays_open_for_unclassified(redaction_import_error, monkeypatch):
    monkeypatch.setattr(
        governance, "_governance_config",
        lambda: {"redaction_fail_closed_classifications": ["CUI", "SECRET"]},
    )
    result, report = GovernancePipeline().wrap(
        lambda p: "out",
        CortexContext(classification="UNCLASSIFIED"), prompt="q", retrieval=False,
    )
    assert result == "out"
    assert report.outcomes[GATE_INPUT_REDACTION] == OUTCOME_WARN


def test_output_redaction_outage_also_fails_closed_for_cui(monkeypatch):
    """An egress redaction outage must not leak the response for CUI."""
    monkeypatch.setattr(governance, "_gate_check_text", lambda text: _pre_ok())
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, cls: (text, 0))

    def boom(text):
        raise ImportError("output redactor unavailable")

    monkeypatch.setattr(governance, "_gate_redact_output", boom)
    monkeypatch.setattr(
        governance, "_gate_register_provenance", lambda *a, **k: "scr-test"
    )
    monkeypatch.setattr(governance, "_gate_record_audit", lambda payload: None)
    monkeypatch.setattr(
        governance, "_governance_config",
        lambda: {"redaction_fail_closed_classifications": ["CUI", "SECRET"]},
    )
    with pytest.raises(GovernanceBlockedError) as exc_info:
        GovernancePipeline().wrap(
            lambda p: "response text", CortexContext(), prompt="q", retrieval=False
        )
    assert exc_info.value.gate == GATE_OUTPUT_REDACTION


# ---------------------------------------------------------------------------
# Config wiring: the shipped args/cortex_config.yaml carries the toggles
# ---------------------------------------------------------------------------
def test_shipped_config_defines_ctx_govern_02_toggles():
    from tools.cortex.config import CORTEX_CONFIG_DEFAULTS, load_cortex_config

    for cfg in (CORTEX_CONFIG_DEFAULTS, load_cortex_config(refresh=True)):
        gov = cfg["governance"]
        assert gov["uncited_policy"] in ("reject", "downgrade")
        assert "CUI" in gov["redaction_fail_closed_classifications"]
        # never remove the pre-existing toggles (ctx-govern-01)
        assert "fail_closed" in gov
        assert "skip_grounding_for_plain_complete" in gov


def test_shim_and_canonical_governance_are_identical_source():
    import pathlib

    import icdev.tools.cortex.governance as canonical

    canonical_src = pathlib.Path(canonical.__file__).read_text(encoding="utf-8")
    repo_root = pathlib.Path(canonical.__file__).resolve().parents[3]
    shim_src = (repo_root / "tools" / "cortex" / "governance.py").read_text(encoding="utf-8")
    assert canonical_src == shim_src
