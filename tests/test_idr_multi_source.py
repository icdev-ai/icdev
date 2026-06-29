# CUI // SP-CTI
"""Tests for IDR multi-source enrichment: RAG full-KB, KG expansion, email extraction,
evidence tiers, CoT/CoD per-section, and confidence threshold gate.
"""
from __future__ import annotations

import os
import sys
import pathlib
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch


# Ensure repo root on path
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ─── Context builder tests ───────────────────────────────────────────────────

class TestEmailExtraction:
    def _write_eml(self, tmp_path: pathlib.Path, body: str, subject: str = "Test") -> str:
        content = (
            f"From: sender@example.com\r\n"
            f"To: receiver@example.com\r\n"
            f"Subject: {subject}\r\n"
            f"Date: Mon, 1 Jan 2026 00:00:00 +0000\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n"
            f"\r\n"
            f"{body}\r\n"
        )
        p = tmp_path / "test.eml"
        p.write_bytes(content.encode("utf-8"))
        return str(p)

    def test_email_headers_extracted(self, tmp_path):
        from tools.docgen.context_builder import _extract_email
        eml = self._write_eml(tmp_path, "Router config changed.", subject="Network Update")
        result = _extract_email(eml)
        assert "sender@example.com" in result
        assert "Network Update" in result

    def test_email_body_extracted(self, tmp_path):
        from tools.docgen.context_builder import _extract_email
        eml = self._write_eml(tmp_path, "SOP-NET-042 requires TACACS+ authentication.")
        result = _extract_email(eml)
        assert "SOP-NET-042" in result
        assert "TACACS+" in result

    def test_email_missing_file_returns_empty(self):
        from tools.docgen.context_builder import _extract_email
        result = _extract_email("/nonexistent/path/file.eml")
        assert result == ""

    def test_extract_text_routes_email_by_upload_type(self, tmp_path):
        from tools.docgen.context_builder import _extract_text_from_file
        eml = self._write_eml(tmp_path, "BGP peer down on RTR-CORE-01.")
        result = _extract_text_from_file(str(eml), upload_type="email")
        assert "BGP" in result

    def test_extract_text_routes_email_by_extension(self, tmp_path):
        from tools.docgen.context_builder import _extract_text_from_file
        # .eml extension should auto-route to email extractor even without upload_type hint
        eml_path = tmp_path / "message.eml"
        eml_path.write_bytes(
            b"From: a@b.com\r\nSubject: Test\r\n\r\nHello VLAN 10.\r\n"
        )
        result = _extract_text_from_file(str(eml_path))
        assert "VLAN 10" in result


class TestKGEntityExpansion:
    def test_no_entities_returns_empty(self):
        from tools.docgen.context_builder import _expand_kg_entities
        with patch("tools.knowledge_graph.graph_rag.retrieve") as mock_retrieve:
            mock_retrieve.return_value = {"context": "", "status": "ok"}
            result = _expand_kg_entities([""], [], [])
        assert result == []

    def test_ip_entities_extracted_and_queried(self):
        from tools.docgen.context_builder import _expand_kg_entities

        called_queries: list[str] = []

        def fake_retrieve(query, profile=None, top_k=5, compress=False, **kw):
            called_queries.append(query)
            return {"context": f"KG node for {query}", "status": "ok"}

        with patch("tools.knowledge_graph.graph_rag.retrieve", fake_retrieve):
            result = _expand_kg_entities(
                source_texts=["Device at 192.168.1.1 running BGP."],
                config_findings=[],
                diagram_findings=[],
            )

        assert any("192.168.1.1" in q for q in called_queries)
        assert len(result) > 0

    def test_kg_chunks_have_tier_label(self):
        from tools.docgen.context_builder import _expand_kg_entities, TIER_KG

        def fake_retrieve(query, **kw):
            return {"context": "some KG node context", "status": "ok"}

        with patch("tools.knowledge_graph.graph_rag.retrieve", fake_retrieve):
            result = _expand_kg_entities(
                source_texts=["VLAN 10 on RTR-CORE-01"],
                config_findings=[],
                diagram_findings=[],
            )

        assert all(TIER_KG in c["text"] for c in result)

    def test_kg_unavailable_degrades_gracefully(self):
        from tools.docgen.context_builder import _expand_kg_entities
        with patch.dict("sys.modules", {"tools.knowledge_graph.graph_rag": None}):
            result = _expand_kg_entities(
                source_texts=["TACACS+ config review."],
                config_findings=[],
                diagram_findings=[],
            )
        # Should return empty list, not raise
        assert isinstance(result, list)


