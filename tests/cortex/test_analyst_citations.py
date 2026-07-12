# CUI // SP-CTI
"""Tests for Cortex Analyst citations + confidence labeling (ctx-analyst-03).

Raw row answers are grounded by construction; ``summarize=True`` answers are
LLM prose whose inline ``[source: ...]`` tags must validate against the
result's synthesized citations via the SHARED
``tools.quality.citation_grounding`` module. Fixture answers cover valid,
stale (hallucinated), and absent citation tags, plus the low-attribution
"abstain" band. ``_llm_summarize`` is stubbed at the module attribute so no
LLM call ever happens here.

Satellites fixture rows produce the citation snippet::

    2 rows; id=1, name='alpha', status='active'; id=2, name='bravo', status='retired'

whose token set (10 tokens: 2, rows, id, 1, name, alpha, status, active,
bravo, retired) the fixture answers overlap deliberately to land in the
include (>=0.7) / flag (0.4-0.69) / abstain (<0.4) attribution bands.
"""
from __future__ import annotations

import pytest

from tools.cortex import analyst as _analyst
from tools.cortex.analyst import ask
from tools.cortex.schemas import CortexContext
from tools.iqe import executor as _executor
from tools.iqe.executor import register_collection
from tools.llm import gateway as _gateway
from tools.quality import citation_grounding as _grounding

_SATELLITES = [
    {"id": 1, "name": "alpha", "status": "active"},
    {"id": 2, "name": "bravo", "status": "retired"},
]

_GATEWAY_OK = {
    "pre_invoke": {"allowed": True, "warnings": [], "blocked_reason": None},
    "post_invoke": {},
}


