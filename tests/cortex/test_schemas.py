# CUI // SP-CTI
"""Tests for the ICDEV Cortex unified schemas (ctx-core-01).

Covers construction, defaults, to_dict()/from_dict() round-trip, score
normalization, and dual-namespace importability (tools.* shim and
icdev.tools.* canonical).
"""
from tools.cortex.schemas import (
    CORTEX_BACKENDS,
    Citation,
    CortexContext,
    CortexResult,
    CortexSearchResult,
    GovernanceReport,
)


# ---------------------------------------------------------------------------
# Namespace / import contract
# ---------------------------------------------------------------------------
def test_import_via_canonical_namespace():
    from icdev.tools.cortex.schemas import (  # noqa: F401
        Citation,
        CortexContext,
        CortexResult,
        CortexSearchResult,
        GovernanceReport,
    )


def test_import_via_package_init():
    from tools.cortex import CortexResult as FromInit  # noqa: F401
    from icdev.tools.cortex import CortexResult as FromCanonicalInit  # noqa: F401


def test_backend_constant():
    assert CORTEX_BACKENDS == ("rag", "graph", "dic", "kb", "sme")


# ---------------------------------------------------------------------------
# Citation
# ---------------------------------------------------------------------------
def test_citation_defaults():
    c = Citation()
    assert c.classification == "CUI"
    assert c.source_id == ""
    assert c.clearance_required == ""
    assert c.provenance_id == ""


def test_citation_round_trip():
    c = Citation(
        source_id="doc-42",
        source_type="dic_document",
        source_table="dic_documents",
        title="Systems Spec",
        snippet="The system shall...",
        url="/document-intelligence/doc/doc-42",
        classification="SECRET",
        clearance_required="SECRET",
        provenance_id="prov-9",
    )
    assert Citation.from_dict(c.to_dict()) == c


def test_citation_from_dict_ignores_unknown_keys():
    c = Citation.from_dict({"source_id": "x", "not_a_field": 1})
    assert c.source_id == "x"


# ---------------------------------------------------------------------------
# CortexSearchResult
# ---------------------------------------------------------------------------
def test_search_result_defaults():
    r = CortexSearchResult()
    assert r.score == 0.0
    assert isinstance(r.citation, Citation)
    assert r.raw_scores == {} and r.metadata == {}


def test_search_result_score_clamped_to_unit_interval():
    assert CortexSearchResult(score=3.7).score == 1.0
    assert CortexSearchResult(score=-0.2).score == 0.0
    assert CortexSearchResult(score=0.5).score == 0.5


def test_search_result_round_trip_with_nested_citation():
    r = CortexSearchResult(
        content="matched chunk text",
        score=0.83,
        backend="rag",
        strategy="hybrid",
        citation=Citation(source_id="chunk-1", source_type="rag_chunk"),
        raw_scores={"bm25": 12.4, "vector": 0.71},
        metadata={"collection": "specs"},
    )
    restored = CortexSearchResult.from_dict(r.to_dict())
    assert restored == r
    assert isinstance(restored.citation, Citation)


# ---------------------------------------------------------------------------
# GovernanceReport
# ---------------------------------------------------------------------------
def test_governance_report_defaults():
    g = GovernanceReport()
    assert g.gates_run == []
    assert g.outcomes == {}
    assert g.redactions_applied == 0
    assert g.blocked is False


def test_governance_report_round_trip():
    g = GovernanceReport(
        gates_run=["citation_guard", "redaction"],
        outcomes={"citation_guard": "pass", "redaction": "warn"},
        redactions_applied=2,
        blocked=False,
    )
    assert GovernanceReport.from_dict(g.to_dict()) == g


# ---------------------------------------------------------------------------
# CortexResult
# ---------------------------------------------------------------------------
def test_cortex_result_defaults():
    res = CortexResult()
    assert res.grounded is False
    assert res.citations == []
    assert isinstance(res.governance, GovernanceReport)
    assert res.cost == 0.0 and res.latency_ms == 0


def test_cortex_result_round_trip_with_nested_objects():
    res = CortexResult(
        text="Grounded answer [source: doc-42]",
        citations=[Citation(source_id="doc-42"), Citation(source_id="kg-7")],
        governance=GovernanceReport(gates_run=["citation_guard"], outcomes={"citation_guard": "pass"}),
        provider="ollama",
        model="llama3",
        cost=0.0031,
        latency_ms=412,
        grounded=True,
    )
    restored = CortexResult.from_dict(res.to_dict())
    assert restored == res
    assert all(isinstance(c, Citation) for c in restored.citations)
    assert isinstance(restored.governance, GovernanceReport)


# ---------------------------------------------------------------------------
# CortexContext
# ---------------------------------------------------------------------------
def test_context_rls_ready_defaults():
    ctx = CortexContext()
    assert ctx.tenant_id == ""
    assert ctx.classification == "CUI"
    assert ctx.air_gap is False
    # Tri-state: None means "defer to platform policy" (resolve_fail_closed),
    # distinct from an explicit False. Not a plain bool anymore.
    assert ctx.fail_closed is None


def test_context_round_trip():
    ctx = CortexContext(
        tenant_id="tenant-a",
        user_id="u-1",
        classification="SECRET",
        domain="proposals",
        air_gap=True,
        fail_closed=True,
    )
    assert CortexContext.from_dict(ctx.to_dict()) == ctx


# ---------------------------------------------------------------------------
# Mirror parity
# ---------------------------------------------------------------------------
def test_shim_and_canonical_schemas_are_identical_source():
    import pathlib

    import icdev.tools.cortex.schemas as canonical

    canonical_src = pathlib.Path(canonical.__file__).read_text(encoding="utf-8")
    repo_root = pathlib.Path(canonical.__file__).resolve().parents[3]
    shim_src = (repo_root / "tools" / "cortex" / "schemas.py").read_text(encoding="utf-8")
    assert canonical_src == shim_src
