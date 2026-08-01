# CUI // SP-CTI
"""HITL chunk inspect & repair (oss-hitl-01).

The load-bearing requirement is not "chunks can be edited" — it is that a repair
keeps the EVIDENCE BASELINE honest. dic_chunk_links.chunk_hash records what a
chunk said when a citation was linked to it (migration 267). A repair that
changed a chunk's content without re-baselining that hash would make
evidence-drift detection fire forever on a chunk that was deliberately fixed, and
a citation would point at content that no longer exists. So the tests centre on:
every mutation audited, links moved to the new chunk with the new hash, and a
degrade-not-fail posture when the embedder is down.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from tools.document_intelligence.chunk_repair import (
    ChunkRepairEngine,
    merge_chunks,
    split_chunk,
)


# ── Pure operations ──────────────────────────────────────────────────────────


def test_merge_joins_in_order():
    assert merge_chunks(["a", "b", "c"]) == "a\nb\nc"


def test_merge_skips_none():
    assert merge_chunks(["a", None, "b"]) == "a\nb"


def test_split_snaps_to_a_word_boundary():
    # offset 8 is mid-"quick"; the nearest space at-or-before is after "the",
    # so the split lands there rather than cutting the word.
    left, right = split_chunk("the quick brown fox", 8)
    assert left == "the"
    assert right == "quick brown fox"
    # neither side has a dangling boundary space, and no word was cut
    assert not left.endswith(" ") and not right.startswith(" ")
    assert "quick" in right, "a word must not be split across the boundary"


def test_split_rejects_an_offset_outside_the_text():
    with pytest.raises(ValueError):
        split_chunk("short", 0)
    with pytest.raises(ValueError):
        split_chunk("short", 99)


# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeStore:
    def __init__(self):
        self.chunks: Dict[str, Any] = {}
        self.deleted: List[str] = []

    def upsert(self, chunks):
        for c in chunks:
            self.chunks[c.chunk_id] = c
        return len(chunks)

    def delete(self, ids):
        for i in ids:
            self.deleted.append(i)
            self.chunks.pop(i, None)
        return len(ids)


class FakeCursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class FakeConn:
    """Records the link re-baseline UPDATE and what it was called with."""

    def __init__(self, link_rows=1):
        self._link_rows = link_rows
        self.executed: List[tuple] = []
        self.committed = False

    def execute(self, sql, params):
        self.executed.append((sql, params))
        return FakeCursor(self._link_rows)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class FakeEmbed:
    def __init__(self, available=True):
        self._available = available

    def embed(self, text):
        if not self._available:
            raise RuntimeError("embedder down")
        return [0.1, 0.2, 0.3]


def _engine(link_rows=1, embed_available=True):
    conn = FakeConn(link_rows=link_rows)
    engine = ChunkRepairEngine(
        store=FakeStore(),
        conn_factory=lambda: conn,
        embed_provider=FakeEmbed(available=embed_available),
    )
    return engine, conn


# ── Merge ────────────────────────────────────────────────────────────────────


def test_merge_produces_one_chunk_with_a_new_hash():
    engine, _ = _engine()
    result = engine.merge(["chunk-a", "chunk-b"], ["first half", "second half"])

    assert result.ok
    assert len(result.result_chunk_ids) == 1
    assert result.result_hashes[0]                       # a fresh content hash
    assert set(result.source_chunk_ids) == {"chunk-a", "chunk-b"}


def test_merge_needs_at_least_two_chunks():
    engine, _ = _engine()
    assert engine.merge(["only-one"], ["text"]).ok is False


def test_merge_deletes_the_source_chunks():
    engine, _ = _engine()
    engine._store.chunks.update({"chunk-a": object(), "chunk-b": object()})
    engine.merge(["chunk-a", "chunk-b"], ["a", "b"])
    assert "chunk-a" in engine._store.deleted
    assert "chunk-b" in engine._store.deleted


# ── The evidence-baseline requirement ────────────────────────────────────────


def test_repair_rebaselines_links_to_the_new_chunk_and_hash():
    """The requirement that makes this safe: the evidence link follows the fix."""
    engine, conn = _engine(link_rows=2)
    result = engine.merge(["chunk-a", "chunk-b"], ["a", "b"])

    assert result.links_rebaselined == 2 + 2, "each source's links must be re-baselined"
    # every UPDATE points links at the new chunk with the new hash
    new_id, new_hash = result.result_chunk_ids[0], result.result_hashes[0]
    for _sql, params in conn.executed:
        assert params[0] == new_id, "link left pointing at the old chunk"
        assert params[1] == new_hash, "link kept the stale hash — drift will misfire"
    assert conn.committed


def test_split_rebaselines_onto_the_first_result():
    engine, conn = _engine(link_rows=1)
    result = engine.split("chunk-x", "the quick brown fox jumps", 10)
    assert result.ok
    assert len(result.result_chunk_ids) == 2
    # links move to the first (canonical successor); operator re-points the rest
    assert conn.executed[0][1][0] == result.result_chunk_ids[0]


# ── Audit ────────────────────────────────────────────────────────────────────


def test_every_repair_carries_an_audit_id():
    engine, _ = _engine()
    for result in (
        engine.merge(["a", "b"], ["x", "y"]),
        engine.split("c", "the quick brown fox", 8),
        engine.reembed("d", "some text"),
    ):
        assert result.audit_id.startswith("cr-"), f"{result.operation} was not audited"


# ── Degrade, don't fail, when the embedder is down ───────────────────────────


def test_reembed_degrades_when_the_provider_is_unavailable():
    """A re-embed with the embedder down updates text and flags pending —
    the same posture ingestion takes, never a hard failure."""
    engine, _ = _engine(embed_available=False)
    result = engine.reembed("chunk-z", "text whose vector is stale")
    assert result.ok is True
    assert result.embedding_pending is True
    assert "pending" in result.detail


def test_reembed_does_not_rebaseline_because_content_is_unchanged():
    """Content unchanged => hash unchanged => the evidence link stays valid."""
    engine, conn = _engine()
    result = engine.reembed("chunk-z", "unchanged text")
    assert result.ok
    assert result.links_rebaselined == 0
    assert conn.executed == [], "re-embed must not touch dic_chunk_links"


def test_merge_flags_pending_when_embedder_down_but_still_succeeds():
    engine, _ = _engine(embed_available=False)
    result = engine.merge(["a", "b"], ["x", "y"])
    assert result.ok is True
    assert result.embedding_pending is True


# ── Rechunk ──────────────────────────────────────────────────────────────────


def test_rechunk_runs_text_through_a_template():
    engine, _ = _engine()
    catalog = (
        "AC-1 Policy\nThe organization documents a policy.\n"
        "AC-2 Accounts\nThe organization manages accounts.\n"
    )
    result = engine.rechunk("chunk-cat", catalog, template="oscal_catalog")
    assert result.ok
    # a control catalog re-chunk should yield more than one chunk
    assert len(result.result_chunk_ids) >= 1