class StubConn:
    def set_security_context(self, ctx):
        self.security_context = ctx


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Fake satellites collection + stubbed gateway; no LLM, no real DB."""
    saved = dict(_executor._default._registry)
    register_collection("satellites", lambda conn: list(_SATELLITES))
    monkeypatch.setattr(_gateway, "check_text", lambda text: dict(_GATEWAY_OK))
    yield
    _executor._default._registry.clear()
    _executor._default._registry.update(saved)


def _stub_summary(monkeypatch, text):
    monkeypatch.setattr(_analyst, "_llm_summarize", lambda q, rows, cits: text)


# ---------------------------------------------------------------------------
# Shared-module invariant + citation synthesis
# ---------------------------------------------------------------------------
def test_citation_validation_uses_shared_module():
    # TRUST invariant: no re-implemented citation parsing — the analyst binds
    # the exact functions from tools/quality/citation_grounding.py.
    assert _analyst.parse_citations is _grounding.parse_citations
    assert _analyst.validate_citations is _grounding.validate_citations
    assert _analyst.compute_attribution_score is _grounding.compute_attribution_score
    assert _analyst.classify_confidence is _grounding.classify_confidence


def test_citations_carry_row_summary_snippet():
    result = ask("show all satellites", conn=StubConn())
    cit = result.citations[0]
    assert cit.source_type == "analyst"
    assert cit.source_table == "satellites"
    assert cit.source_id == "iqe:satellites"
    assert cit.snippet == (
        "2 rows; id=1, name='alpha', status='active'; "
        "id=2, name='bravo', status='retired'"
    )


# ---------------------------------------------------------------------------
# Raw row answers — grounded by construction
# ---------------------------------------------------------------------------
def test_raw_rows_grounded_by_construction():
    result = ask("show all satellites", conn=StubConn())
    assert result.grounded is True
    assert result.metadata["grounding"] == "rows_by_construction"
    assert result.metadata["confidence"] == "include"
    assert result.metadata["confidence_score"] == 1.0
    # No LLM text -> no citation gate entry (analyst-01 gate sequence stable).
    assert "citation_validation" not in result.governance.outcomes


def test_summarizer_unavailable_degrades_to_rows(monkeypatch):
    _stub_summary(monkeypatch, None)
    result = ask("show all satellites", conn=StubConn(), summarize=True)
    assert result.grounded is True
    assert result.metadata["grounding"] == "rows_by_construction"
    assert "2 rows" in result.text
    assert "summarized" not in result.data


# ---------------------------------------------------------------------------
# LLM-summarized answers — valid / stale / absent [source: ...] tags
# ---------------------------------------------------------------------------
def test_llm_summary_valid_citations_grounded_with_label(monkeypatch):
    # Every snippet token appears in the answer -> attribution 1.0 -> include.
    _stub_summary(
        monkeypatch,
        "There are 2 rows: id 1 name alpha status active, and id 2 name "
        "bravo status retired [source: iqe:satellites].",
    )
    result = ask("show all satellites", conn=StubConn(), summarize=True)
    assert result.grounded is True
    assert result.metadata["grounding"] == "llm_summary"
    assert result.metadata["confidence"] == "include"
    assert result.metadata["confidence_score"] == 1.0
    assert result.metadata["citation_report"]["valid"] is True
    assert result.metadata["attribution"] == {"iqe:satellites": 1.0}
    assert result.governance.outcomes["citation_validation"] == "pass"
    assert result.data["summarized"] is True


def test_llm_summary_source_table_tag_also_valid(monkeypatch):
    # Tags may reference the source_table form instead of the source_id.
    _stub_summary(
        monkeypatch,
        "2 rows: id 1 alpha active and id 2 bravo retired, name and status "
        "included [source: satellites].",
    )
    result = ask("show all satellites", conn=StubConn(), summarize=True)
    assert result.grounded is True
    assert result.metadata["citation_report"]["valid"] is True


def test_llm_summary_missing_citations_flagged_ungrounded(monkeypatch):
    _stub_summary(
        monkeypatch,
        "There are 2 rows: alpha is active and bravo is retired.",
    )
    result = ask("show all satellites", conn=StubConn(), summarize=True)
    assert result.grounded is False
    assert result.governance.outcomes["citation_validation"] == "fail"
    # A degrade, not a refusal: the flagged text is still returned.
    assert result.governance.blocked is False
    assert "alpha" in result.text


def test_llm_summary_stale_citation_flagged_ungrounded(monkeypatch):
    # Cites a source that is not among this result's citations.
    _stub_summary(
        monkeypatch,
        "2 rows: id 1 name alpha status active, id 2 name bravo status "
        "retired [source: iqe:decommissioned].",
    )
    result = ask("show all satellites", conn=StubConn(), summarize=True)
    assert result.grounded is False
    report = result.metadata["citation_report"]
    assert report["valid"] is False
    assert report["hallucinated_citations"] == ["iqe:decommissioned"]
    assert result.governance.outcomes["citation_validation"] == "fail"


def test_llm_summary_low_attribution_abstains(monkeypatch):
    # Valid tag but the prose shares no tokens with the row evidence.
    _stub_summary(monkeypatch, "Everything looks nominal overall [source: iqe:satellites].")
    result = ask("show all satellites", conn=StubConn(), summarize=True)
    assert result.grounded is False
    assert result.metadata["confidence"] == "abstain"
    assert result.governance.outcomes["citation_validation"] == "fail"


def test_llm_summary_mid_attribution_flag_band_stays_grounded(monkeypatch):
    # 5 of the 10 snippet tokens -> 0.5 -> "flag": grounded, HITL-flagged.
    _stub_summary(monkeypatch, "id 1 alpha active rows [source: iqe:satellites].")
    result = ask("show all satellites", conn=StubConn(), summarize=True)
    assert result.grounded is True
    assert result.metadata["confidence"] == "flag"
    assert result.metadata["confidence_score"] == 0.5
    assert result.governance.outcomes["citation_validation"] == "pass"


# ---------------------------------------------------------------------------
# NLQ path gets the identical labeling
# ---------------------------------------------------------------------------
def test_nlq_path_labels_llm_summary(monkeypatch):
    from tools.dashboard import nlq_processor as _nlq

    monkeypatch.setattr(_nlq, "extract_schema", lambda db_path=None: {})
    monkeypatch.setattr(
        _nlq, "generate_sql_via_bedrock", lambda q, s, exclude_model_ids=None: "SELECT id, name FROM satellites"
    )
    # ctx-expose-03: stub the analyst's RLS-context-threading execution seam.
    monkeypatch.setattr(
        _analyst,
        "_execute_nlq_readonly",
        lambda sql, ctx: {
            "columns": ["id", "name"],
            "rows": [{"id": 1, "name": "alpha"}],
            "row_count": 1,
            "truncated": False,
        },
    )
    monkeypatch.setattr(_nlq, "log_nlq_query", lambda *a, **k: None)
    _stub_summary(monkeypatch, "One row, no tags here at all.")

    result = ask(
        "show all satellites", mode="nlq", ctx=CortexContext(), summarize=True
    )
    assert result.provider == "nlq"
    assert result.grounded is False
    assert result.governance.outcomes["citation_validation"] == "fail"


def test_nlq_raw_rows_grounded_by_construction(monkeypatch):
    from tools.dashboard import nlq_processor as _nlq

    monkeypatch.setattr(_nlq, "extract_schema", lambda db_path=None: {})
    monkeypatch.setattr(
        _nlq, "generate_sql_via_bedrock", lambda q, s, exclude_model_ids=None: "SELECT id, name FROM satellites"
    )
    # ctx-expose-03: stub the analyst's RLS-context-threading execution seam.
    monkeypatch.setattr(
        _analyst,
        "_execute_nlq_readonly",
        lambda sql, ctx: {
            "columns": ["id", "name"],
            "rows": [{"id": 1, "name": "alpha"}],
            "row_count": 1,
            "truncated": False,
        },
    )
    monkeypatch.setattr(_nlq, "log_nlq_query", lambda *a, **k: None)

    result = ask("show all satellites", mode="nlq", ctx=CortexContext())
    assert result.grounded is True
    assert result.metadata["grounding"] == "rows_by_construction"
    assert result.citations[0].source_id == "nlq:satellites"
    assert result.citations[0].snippet == "1 row; id=1, name='alpha'"
