# CUI // SP-CTI
"""Egress-redaction write-back tests (Cortex analysis item 2).

The retrieval facades wrap attach=False, but must still mask PII/CUI in the
CALLER-VISIBLE content before it leaves — previously the masked text was computed
and discarded. Monkeypatch the redactor to isolate the write-back logic from the
real pattern set.
"""
from __future__ import annotations

import tools.cortex.governance as gov
from tools.cortex.schemas import CortexContext, CortexResult, CortexSearchResult


def _mask_ssn(text):
    # Fake gate 6: replace a known token, report one hit.
    if "SECRET" in (text or ""):
        return text.replace("SECRET", "[REDACTED]"), ["pii"]
    return text, []


def _pipe(monkeypatch, op):
    monkeypatch.setattr(gov, "_gate_redact_output", _mask_ssn)
    return gov.GovernancePipeline(operation=op)


def test_ask_result_text_redacted_even_attach_false(monkeypatch):
    pipe = _pipe(monkeypatch, "cortex.ask")
    result, report = pipe.wrap(
        lambda p: CortexResult(text="answer with SECRET token"),
        CortexContext(tenant_id="t1"),
        prompt="q", retrieval=False, attach=False,
    )
    assert "SECRET" not in result.text
    assert "[REDACTED]" in result.text
    assert report.redactions_applied >= 1


def test_search_list_content_redacted_even_attach_false(monkeypatch):
    pipe = _pipe(monkeypatch, "cortex.search")
    hits = [
        CortexSearchResult(content="doc one has SECRET here"),
        CortexSearchResult(content="clean doc"),
    ]
    result, report = pipe.wrap(
        lambda p: hits,
        CortexContext(tenant_id="t1"),
        prompt="q", retrieval=False, attach=False,
    )
    assert "SECRET" not in result[0].content
    assert "[REDACTED]" in result[0].content
    assert result[1].content == "clean doc"
    assert report.redactions_applied >= 1


def test_complete_attach_true_still_redacts(monkeypatch):
    # regression: the attach=True path must keep masking result.text.
    pipe = _pipe(monkeypatch, "cortex.complete")
    result, report = pipe.wrap(
        lambda p: CortexResult(text="draft with SECRET"),
        CortexContext(tenant_id="t1"),
        prompt="q", retrieval=False, attach=True,
    )
    assert "SECRET" not in result.text
    assert result.governance is report  # report still attached under attach=True


def test_no_pii_leaves_content_untouched(monkeypatch):
    pipe = _pipe(monkeypatch, "cortex.ask")
    result, _ = pipe.wrap(
        lambda p: CortexResult(text="perfectly clean answer"),
        CortexContext(tenant_id="t1"),
        prompt="q", retrieval=False, attach=False,
    )
    assert result.text == "perfectly clean answer"
