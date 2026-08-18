# CUI // SP-CTI
"""Tests for the ``document_intelligence`` domain lens (cef-bck-04).

The lens is configuration — a ``search.domains.document_intelligence`` block in
args/cortex_config.yaml — over machinery that already existed. These tests pin
the four acceptance behaviors:

1. The block exists and every field the card names is populated
   (backends, collections, sources, intents, persona).
2. A DI-scoped query DROPS out-of-scope rows and reports the drop count in
   ``metadata.router.domain_scope.filtered_out``.
3. A query outside the lens is unaffected — no scoping, no persona.
4. It is the DIC corpus specifically: ``dic_documents`` chunks retrieved out of
   the SHARED ``rag_chunks`` table survive while ``rag_compliance_corpus``
   chunks from the same table do not. That is the row-level distinction the
   lens exists for, and the reason it is a separate key from ``document``.

The lens config is read from the SHIPPED args/cortex_config.yaml on purpose —
these assertions are about that file's contents, so a self-contained dict would
pass with the block absent. Backend adapters are monkeypatched via
``search_service.BACKEND_ADAPTERS``.
"""
from __future__ import annotations

import pytest

from tools.cortex import domains, search_service
from tools.cortex.config import load_cortex_config
from tools.cortex.constants import CORTEX_DOMAIN_KEYS
from tools.cortex.domains import apply_persona, load_domain_profile
from tools.cortex.schemas import Citation, CortexContext, CortexSearchResult

DI_DOMAIN = "document_intelligence"


@pytest.fixture(scope="module")
def cfg():
    return load_cortex_config()


_SEQ = iter(range(1, 10_000))


def _hit(backend, source_table, cite_type, score=0.5, **meta):
    """A search hit shaped like the real adapters' output.

    ``cite_type`` is the ``Citation.source_type`` field, which the rag adapter
    sets to the CONSTANT ``"rag_chunk"`` for every row and the dic adapter sets
    to ``"dic_document"``. The per-row discriminator for a rag hit lives in
    ``metadata["source_type"]`` instead, so both are modelled separately here —
    collapsing them would hide the fact that a rag hit's citation type can never
    carry the scope signal.

    Each hit gets a distinct ``source_id``: RRF fusion keys on it, so two hits
    sharing one id are deduplicated into a single result.
    """
    return CortexSearchResult(
        content=f"{backend} hit from {source_table}",
        score=score,
        backend=backend,
        strategy="native",
        citation=Citation(
            source_id=f"{source_table}-{next(_SEQ)}",
            source_type=cite_type,
            source_table=source_table,
            title=source_table,
        ),
        metadata=dict(meta),
    )


# --------------------------------------------------------------------------- #
# 1. The lens exists and is fully populated
# --------------------------------------------------------------------------- #
def test_lens_block_exists_and_is_populated(cfg):
    block = (cfg.get("search") or {}).get("domains", {}).get(DI_DOMAIN)
    assert block is not None, "search.domains.document_intelligence is missing"
    for field in ("backends", "collections", "sources", "intents", "persona"):
        assert block.get(field), f"{field} is empty — the card requires it populated"


def test_profile_loads_with_dic_scope_and_persona(cfg):
    profile = load_domain_profile(DI_DOMAIN, cfg)
    assert profile is not None
    assert profile.sources == ["dic_"]
    assert profile.backends == ["rag", "dic"]
    assert "dic_documents" in profile.collections
    assert "Document Intelligence corpus" in profile.persona
    assert profile.triage is True


def test_backend_scope_admits_no_backend_the_row_scope_would_empty(cfg):
    """graph/kb hits carry kg_nodes / knowledge_patterns source tables, which no
    ``dic_`` prefix can match — declaring them would guarantee a 100% row drop."""
    profile = load_domain_profile(DI_DOMAIN, cfg)
    assert "graph" not in profile.backends
    assert "kb" not in profile.backends


def test_lens_is_reachable_from_the_canvas():
    """A lens absent from CORTEX_DOMAIN_KEYS is rejected by /cortex/api/chat, so
    the config block alone would be declared-but-unconsumable."""
    assert DI_DOMAIN in CORTEX_DOMAIN_KEYS


