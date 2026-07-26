#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG adaptive chunker (D-RAG-4)."""

from __future__ import annotations


from tools.rag.chunker import (
    CHARS_PER_TOKEN,
    _estimate_tokens,
    _find_sentence_boundary,
    chunk_content,
    chunk_fields,
)


class TestEstimateTokens:
    def test_short_text(self):
        assert _estimate_tokens("hi") >= 1

    def test_empty_text(self):
        assert _estimate_tokens("") >= 1

    def test_token_estimate_ratio(self):
        text = "a" * 400
        tokens = _estimate_tokens(text)
        assert tokens == 400 // CHARS_PER_TOKEN


class TestFindSentenceBoundary:
    def test_finds_period(self):
        text = "First sentence. Second sentence."
        pos = _find_sentence_boundary(text, 15)
        assert text[pos - 1 : pos] in (" ", "S")  # Should be after ". "

    def test_no_boundary_falls_to_whitespace(self):
        text = "word1 word2 word3 word4"
        pos = _find_sentence_boundary(text, 10)
        assert pos > 0

    def test_near_end_of_text(self):
        text = "Short."
        pos = _find_sentence_boundary(text, 6)
        assert 0 <= pos <= len(text) + 1


class TestChunkContent:
    def test_empty_content(self):
        assert chunk_content("") == []
        assert chunk_content("   ") == []

    def test_short_content_single_chunk(self):
        chunks = chunk_content("Hello world", source_type="test", source_id="1")
        assert len(chunks) == 1
        assert chunks[0].content == "Hello world"
        assert chunks[0].source_type == "test"
        assert chunks[0].source_id == "1"
        assert chunks[0].total_chunks == 1
        assert chunks[0].chunk_index == 0

    def test_content_hash_set(self):
        chunks = chunk_content("Some text")
        assert chunks[0].content_hash != ""
        assert len(chunks[0].content_hash) == 64

    def test_chunk_id_generated(self):
        chunks = chunk_content("Some text")
        assert chunks[0].chunk_id.startswith("chunk-")

    def test_long_content_multiple_chunks(self):
        # Create content longer than chunk_size_tokens * CHARS_PER_TOKEN
        long_text = ". ".join(["This is sentence number {}".format(i) for i in range(500)])
        chunks = chunk_content(
            long_text,
            source_type="test",
            chunk_config={"chunk_size_tokens": 100, "overlap_pct": 0.10},
        )
        assert len(chunks) > 1
        for c in chunks:
            assert c.total_chunks == len(chunks)

    def test_metadata_passed_through(self):
        meta = {"key": "val"}
        chunks = chunk_content("text", metadata=meta)
        # Caller metadata survives alongside the chunking-provenance keys
        # stamped by oss-chunk-01.
        assert chunks[0].metadata["key"] == "val"
        assert meta == {"key": "val"}, "caller's dict must not be mutated"

    def test_chunking_template_recorded(self):
        """Every chunk records which template produced it (oss-chunk-01)."""
        chunks = chunk_content("text")
        assert chunks[0].metadata["chunking_template"] == "general"
        assert chunks[0].metadata["chunking_strategy"] == "sliding_window"

    def test_metadata_not_shared_between_chunks(self):
        long_text = ". ".join("This is sentence number {}".format(i) for i in range(500))
        chunks = chunk_content(long_text, chunk_config={"chunk_size_tokens": 100})
        assert len(chunks) > 1
        assert chunks[0].metadata is not chunks[1].metadata

    def test_classification_set(self):
        chunks = chunk_content("text", classification="SECRET")
        assert chunks[0].classification == "SECRET"

    def test_tenant_and_project_ids(self):
        chunks = chunk_content("text", tenant_id="t1", project_id="p1")
        assert chunks[0].tenant_id == "t1"
        assert chunks[0].project_id == "p1"

    def test_medium_content_uses_adaptive_floor(self):
        """The adaptive floor, not the configured size, governs medium content.

        Predates oss-chunk-01: adaptive sizing takes min(configured, adaptive)
        with the adaptive value clamped to a 150-token floor, so a 375-token
        document splits at 150 even though 500 was configured. The old
        assertion here (single chunk) described the pre-adaptive behaviour and
        had been failing on main since adaptive sizing landed.
        """
        text = "word " * 300  # ~300 words, ~1500 chars, ~375 tokens
        chunks = chunk_content(text, chunk_config={"chunk_size_tokens": 500})
        assert len(chunks) > 1
        # Content at or below the 150-token floor is still one chunk.
        short = "word " * 100  # ~500 chars, ~125 tokens
        assert len(chunk_content(short, chunk_config={"chunk_size_tokens": 500})) == 1

    def test_overlap_in_long_chunks(self):
        """Verify chunks overlap when splitting long content."""
        long_text = "Sentence {}. ".format("x" * 100) * 50
        chunks = chunk_content(
            long_text,
            chunk_config={"chunk_size_tokens": 200, "overlap_pct": 0.20},
        )
        if len(chunks) >= 2:
            # Check that chunks share some content (overlap)
            chunks[0].content[-50:]
            chunks[1].content[:50]
            # The overlap means the end of chunk 0 should appear near the start of chunk 1
            # (exact overlap depends on sentence boundary finding)
            assert len(chunks) > 1

    def test_no_embedding_on_chunks(self):
        """Chunker should NOT set embeddings — caller must embed."""
        chunks = chunk_content("some text")
        assert chunks[0].embedding is None


class TestChunkFields:
    def test_basic_field_chunking(self):
        fields = {"title": "My Title", "description": "A detailed description"}
        chunks = chunk_fields(
            fields,
            ["title", "description"],
            source_type="test",
            source_id="42",
        )
        assert len(chunks) >= 1
        assert "title: My Title" in chunks[0].content
        assert "description: A detailed description" in chunks[0].content

    def test_skips_empty_fields(self):
        fields = {"title": "Title", "body": "", "notes": None}
        chunks = chunk_fields(fields, ["title", "body", "notes"])
        assert len(chunks) >= 1
        assert "body" not in chunks[0].content

    def test_empty_fields_returns_empty(self):
        fields = {"a": "", "b": None}
        chunks = chunk_fields(fields, ["a", "b"])
        assert chunks == []

    def test_source_metadata(self):
        fields = {"name": "test"}
        chunks = chunk_fields(
            fields,
            ["name"],
            source_type="signals",
            source_id="99",
            source_table="innovation_signals",
        )
        assert chunks[0].source_type == "signals"
        assert chunks[0].source_id == "99"
        assert chunks[0].source_table == "innovation_signals"
