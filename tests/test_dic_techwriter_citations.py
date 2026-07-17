# CUI // SP-CTI
"""Tech Writer drafted uncited AI prose.

CLAUDE.md names this surface explicitly: "Every LLM-generated artifact
(proposals, RFI, DIC, Tech Writer, and any new drafting surface) MUST carry
inline [source: …] citations validated against its evidence."

It did not. citation_grounding was used nowhere in tools/document_intelligence/;
the route jsonify'd the draft straight back; WriteGuard checks no citations. The
system prompt had always said "Cite your sources." — an instruction the model
could not follow, because the context blocks were unnumbered ("[RAG] …",
"[KG:type] …", "[WEB] url: …"). There was nothing to cite BY.

check_trust_coverage passed throughout: it asserts the grounding MODULES EXIST
and are in child_app_generator's DIRECTORY_TREE. Its docstring is honest about
that scope — but it means the gate is green with zero call-sites.
"""

import importlib

import pytest

from tools.document_intelligence import tech_writing_assist as twa

_orch = importlib.import_module("tools.document_intelligence.tech_writing_assist")


class _Chunk:
    def __init__(self, cid, did, text):
        self.chunk_id, self.doc_id, self.text, self.score = cid, did, text, 0.9


@pytest.fixture()
def rag(monkeypatch):
    """Two retrievable chunks; no KG, no web, no LLM unless a test adds one."""
    class FakeRetriever:
        def __init__(self, tenant_id="default"):
            pass

        def search(self, query, top_k=10):
            return [
                _Chunk("chunk-1", "dic_doc_1", "CORE-RTR-01 runs BGP to the edge."),
                _Chunk("chunk-2", "dic_doc_2", "The Catalyst 6500 reaches EOL in 2027."),
            ]

    monkeypatch.setattr(_orch, "RAGRetriever", FakeRetriever)
    monkeypatch.setattr(_orch, "kg_retrieve", None)
    monkeypatch.setattr(_orch, "fetch_content", None)
    monkeypatch.setattr(_orch, "is_airgap", lambda **kw: True)  # no web
    return FakeRetriever


def _draft(monkeypatch, text):
    """Force a specific draft, bypassing the LLM path entirely."""
    monkeypatch.setattr(_orch, "LLMRouter", None)
    monkeypatch.setattr(_orch, "LLMRequest", None)
    real = twa.research_and_draft

    def wrapper(**kw):
        res = real(**kw)
        res.draft_content = text
        res.error = ""
        _orch._apply_citation_report(res)
        return res
    return wrapper


class TestSourceRegister:
    def test_every_rag_chunk_becomes_a_citable_source(self, rag, monkeypatch):
        monkeypatch.setattr(_orch, "LLMRouter", None)
        res = twa.research_and_draft(query="bgp", section_heading="Overview")
        assert [s["id"] for s in res.sources] == ["1", "2"]
        assert res.sources[0]["kind"] == "rag"
        assert res.sources[0]["ref"] == "chunk-1"

    def test_ids_are_the_rag_injected_source_convention(self, rag, monkeypatch):
        """validate_citations accepts a bare int count meaning ids '1'..'N'."""
        monkeypatch.setattr(_orch, "LLMRouter", None)
        res = twa.research_and_draft(query="bgp", section_heading="Overview")
        assert all(s["id"].isdigit() for s in res.sources)


class TestCitationInstruction:
    def test_prompt_tells_the_model_the_numbers_and_the_format(self):
        instr = _orch._citation_instruction(3)
        assert "[source: 1] .. [source: 3]" in instr
        assert "ONLY those numbers" in instr

    def test_no_sources_means_no_instruction(self):
        """Do not order the model to cite when nothing was retrieved — that
        invites it to invent a citation."""
        assert _orch._citation_instruction(0) == ""


class TestValidation:
    def test_a_hallucinated_citation_is_caught(self, rag, monkeypatch):
        """THE case. The draft cites [source: 7] with two sources retrieved —
        it names evidence that was never retrieved."""
        twa.research_and_draft = _draft(
            monkeypatch, "BGP is configured [source: 1]. It is FIPS certified [source: 7].")
        try:
            res = twa.research_and_draft(query="bgp", section_heading="Overview")
        finally:
            importlib.reload(twa)
        assert res.citation_report["hallucinated_citations"] == ["7"]
        assert res.citation_report["valid"] is False
        assert any("never retrieved" in w for w in res.warnings)

    def test_a_well_cited_draft_is_clean(self, rag, monkeypatch):
        twa.research_and_draft = _draft(
            monkeypatch, "BGP runs to the edge [source: 1]. EOL is 2027 [source: 2].")
        try:
            res = twa.research_and_draft(query="bgp", section_heading="Overview")
        finally:
            importlib.reload(twa)
        assert res.citation_report["valid"] is True
        assert res.citation_report["cited_count"] == 2
        assert not [w for w in res.warnings if "never retrieved" in w or "cites none" in w]

    def test_uncited_prose_is_flagged(self, rag, monkeypatch):
        """Evidence was retrieved and the draft cites none of it — the exact
        thing the TRUST invariant exists to catch."""
        twa.research_and_draft = _draft(monkeypatch, "BGP is configured on the router.")
        try:
            res = twa.research_and_draft(query="bgp", section_heading="Overview")
        finally:
            importlib.reload(twa)
        assert any("cites none of the 2 retrieved sources" in w for w in res.warnings)

    def test_validation_never_raises_out_of_the_drafting_path(self, rag, monkeypatch):
        """The module's contract: errors surface on the result, never as an
        exception."""
        def boom(*a, **k):
            raise RuntimeError("validator exploded")
        monkeypatch.setattr(_orch, "validate_citations", boom)
        res = twa.ResearchResult(draft_content="text", sources=[{"id": "1"}])
        _orch._apply_citation_report(res)  # must not raise
        assert any("Citation validation unavailable" in w for w in res.warnings)

    def test_missing_module_degrades_quietly(self, monkeypatch):
        """Air-gap / stripped install: no citation module, no crash."""
        monkeypatch.setattr(_orch, "validate_citations", None)
        res = twa.ResearchResult(draft_content="text", sources=[{"id": "1"}])
        _orch._apply_citation_report(res)
        assert res.citation_report == {}


class TestBuildsOnTheSharedModule:
    def test_does_not_reimplement_citation_parsing(self):
        """CLAUDE.md: 'Build on the shared tools/quality/citation_grounding.py —
        do not re-implement citation parsing/validation.'"""
        from pathlib import Path
        src = Path(_orch.__file__).read_text(encoding="utf-8")
        assert "from tools.quality.citation_grounding import validate_citations" in src
        assert "re.findall(r\"\\[source" not in src, "citation parsing must not be re-implemented"
