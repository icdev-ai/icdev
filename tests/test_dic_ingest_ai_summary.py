"""Tests for the LLM document-summary enrichment in the DIC ingest orchestrator.

aiify-opp-6098: document_ingestion_pipeline -> llm_generation. These pin the
load-bearing guarantees of ``_ai_document_summary``:

* it grounds the model on the document's own text and never sends more than
  ``_SUMMARY_INPUT_CHARS`` characters (cheap, bounded, no drift);
* it parses strict JSON, tolerating fenced code blocks;
* it degrades silently to ``None`` on empty input, blank output, malformed
  output, or any LLM failure — ingestion must never break on enrichment.
"""
from __future__ import annotations

import importlib

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that records the request and returns a canned reply."""

    last_request = None

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)

    _content = '{"title": "Quarterly Budget Report", "summary": "Summarizes Q3 spend."}'


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = (
        '{"title": "Quarterly Budget Report", "summary": "Summarizes Q3 spend."}'
    )
    yield


def _patch_router(monkeypatch, content=None):
    import sys as _sys
    if content is not None:
        _Router._content = content
    # Patch the attribute on the real module object the helper imports from.
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    # Patch ALL known router aliases — the _ToolsRedirect shim causes
    # `from tools.llm.router import LLMRouter` to resolve to different
    # module objects depending on full-suite import ordering.
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


def test_parses_json_title_and_summary(monkeypatch):
    _patch_router(monkeypatch)
    out = ingest._ai_document_summary("Some real document text.", "budget.pdf", 3)
    assert out == {
        "title": "Quarterly Budget Report",
        "summary": "Summarizes Q3 spend.",
    }


def test_empty_text_returns_none_without_calling_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_document_summary("   ", "empty.txt", 1) is None
    # The model must never be invoked for empty input.
    assert _Router.last_request is None


def test_input_text_is_truncated_to_budget(monkeypatch):
    _patch_router(monkeypatch)
    big = "A" * (ingest._SUMMARY_INPUT_CHARS + 5000)
    ingest._ai_document_summary(big, "big.txt", 99)
    sent = _Router.last_request.messages[0]["content"]
    # Only the leading slice is sent — grounding stays bounded regardless of size.
    assert sent.count("A") == ingest._SUMMARY_INPUT_CHARS


def test_tolerates_fenced_json(monkeypatch):
    _patch_router(
        monkeypatch,
        content='```json\n{"title": "T", "summary": "S"}\n```',
    )
    out = ingest._ai_document_summary("text", "f.txt", 1)
    assert out == {"title": "T", "summary": "S"}


def test_blank_fields_return_none(monkeypatch):
    _patch_router(monkeypatch, content='{"title": "", "summary": ""}')
    assert ingest._ai_document_summary("text", "f.txt", 1) is None


def test_malformed_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json at all")
    assert ingest._ai_document_summary("text", "f.txt", 1) is None


def test_llm_failure_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    import sys as _sys
    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Boom)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Boom)
    assert ingest._ai_document_summary("text", "f.txt", 1) is None


def test_partial_json_title_only(monkeypatch):
    _patch_router(monkeypatch, content='{"title": "Only Title"}')
    out = ingest._ai_document_summary("text", "f.txt", 1)
    assert out == {"title": "Only Title", "summary": ""}


def test_ingest_outcome_to_dict_includes_summary():
    oc = ingest.IngestOutcome(
        doc_id="d", version_id="v", collection_id="c", source_id="s",
        provider="p", chunks=1, chunks_embedded=1, kg_entities=0,
        kg_relationships=0, tenant_id="t", classification="UNCLASSIFIED",
        summary="An abstract.",
    )
    d = oc.to_dict()
    assert d["summary"] == "An abstract."


def test_ingest_outcome_summary_defaults_empty():
    oc = ingest.IngestOutcome(
        doc_id="d", version_id="v", collection_id="c", source_id="s",
        provider="p", chunks=0, chunks_embedded=0, kg_entities=0,
        kg_relationships=0, tenant_id="t", classification="UNCLASSIFIED",
    )
    assert oc.summary == ""
    assert oc.to_dict()["summary"] == ""
