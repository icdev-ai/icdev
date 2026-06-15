"""Tests for the LLM OCR-cleanup enrichment in the DIC ingest orchestrator.

aiify-opp-6118: ocr_extraction_pipeline -> llm_generation. These pin the
load-bearing guarantees of ``_ai_ocr_cleanup`` and its wiring:

* it grounds the model on the OCR text alone and only runs within the bounded
  ``_OCR_CLEANUP_MAX_CHARS`` budget (cheap, never truncates a real document);
* a length-ratio guard discards any result that drifts too far from the
  original, so the model can never silently drop or invent content;
* it degrades silently to ``None`` on empty input, oversized input, blank
  output, or any LLM failure — ingestion must never break on enrichment;
* cleanup only fires for genuinely OCR-derived providers, never clean text.
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
    _content = "Corrected clean text."

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = "Corrected clean text."
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


def test_returns_corrected_text(monkeypatch):
    _patch_router(monkeypatch, content="The quick brown fox jumped.")
    out = ingest._ai_ocr_cleanup("The qu-\nick br0wn  fox jum ped.")
    assert out == "The quick brown fox jumped."


def test_empty_text_returns_none_without_calling_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_ocr_cleanup("   ") is None
    # The model must never be invoked for empty input.
    assert _Router.last_request is None


def test_oversized_text_skipped_without_calling_llm(monkeypatch):
    _patch_router(monkeypatch)
    big = "A" * (ingest._OCR_CLEANUP_MAX_CHARS + 1)
    # Too large to clean cheaply — skip rather than truncate real content.
    assert ingest._ai_ocr_cleanup(big) is None
    assert _Router.last_request is None


def test_text_at_budget_is_sent(monkeypatch):
    _patch_router(monkeypatch, content="x" * ingest._OCR_CLEANUP_MAX_CHARS)
    src = "y" * ingest._OCR_CLEANUP_MAX_CHARS
    ingest._ai_ocr_cleanup(src)
    sent = _Router.last_request.messages[0]["content"]
    assert sent.count("y") == ingest._OCR_CLEANUP_MAX_CHARS


def test_blank_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="   ")
    assert ingest._ai_ocr_cleanup("real ocr text here") is None


def test_runaway_expansion_rejected(monkeypatch):
    # Model that more than doubles the length is assumed to have invented content.
    src = "short"
    _patch_router(monkeypatch, content="X" * (len(src) * 3))
    assert ingest._ai_ocr_cleanup(src) is None


def test_content_drop_rejected(monkeypatch):
    # Model that returns far less than the original is assumed to have dropped content.
    src = "A" * 100
    _patch_router(monkeypatch, content="A" * 10)
    assert ingest._ai_ocr_cleanup(src) is None


def test_strips_fenced_block(monkeypatch):
    _patch_router(monkeypatch, content="```\nThe corrected sentence body.\n```")
    out = ingest._ai_ocr_cleanup("The c0rrected sentence body.")
    assert out == "The corrected sentence body."


def test_llm_failure_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    import sys as _sys
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Boom)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Boom)
    assert ingest._ai_ocr_cleanup("some ocr text") is None


def test_ocr_providers_set_covers_ocr_paths():
    # The gate must include every OCR-derived provider name the extractors emit.
    assert "ocr" in ingest._OCR_PROVIDERS
    assert "pypdf+ocr" in ingest._OCR_PROVIDERS
    # Clean digital-text providers must NOT trigger cleanup.
    assert "pypdf" not in ingest._OCR_PROVIDERS
    assert "builtin-text" not in ingest._OCR_PROVIDERS


def test_ingest_outcome_to_dict_includes_ocr_cleaned():
    oc = ingest.IngestOutcome(
        doc_id="d", version_id="v", collection_id="c", source_id="s",
        provider="p", chunks=1, chunks_embedded=1, kg_entities=0,
        kg_relationships=0, tenant_id="t", classification="UNCLASSIFIED",
        ocr_cleaned=True,
    )
    d = oc.to_dict()
    assert d["ocr_cleaned"] is True


def test_ingest_outcome_ocr_cleaned_defaults_false():
    oc = ingest.IngestOutcome(
        doc_id="d", version_id="v", collection_id="c", source_id="s",
        provider="p", chunks=0, chunks_embedded=0, kg_entities=0,
        kg_relationships=0, tenant_id="t", classification="UNCLASSIFIED",
    )
    assert oc.ocr_cleaned is False
    assert oc.to_dict()["ocr_cleaned"] is False
