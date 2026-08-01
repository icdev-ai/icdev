#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for contextual retrieval (rce-ctx-01).

Fixture-driven — the LLM router is monkeypatched / injected and no live corpus
or DB is touched, so the suite runs deterministically in a fresh (empty-DB)
worktree.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag import contextual_retrieval as cr  # noqa: E402
from tools.rag.vector_store_provider import VectorChunk  # noqa: E402


# ---------------------------------------------------------------------------
# VectorChunk contract
# ---------------------------------------------------------------------------
class TestVectorChunkEmbedText:
    def test_text_for_embedding_defaults_to_content(self) -> None:
        c = VectorChunk(content="original chunk text")
        assert c.embed_text is None
        assert c.context_prefix == ""
        assert c.text_for_embedding() == "original chunk text"

    def test_text_for_embedding_uses_embed_text_when_set(self) -> None:
        c = VectorChunk(content="original", embed_text="PREFIX\n\noriginal")
        assert c.text_for_embedding() == "PREFIX\n\noriginal"

    def test_content_hash_unchanged_by_prefixing(self) -> None:
        """Hash is over original content, not embed_text — dedup stays stable."""
        a = VectorChunk(content="the same content")
        a.compute_content_hash()
        b = VectorChunk(content="the same content",
                        embed_text="A CONTEXT PREFIX\n\nthe same content",
                        context_prefix="A CONTEXT PREFIX")
        b.compute_content_hash()
        assert a.content_hash == b.content_hash


# ---------------------------------------------------------------------------
# generate_context_prefix — enablement + graceful degradation
# ---------------------------------------------------------------------------
class TestGeneratePrefix:
    def test_returns_empty_when_disabled(self) -> None:
        cfg = {"enabled": False}
        assert cr.generate_context_prefix("doc", "chunk", config=cfg) == ""

    def test_returns_empty_when_llm_unavailable_and_no_heuristic(self, monkeypatch) -> None:
        monkeypatch.setattr(cr, "_llm_prefix", lambda *a, **k: "")
        cfg = {"enabled": True, "fallback_heuristic": False}
        assert cr.generate_context_prefix("doc", "chunk", config=cfg) == ""

    def test_llm_prefix_used_when_available(self, monkeypatch) -> None:
        monkeypatch.setattr(cr, "_llm_prefix",
                            lambda doc, chunk, cfg: "This chunk covers NIST AC-2.")
        cfg = {"enabled": True}
        out = cr.generate_context_prefix("full document", "chunk about accounts", config=cfg)
        assert out == "This chunk covers NIST AC-2."

    def test_prefix_capped_to_token_budget(self, monkeypatch) -> None:
        long_prefix = "word " * 200
        monkeypatch.setattr(cr, "_llm_prefix", lambda *a, **k: long_prefix)
        cfg = {"enabled": True, "token_budget": 10}
        out = cr.generate_context_prefix("doc", "chunk", config=cfg)
        # ~10 tokens * 4 chars/token = ~40 chars cap
        assert 0 < len(out) <= 10 * 4


# ---------------------------------------------------------------------------
# Heuristic fallback (air-gap safe, no LLM)
# ---------------------------------------------------------------------------
class TestHeuristicFallback:
    def test_heuristic_used_when_llm_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(cr, "_llm_prefix", lambda *a, **k: "")
        cfg = {"enabled": True, "fallback_heuristic": True}
        out = cr.generate_context_prefix(
            "doc", "chunk", config=cfg,
            source_type="compliance_artifacts",
            metadata={"framework": "NIST 800-53", "control_family": "AC"},
        )
        assert out  # non-empty
        assert "compliance artifacts" in out
        assert "NIST 800-53" in out

    def test_heuristic_empty_without_source_or_metadata(self) -> None:
        out = cr._heuristic_prefix(source_type="", metadata={})
        assert out == ""

    def test_provenance_records_method_and_version(self, monkeypatch) -> None:
        monkeypatch.setattr(cr, "_llm_prefix", lambda *a, **k: "")
        cfg = {"enabled": True, "fallback_heuristic": True, "prompt_version": "ctx-v9"}
        prefix, prov = cr.generate_context_prefix_with_provenance(
            "doc", "chunk", config=cfg, source_type="innovation_signals",
        )
        assert prefix
        assert prov["method"] == "heuristic"
        assert prov["prompt_version"] == "ctx-v9"
        assert "generated_at" in prov

    def test_llm_provenance_method(self, monkeypatch) -> None:
        monkeypatch.setattr(cr, "_llm_prefix", lambda *a, **k: "situating sentence")
        prefix, prov = cr.generate_context_prefix_with_provenance(
            "doc", "chunk", config={"enabled": True, "function": "rag_evaluate"},
        )
        assert prov["method"] == "llm"
        assert prov["generator"] == "rag_evaluate"


# ---------------------------------------------------------------------------
# contextualize_chunk — end-to-end wiring on a VectorChunk
# ---------------------------------------------------------------------------
class TestContextualizeChunk:
    def test_noop_when_disabled(self) -> None:
        c = VectorChunk(content="body", source_type="x")
        applied = cr.contextualize_chunk(c, "document", config={"enabled": False})
        assert applied is False
        assert c.embed_text is None
        assert c.text_for_embedding() == "body"

    def test_sets_embed_text_and_metadata_when_enabled(self, monkeypatch) -> None:
        monkeypatch.setattr(cr, "_llm_prefix", lambda *a, **k: "CTX: about widgets.")
        c = VectorChunk(content="widget body text", source_type="creative_specs")
        original_hash = c.compute_content_hash()
        applied = cr.contextualize_chunk(c, "the whole document", config={"enabled": True})
        assert applied is True
        assert c.context_prefix == "CTX: about widgets."
        assert c.embed_text == "CTX: about widgets.\n\nwidget body text"
        assert c.text_for_embedding() == "CTX: about widgets.\n\nwidget body text"
        # stored content + hash unchanged
        assert c.content == "widget body text"
        assert c.compute_content_hash() == original_hash
        # provenance persisted for TRUST
        assert c.metadata["context_prefix"] == "CTX: about widgets."
        assert c.metadata["context_provenance"]["method"] == "llm"

    def test_returns_false_when_prefix_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(cr, "_llm_prefix", lambda *a, **k: "")
        c = VectorChunk(content="body", source_type="")  # no metadata → heuristic empty
        applied = cr.contextualize_chunk(
            c, "document", config={"enabled": True, "fallback_heuristic": True}
        )
        assert applied is False
        assert c.embed_text is None
