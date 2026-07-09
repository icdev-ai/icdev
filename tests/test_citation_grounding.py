"""Tests for tools/quality/citation_grounding.py — shared, surface-agnostic
citation & provenance utilities (extracted from the DIC document generator)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.quality.citation_grounding import (
    ArtifactProvenance,
    Provenance,
    build_artifact_provenance,
    citation_gate,
    classify_confidence,
    compute_attribution_score,
    has_citations,
    parse_citations,
    validate_citations,
)


class TestParseCitations:
    def test_dic_chunk_style(self):
        assert parse_citations("A claim [source: chunk 12ab].") == ["12ab"]

    def test_bare_source_style(self):
        assert parse_citations("[source: KB-42] backs this.") == ["KB-42"]

    def test_rag_source_n_style(self):
        assert parse_citations("Per [SOURCE-3] and [SOURCE-1].") == ["3", "1"]

    def test_multiple_ids_in_one_tag(self):
        assert parse_citations("[source: chunk a, chunk b]") == ["a", "b"]

    def test_dedupes_preserving_order(self):
        assert parse_citations("[source: x] ... [source: x] ... [source: y]") == ["x", "y"]

    def test_case_insensitive_marker(self):
        assert parse_citations("[SOURCE: chunk Q9]") == ["Q9"]

    def test_empty_and_uncited(self):
        assert parse_citations("") == []
        assert parse_citations("No citations here.") == []

    def test_has_citations(self):
        assert has_citations("[source: 1]") is True
        assert has_citations("plain") is False


class TestValidateCitations:
    def test_int_count_available(self):
        r = validate_citations("[SOURCE-1] [SOURCE-2]", 3)
        assert r["available_count"] == 3
        assert r["cited_count"] == 2
        assert r["uncited_sources"] == ["3"]
        assert r["valid"] is True

    def test_hallucinated_citation_flagged(self):
        r = validate_citations("[SOURCE-5]", 2)
        assert r["hallucinated_citations"] == ["5"]
        assert r["valid"] is False

    def test_iterable_allowed_sources(self):
        r = validate_citations("[source: chunk kb1] [source: chunk kb9]", ["kb1", "kb2"])
        assert r["hallucinated_citations"] == ["kb9"]
        assert r["cited_count"] == 1
        assert r["valid"] is False

    def test_citation_rate(self):
        r = validate_citations("[source: a]", ["a", "b", "c", "d"])
        assert r["citation_rate"] == 0.25

    def test_bool_is_not_treated_as_int(self):
        # True is an int subclass; must not become an available-source count.
        r = validate_citations("[SOURCE-1]", True)
        assert r["available_count"] == 0
        assert r["hallucinated_citations"] == ["1"]


class TestCitationGate:
    def test_missing_citations_flagged(self):
        findings = citation_gate([{"item_number": "1.1", "content": "Uncited claim."}])
        assert findings == [{"item_number": "1.1", "issue": "missing_citations", "detail": []}]

    def test_cited_section_passes(self):
        findings = citation_gate([{"item_number": "1.1", "content": "Claim [source: chunk a]."}])
        assert findings == []

    def test_abstained_section_skipped(self):
        findings = citation_gate([{"item_number": "1.1", "content": "", "abstained": True}])
        assert findings == []

    def test_empty_content_skipped(self):
        assert citation_gate([{"item_number": "1.1", "content": ""}]) == []

    def test_hallucinated_citation_flagged(self):
        sections = [{"item_number": "2.1", "content": "[source: chunk zzz]", "allowed_sources": ["a", "b"]}]
        findings = citation_gate(sections)
        assert any(f["issue"] == "hallucinated_citation" and f["detail"] == ["zzz"] for f in findings)

    def test_require_citations_false_allows_uncited(self):
        findings = citation_gate([{"item_number": "1.1", "content": "Uncited."}], require_citations=False)
        assert findings == []

    def test_prefers_content_over_ai_draft(self):
        sections = [{"item_number": "1.1", "content": "Cited [source: a].", "ai_draft": "leftover uncited"}]
        assert citation_gate(sections) == []

    def test_falls_back_to_title_label(self):
        findings = citation_gate([{"title": "Overview", "content": "Uncited."}])
        assert findings[0]["item_number"] == "Overview"


class TestClassifyConfidence:
    def test_bands(self):
        assert classify_confidence(0.9) == "include"
        assert classify_confidence(0.7) == "include"
        assert classify_confidence(0.5) == "flag"
        assert classify_confidence(0.4) == "flag"
        assert classify_confidence(0.2) == "abstain"


class TestAttributionScore:
    def test_full_overlap(self):
        assert compute_attribution_score("alpha beta", "alpha beta gamma") == 1.0

    def test_partial_overlap(self):
        assert compute_attribution_score("alpha beta", "alpha only") == 0.5

    def test_empty_inputs(self):
        assert compute_attribution_score("", "x") == 0.0
        assert compute_attribution_score("x", "") == 0.0


class TestProvenance:
    def test_from_ledger_row_native_keys(self):
        p = Provenance.from_ledger_row({
            "source_id": "s1", "sha256": "abc", "classification": "CUI",
            "version_ref": "v1", "ingest_timestamp": "2026-07-08T00:00:00Z",
            "attribution_score": 0.5,
        })
        assert p.source_id == "s1"
        assert p.attribution_score == 0.5

    def test_from_ledger_row_adapter_keys(self):
        # Keys as emitted by dic/provenance_adapter.get_chunk_provenance.
        p = Provenance.from_ledger_row({
            "chunk_uuid": "c9", "sha256_hash": "def", "classification_label": "SECRET",
            "version_tree_ref": "vt2", "ingest_timestamp": "t",
        })
        assert p.source_id == "c9"
        assert p.sha256 == "def"
        assert p.classification == "SECRET"
        assert p.version_ref == "vt2"

    def test_to_dict_roundtrip(self):
        bundle = ArtifactProvenance(
            artifact_id="draft-1", generation_model="claude", method="cod",
            sources=[Provenance(source_id="s1", sha256="h")],
        )
        d = bundle.to_dict()
        assert d["artifact_id"] == "draft-1"
        assert d["sources"][0]["source_id"] == "s1"


class TestBuildArtifactProvenance:
    def test_normalizes_mixed_sources(self):
        bundle = build_artifact_provenance(
            "art-1",
            [
                "kb1",                                              # bare id
                {"chunk_uuid": "c9", "sha256_hash": "d"},          # ledger row
                Provenance(source_id="p3", classification="SECRET"),  # instance
            ],
            generation_model="claude", method="cod",
        )
        assert bundle.artifact_id == "art-1"
        assert [s.source_id for s in bundle.sources] == ["kb1", "c9", "p3"]
        assert bundle.sources[1].sha256 == "d"
        assert bundle.sources[2].classification == "SECRET"

    def test_empty_sources(self):
        bundle = build_artifact_provenance("art-2", None, method="template")
        assert bundle.sources == []
        assert bundle.method == "template"