def test_triage_formatter_is_registered_and_reports_its_own_domain():
    """``triage: true`` with no registered formatter silently returns None."""
    hits = [_hit("dic", "dic_documents", "dic_document", score=0.8)]
    summary = domains.summarize(hits, ctx=CortexContext(domain=DI_DOMAIN), query="q")
    assert summary is not None
    assert summary["domain"] == DI_DOMAIN


# --------------------------------------------------------------------------- #
# 2 + 4. Row-level scope over the SHARED rag_chunks table
# --------------------------------------------------------------------------- #
def test_di_query_drops_out_of_scope_rows_and_reports_filtered_out(monkeypatch, cfg):
    def rag(query, top_k=5, ctx=None):
        # Both shapes come out of the same rag_chunks table on the live board:
        # 559 rows source_table='dic_documents', 3552 'rag_compliance_corpus'.
        return [
            _hit("rag", "dic_documents", "rag_chunk", score=0.9,
                 source_type="dic_document"),
            _hit("rag", "rag_compliance_corpus", "rag_chunk", score=0.8,
                 source_type="compliance_reference"),
            _hit("rag", "rag_compliance_corpus", "rag_chunk", score=0.7,
                 source_type="compliance_reference"),
        ]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)

    results = search_service.search(
        "what do the ingested collections say about retention",
        ctx=CortexContext(domain=DI_DOMAIN),
        strategy="rag",  # pin the backend so the test is deterministic
        config=cfg,
    )

    assert [r.citation.source_table for r in results] == ["dic_documents"]

    scope = results[0].metadata["router"]["domain_scope"]
    assert scope["domain"] == DI_DOMAIN
    assert scope["filtered_out"] == 2
    assert scope["sources"] == ["dic_"]
    assert "dic_documents" in scope["collections"]


def test_dic_backend_hits_are_all_in_scope(monkeypatch, cfg):
    """The lens must not row-drop its own backend — filtered_out stays 0 for a
    known-good query, which is the condition the config's standing decision on
    populating ``sources`` requires before a prefix list may ship."""
    def dic(query, top_k=5, ctx=None):
        return [
            _hit("dic", "dic_documents", "dic_document", score=0.9,
                 collection_id="col-1"),
            _hit("dic", "dic_documents", "dic_document", score=0.6,
                 collection_id="col-2"),
        ]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "dic", dic)

    results = search_service.search(
        "summarize the SOP collection",
        ctx=CortexContext(domain=DI_DOMAIN),
        strategy="dic",
        config=cfg,
    )

    assert len(results) == 2
    assert results[0].metadata["router"]["domain_scope"]["filtered_out"] == 0


# --------------------------------------------------------------------------- #
# 3. A query outside the lens is unaffected
# --------------------------------------------------------------------------- #
def test_general_mode_is_unaffected(monkeypatch, cfg):
    def rag(query, top_k=5, ctx=None):
        return [
            _hit("rag", "dic_documents", "rag_chunk", score=0.9),
            _hit("rag", "rag_compliance_corpus", "rag_chunk", score=0.8),
        ]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)

    results = search_service.search("recent activity", strategy="rag", config=cfg)

    assert len(results) == 2
    assert "domain_scope" not in results[0].metadata["router"]
    assert apply_persona(CortexContext(), "base") == "base"


def test_document_lens_still_unscoped(monkeypatch, cfg):
    """The broad ``document`` lens is deliberately NOT row-scoped — adding the DI
    lens must not have changed it."""
    profile = load_domain_profile("document", cfg)
    assert profile.sources == []

    def rag(query, top_k=5, ctx=None):
        return [
            _hit("rag", "dic_documents", "rag_chunk", score=0.9),
            _hit("rag", "rag_compliance_corpus", "rag_chunk", score=0.8),
        ]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)

    results = search_service.search(
        "retention policy",
        ctx=CortexContext(domain="document"),
        strategy="rag",
        config=cfg,
    )
    assert len(results) == 2
    assert "filtered_out" not in results[0].metadata["router"]["domain_scope"]


def test_di_persona_is_injected_only_under_the_lens(cfg):
    out = apply_persona(CortexContext(domain=DI_DOMAIN), "Follow the style guide.")
    assert "Document Intelligence corpus" in out
    assert "Follow the style guide." in out
    assert "Document Intelligence corpus" not in apply_persona(
        CortexContext(domain="general"), "Follow the style guide."
    )
