# CUI // SP-CTI
"""Tests for the proposal + document domain triage formatters and dispatch.

The formatters are deterministic and grounded by construction — every field
derives from a real retrieved row, so no LLM is involved.
"""
from __future__ import annotations

from tools.cortex import domains
from tools.cortex.domains import document, proposal
from tools.cortex.domains._triage import confidence_tier, grounded_triage
from tools.cortex.schemas import Citation, CortexContext, CortexSearchResult


def _hit(score, sid, title="", content="body", backend="rag", table=""):
    return CortexSearchResult(
        content=content, score=score, backend=backend,
        citation=Citation(source_id=sid, title=title, source_table=table),
    )


def test_confidence_tier_bands():
    assert confidence_tier(0.9) == "high"
    assert confidence_tier(0.5) == "medium"
    assert confidence_tier(0.1) == "low"
    assert confidence_tier("bad") == "low"


def test_grounded_triage_ranks_and_covers():
    hits = [_hit(0.3, "a", table="t1"), _hit(0.9, "b", table="t2"), _hit(0.8, "c", table="t2")]
    out = grounded_triage(hits, "generic", {"title": "T"}, lambda r: ["do x"], query="q")
    assert out["domain"] == "generic"
    assert out["result_count"] == 3
    # Evidence ranked by score desc.
    assert [e["score"] for e in out["top_evidence"]] == [0.9, 0.8, 0.3]
    assert out["top_evidence"][0]["tier"] == "high"
    # Coverage counts distinct sources (t1, t2).
    assert out["coverage"]["distinct_sources"] == 2
    assert out["recommended_actions"] == ["do x"]
    assert "T for: 'q'" in out["text"]


# --------------------------------------------------------------------------- #
# Proposal
# --------------------------------------------------------------------------- #
def test_proposal_triage_shape_and_capture_framing():
    hits = [_hit(0.85, "req-1", title="Requirement 1", table="requirements"),
            _hit(0.3, "pp-1", title="Past perf", table="past_performance")]
    out = proposal.triage_summary(hits, query="win themes?")
    assert out["domain"] == "proposal"
    assert out["top_evidence"][0]["title"] == "Requirement 1"
    # Capture-manager actions: a weakly supported requirement is flagged.
    acts = " ".join(out["recommended_actions"]).lower()
    assert "win theme" in acts
    assert "weakly-supported" in acts  # the 0.3 hit
    assert "Requirement coverage" in out["text"]


def test_proposal_triage_empty():
    out = proposal.triage_summary([], query="q")
    assert out["result_count"] == 0
    assert out["top_evidence"] == []
    assert "No proposal-scoped findings." in out["text"]


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #
def test_document_triage_flags_uncited_and_lowconf():
    hits = [_hit(0.9, "doc-1", title="SOP", table="dic_documents"),
            _hit(0.2, "", content="uncited weak passage")]  # no source_id + low score
    out = document.triage_summary(hits, query="retention?")
    assert out["domain"] == "document"
    acts = " ".join(out["recommended_actions"]).lower()
    assert "lack a resolvable citation" in acts
    assert "low-confidence" in acts
    assert "classification markings" in acts


# --------------------------------------------------------------------------- #
# Dispatch via domains.summarize (config triage:true + registered formatter)
# --------------------------------------------------------------------------- #
def test_summarize_dispatches_proposal_and_document(monkeypatch):
    cfg = {"search": {"domains": {
        "proposal": {"triage": True},
        "document": {"triage": True},
        "network": {"triage": False},
    }}}
    hits = [_hit(0.8, "x", table="requirements")]
    p = domains.summarize(hits, ctx=CortexContext(domain="proposal"), query="q", config=cfg)
    assert p is not None and p["domain"] == "proposal"
    d = domains.summarize(hits, ctx=CortexContext(domain="document"), query="q", config=cfg)
    assert d is not None and d["domain"] == "document"
    # network has no formatter -> None (raw results kept).
    n = domains.summarize(hits, ctx=CortexContext(domain="network"), query="q", config=cfg)
    assert n is None


def test_shipped_config_enables_proposal_document_triage():
    # The checked-in cortex_config.yaml must set triage:true for both.
    from tools.cortex.domains import load_domain_profile
    assert load_domain_profile("proposal").triage is True
    assert load_domain_profile("document").triage is True
