"""Tests for AI-powered result snippet generation in the DIC search engine.

aiify-opp-74: fulltext_search_engine -> llm_generation, analog of paperless
``src/documents/serialisers.py`` highlights field. DIC upgrades fixed-length
content truncation to LLM-extracted, query-focused passages. These tests pin
the load-bearing guarantees:

* single snippet is extracted via LLM when content is non-empty;
* graceful degradation to raw fallback on empty content, LLM unavailable, or
  blank LLM response — ``snippet`` is always populated;
* ``llm_used`` and ``refusal_reason`` accurately reflect which path ran;
* batch ``generate_snippets`` caps LLM calls at the configured maximum and
  applies raw-fallback for results beyond the cap;
* query is passed as a user turn (never injected into the system prompt) so
  injection-scanning remains active.
"""
from __future__ import annotations

import importlib

import pytest

se = importlib.import_module("tools.document_intelligence.search_engine")
router_mod = importlib.import_module("tools.llm.router")
DICResultSnippet = se.DICResultSnippet
DICSearchEngine = se.DICSearchEngine
DICSearchResult = se.DICSearchResult
Citation = se.Citation


def _make_result(content: str = "Contracts grew 14% in Q3 driven by new hires.",
                 chunk_id: str = "ck-001",
                 doc_id: str = "doc-001") -> DICSearchResult:
    return DICSearchResult(
        chunk_id=chunk_id,
        doc_id=doc_id,
        content=content,
        score=0.8,
        citation=Citation(doc_id=doc_id),
    )


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter; records calls and returns a canned passage."""

    call_count = 0
    _content = "Contracts grew 14% in Q3."

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.call_count += 1
        return _Resp(self._content)


class _RouterBroken:
    def __init__(self, *a, **k):
        pass

    def invoke(self, *a, **k):
        raise RuntimeError("LLM offline")


def _patch_router(monkeypatch, router_cls=_Router):
    monkeypatch.setattr(router_mod, "LLMRouter", router_cls)
    try:
        import icdev.tools.llm.router as _icdev
        monkeypatch.setattr(_icdev, "LLMRouter", router_cls)
    except Exception:
        pass
    try:
        import tools.llm.router as _tools
        monkeypatch.setattr(_tools, "LLMRouter", router_cls)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset():
    _Router.call_count = 0
    _Router._content = "Contracts grew 14% in Q3."
    yield


# ── DICResultSnippet dataclass ─────────────────────────────────────────────

def test_snippet_to_dict_keys():
    s = DICResultSnippet(
        chunk_id="ck-1", doc_id="d-1", query="q", snippet="text", llm_used=True
    )
    d = s.to_dict()
    assert set(d) == {"chunk_id", "doc_id", "query", "snippet", "llm_used",
                      "refusal_reason", "origin"}


def test_snippet_origin_default():
    s = DICResultSnippet()
    assert s.origin == "ai_generated"


# ── generate_snippet — happy path ─────────────────────────────────────────

def test_generate_snippet_uses_llm(monkeypatch):
    _patch_router(monkeypatch)
    engine = DICSearchEngine()
    result = _make_result("Contracts grew 14% in Q3 driven by new hires. Budget rose.")
    snippet = engine.generate_snippet("contract growth", result)
    assert snippet.llm_used is True
    assert snippet.snippet == "Contracts grew 14% in Q3."
    assert snippet.refusal_reason == ""
    assert snippet.chunk_id == "ck-001"
    assert snippet.doc_id == "doc-001"
    assert _Router.call_count == 1


def test_generate_snippet_query_in_user_turn(monkeypatch):
    """The query must appear in the user message, not the system prompt."""
    _patch_router(monkeypatch)
    engine = DICSearchEngine()
    result = _make_result()

    class _Spy(_Router):
        last_messages = None
        last_system = None

        def invoke(self, fn, req):
            _Spy.last_messages = req.messages
            _Spy.last_system = req.system_prompt
            return _Resp("Contracts grew 14% in Q3.")

    _patch_router(monkeypatch, _Spy)
    engine.generate_snippet("contract growth", result)

    user_content = _Spy.last_messages[0]["content"]
    assert "contract growth" in user_content
    # query must NOT be embedded in the system prompt (injection risk)
    assert "contract growth" not in (_Spy.last_system or "")


# ── generate_snippet — graceful degradation ────────────────────────────────

def test_generate_snippet_empty_content(monkeypatch):
    _patch_router(monkeypatch)
    engine = DICSearchEngine()
    result = _make_result(content="")
    snippet = engine.generate_snippet("query", result)
    assert snippet.llm_used is False
    assert snippet.refusal_reason == "empty_content"
    assert snippet.snippet == ""
    assert _Router.call_count == 0  # LLM never called


def test_generate_snippet_llm_unavailable(monkeypatch):
    _patch_router(monkeypatch, _RouterBroken)
    engine = DICSearchEngine()
    result = _make_result()
    snippet = engine.generate_snippet("query", result)
    assert snippet.llm_used is False
    assert snippet.refusal_reason == "llm_unavailable"
    # fallback = content[:500]
    assert snippet.snippet == result.content[:500]


def test_generate_snippet_blank_llm_response(monkeypatch):
    class _BlankRouter(_Router):
        def invoke(self, fn, req):
            _BlankRouter.call_count += 1
            return _Resp("   ")

    _patch_router(monkeypatch, _BlankRouter)
    engine = DICSearchEngine()
    result = _make_result()
    snippet = engine.generate_snippet("query", result)
    assert snippet.llm_used is False
    assert snippet.refusal_reason == "llm_unavailable"
    assert snippet.snippet == result.content[:500]


def test_generate_snippet_none_llm_response(monkeypatch):
    class _NoneRouter(_Router):
        def invoke(self, fn, req):
            return None

    _patch_router(monkeypatch, _NoneRouter)
    engine = DICSearchEngine()
    result = _make_result()
    snippet = engine.generate_snippet("query", result)
    assert snippet.llm_used is False


# ── generate_snippets — batch ─────────────────────────────────────────────

def test_generate_snippets_length_matches_input(monkeypatch):
    _patch_router(monkeypatch)
    engine = DICSearchEngine()
    results = [_make_result(f"content {i}", chunk_id=f"ck-{i}") for i in range(4)]
    snippets = engine.generate_snippets("growth", results)
    assert len(snippets) == 4


def test_generate_snippets_llm_called_up_to_cap(monkeypatch):
    _patch_router(monkeypatch)
    engine = DICSearchEngine()
    cap = se._SNIPPET_MAX_RESULTS
    results = [_make_result(f"content {i}", chunk_id=f"ck-{i}") for i in range(cap + 3)]
    snippets = engine.generate_snippets("query", results)
    assert _Router.call_count == cap
    # results beyond the cap get raw fallback
    for s in snippets[cap:]:
        assert s.llm_used is False
        assert s.refusal_reason == "beyond_cap"


def test_generate_snippets_top_k_respected(monkeypatch):
    _patch_router(monkeypatch)
    engine = DICSearchEngine()
    results = [_make_result(f"content {i}", chunk_id=f"ck-{i}") for i in range(6)]
    engine.generate_snippets("query", results, top_k=2)
    assert _Router.call_count == 2


def test_generate_snippets_empty_list(monkeypatch):
    _patch_router(monkeypatch)
    engine = DICSearchEngine()
    assert engine.generate_snippets("query", []) == []
    assert _Router.call_count == 0


def test_generate_snippets_order_preserved(monkeypatch):
    _patch_router(monkeypatch)

    class _EchoRouter(_Router):
        def invoke(self, fn, req):
            _EchoRouter.call_count += 1
            # echo back the chunk content prefix for traceability
            _content_line = [
                l for l in req.messages[0]["content"].splitlines()
                if l.startswith("Document excerpt:")
            ]
            return _Resp(_Router._content)

    _patch_router(monkeypatch, _EchoRouter)
    engine = DICSearchEngine()
    results = [_make_result(f"content-{i}", chunk_id=f"ck-{i}") for i in range(3)]
    snippets = engine.generate_snippets("query", results)
    chunk_ids = [s.chunk_id for s in snippets]
    assert chunk_ids == ["ck-0", "ck-1", "ck-2"]


# ── DICResultSnippet serialization round-trip ──────────────────────────────

def test_snippet_dict_round_trip():
    s = DICResultSnippet(
        chunk_id="ck-99",
        doc_id="doc-99",
        query="budget growth",
        snippet="Contracts grew 14% in Q3.",
        llm_used=True,
        refusal_reason="",
    )
    d = s.to_dict()
    assert d["snippet"] == "Contracts grew 14% in Q3."
    assert d["llm_used"] is True
    assert d["refusal_reason"] == ""
    assert d["origin"] == "ai_generated"