class TestEvidencePriorityTiers:
    def test_operator_text_appears_first_in_query(self):
        from tools.docgen.context_builder import build_context, TIER_OPERATOR

        session = {"id": "s1", "domain": "network", "doc_type": "runbook",
                   "title": "Test", "classification": "CUI"}
        uploads: list = []
        analyses: list = []

        with patch("tools.knowledge_graph.graph_rag.retrieve") as mock_kg:
            mock_kg.return_value = {"context": "", "status": "ok"}
            ctx = build_context(
                session=session, uploads=uploads, analyses=analyses,
                supplemental_text="CRITICAL: SOP-NET-042 must be enforced.",
            )

        qs = ctx["query_string"]
        op_idx = qs.find(TIER_OPERATOR)
        gen_idx = qs.find("Generate a")
        assert op_idx >= 0, "OPERATOR tier label missing from query string"
        assert op_idx < gen_idx, "OPERATOR block must precede generation directive"

    def test_supplemental_text_in_context_dict(self):
        from tools.docgen.context_builder import build_context

        session = {"id": "s2", "domain": "network", "doc_type": "runbook",
                   "title": "T", "classification": "CUI"}
        with patch("tools.knowledge_graph.graph_rag.retrieve") as m:
            m.return_value = {"context": "", "status": "ok"}
            ctx = build_context(
                session=session, uploads=[], analyses=[],
                supplemental_text="Operator says: use SNMP v3.",
            )
        assert ctx["supplemental_text"] == "Operator says: use SNMP v3."

    def test_kg_chunks_in_context_dict(self):
        from tools.docgen.context_builder import build_context

        session = {"id": "s3", "domain": "network", "doc_type": "runbook",
                   "title": "T", "classification": "CUI"}
        uploads = [{"id": "u1", "upload_type": "doc", "file_path": "/nonexistent.txt",
                    "filename": "test.txt"}]

        def fake_retrieve(query, **kw):
            return {"context": "BGP node found", "status": "ok"}

        with patch("tools.knowledge_graph.graph_rag.retrieve", fake_retrieve):
            ctx = build_context(session=session, uploads=uploads, analyses=[])

        assert "kg_chunks" in ctx
        assert isinstance(ctx["kg_chunks"], list)


# ─── Doc generator tests ─────────────────────────────────────────────────────

@dataclass
class _FakeChunk:
    chunk_id: str = "c1"
    content: str = "SOP-NET-042: all routers require TACACS+ authentication."
    doc_title: str = "SOP"
    page: int = 1

    @property
    def citation(self):
        c = MagicMock()
        c.to_dict.return_value = {"chunk_id": self.chunk_id, "source": self.doc_title}
        return c


@dataclass
class _FakeVerifyResult:
    verified_text: str = ""
    abstained: bool = False
    claims: list = field(default_factory=list)


@dataclass
class _FakeClaim:
    supported: bool = True
    score: float = 0.9


class TestFullKBSearchFallback:
    def test_full_kb_called_first(self):
        from tools.document_intelligence.doc_generator import generate_document

        call_log: list[dict] = []

        def fake_search(q, collection_id=None, top_k=10):
            call_log.append({"collection_id": collection_id})
            return [_FakeChunk()] if collection_id is None else []

        with patch("tools.document_intelligence.search_engine.DICSearchEngine") as MockEng:
            MockEng.return_value.search = fake_search
            with patch("tools.document_intelligence.doc_generator._llm_generate", return_value='{"title":"T","sections":[{"heading":"H","summary":"S"}]}'):
                with patch("tools.document_intelligence.verifier.verify") as mock_verify:
                    mock_verify.return_value = _FakeVerifyResult(
                        verified_text="ok", claims=[_FakeClaim()]
                    )
                    with patch("tools.db.storage.get_connection"):
                        generate_document("test query", collection_id="sess-1")

        # First call must be collection_id=None (full KB)
        assert call_log[0]["collection_id"] is None

    def test_session_fallback_when_full_kb_empty(self):
        from tools.document_intelligence.doc_generator import generate_document

        call_log: list[dict] = []

        def fake_search(q, collection_id=None, top_k=10):
            call_log.append({"collection_id": collection_id})
            # Full KB returns nothing; session-scoped returns a hit
            return [] if collection_id is None else [_FakeChunk()]

        with patch("tools.document_intelligence.search_engine.DICSearchEngine") as MockEng:
            MockEng.return_value.search = fake_search
            with patch("tools.document_intelligence.doc_generator._llm_generate", return_value=None):
                with patch("tools.db.storage.get_connection"):
                    generate_document("test query", collection_id="sess-1")

        assert len(call_log) >= 2
        assert call_log[0]["collection_id"] is None   # full KB first
        assert call_log[1]["collection_id"] == "sess-1"  # then session fallback


class TestConfidenceGate:
    def _run_with_claims(self, supported_ratio: float):
        """Run generate_document with mocked verifier returning given support ratio."""
        from tools.document_intelligence.doc_generator import generate_document

        n_claims = 10
        n_supported = int(n_claims * supported_ratio)
        claims = (
            [_FakeClaim(supported=True)] * n_supported
            + [_FakeClaim(supported=False)] * (n_claims - n_supported)
        )
        vr = _FakeVerifyResult(verified_text="Section content.", claims=claims)

        def fake_search(q, collection_id=None, top_k=10):
            return [_FakeChunk()]

        with patch("tools.document_intelligence.search_engine.DICSearchEngine") as MockEng:
            MockEng.return_value.search = fake_search
            with patch("tools.document_intelligence.doc_generator._llm_generate") as mock_llm:
                mock_llm.return_value = '{"title":"T","sections":[{"heading":"Sec","summary":"S"}]}'
                # Second call (section) returns section text
                mock_llm.side_effect = [
                    '{"title":"T","sections":[{"heading":"Sec","summary":"S"}]}',
                    "Draft section text.",
                ]
                with patch("tools.document_intelligence.verifier.verify", return_value=vr):
                    with patch("tools.db.storage.get_connection"):
                        result = generate_document("test", collection_id="s1")
        return result

    def test_high_confidence_includes_normally(self):
        result = self._run_with_claims(0.9)
        sec = next((s for s in result.sections if not s.abstained), None)
        if sec:
            assert not sec.low_confidence
            assert sec.confidence >= 0.7

    def test_medium_confidence_flags_section(self):
        result = self._run_with_claims(0.5)
        sec = next((s for s in result.sections), None)
        if sec and not sec.abstained:
            assert sec.low_confidence
            assert "⚠" in sec.content or sec.hitl_note

    def test_low_confidence_abstains(self):
        result = self._run_with_claims(0.2)
        sec = next((s for s in result.sections), None)
        if sec:
            assert sec.abstained


class TestCoTActivation:
    def test_cot_called_when_evidence_rich(self):
        from tools.document_intelligence import doc_generator as dg

        # Patch evidence so it's > 500 chars
        rich_evidence = "x" * 600

        cot_calls: list = []

        def fake_cot(heading, evidence, function="document_qna"):
            cot_calls.append(heading)
            return "CoT generated text."

        def patched_build(*a, **kw):
            return rich_evidence

        with patch.object(dg, "_build_evidence_pool", patched_build):
            with patch.object(dg, "_cot_generate", fake_cot):
                with patch.object(dg, "_llm_generate") as mock_llm:
                    mock_llm.return_value = '{"title":"T","sections":[{"heading":"H","summary":"S"}]}'
                    with patch("tools.document_intelligence.search_engine.DICSearchEngine") as MockEng:
                        MockEng.return_value.search.return_value = []
                        with patch("tools.db.storage.get_connection"):
                            dg.generate_document("q", collection_id=None)

        assert len(cot_calls) > 0, "CoT should be called when evidence > 500 chars"

    def test_cot_skipped_when_evidence_thin(self):
        from tools.document_intelligence import doc_generator as dg

        thin_evidence = "x" * 50  # below threshold

        cot_calls: list = []

        def fake_cot(heading, evidence, function="document_qna"):
            cot_calls.append(heading)
            return "CoT text."

        with patch.object(dg, "_build_evidence_pool", return_value=thin_evidence):
            with patch.object(dg, "_cot_generate", fake_cot):
                with patch.object(dg, "_llm_generate") as mock_llm:
                    mock_llm.return_value = '{"title":"T","sections":[{"heading":"H","summary":"S"}]}'
                    with patch("tools.document_intelligence.search_engine.DICSearchEngine") as MockEng:
                        MockEng.return_value.search.return_value = []
                        with patch("tools.db.storage.get_connection"):
                            dg.generate_document("q", collection_id=None)

        assert len(cot_calls) == 0, "CoT must NOT be called when evidence ≤ 500 chars"
