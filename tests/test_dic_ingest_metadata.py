"""Tests for the LLM metadata-extraction enrichment in the DIC ingest orchestrator.

aiify-opp-6086: metadata_extraction -> llm_generation. These pin the
load-bearing guarantees of ``_ai_metadata_extraction`` and its wiring:

* it grounds the model on the document text alone and only sends the leading
  ``_METADATA_INPUT_CHARS`` slice (cheap, bounded regardless of size);
* document_type is constrained to the closed ``_METADATA_DOC_TYPES`` enum —
  anything else collapses to "other", so the model can never invent a type;
* tags are lower-cased, de-duplicated, length- and count-capped;
* the date is kept only when it is a real ISO calendar date;
* a single confidence score gates the whole suggestion — below
  ``_METADATA_MIN_CONFIDENCE`` the result is dropped for the HITL / manual path;
* it degrades silently to ``None`` on empty input, blank/garbled output, or any
  LLM failure — ingestion must never break on enrichment;
* the proposal is surfaced on ``IngestOutcome.metadata`` and never persisted.
"""
from __future__ import annotations

import importlib
import json

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that records the request and returns a canned reply."""

    last_request = None
    _content = "{}"

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = "{}"
    yield


def _patch_router(monkeypatch, content=None):
    if content is not None:
        _Router._content = content
    # Patch the attribute on the real module object the helper imports from.
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)


def _json(**kw):
    return json.dumps(kw)


def test_returns_normalized_metadata(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            document_type="policy",
            tags=["Security", "MFA"],
            date="2026-01-15",
            confidence=0.92,
        ),
    )
    out = ingest._ai_metadata_extraction("Acme security policy text.", "policy.md")
    assert out == {
        "document_type": "policy",
        "tags": ["security", "mfa"],
        "date": "2026-01-15",
        "confidence": 0.92,
    }


def test_empty_text_returns_none_without_calling_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_metadata_extraction("   ", "f.md") is None
    assert _Router.last_request is None


def test_low_confidence_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(document_type="report", tags=["x"], date=None, confidence=0.4),
    )
    # Below _METADATA_MIN_CONFIDENCE -> whole suggestion discarded (HITL fallback).
    assert ingest._ai_metadata_extraction("some text", "f.md") is None


def test_confidence_at_threshold_kept(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            document_type="memo",
            tags=[],
            date=None,
            confidence=ingest._METADATA_MIN_CONFIDENCE,
        ),
    )
    out = ingest._ai_metadata_extraction("text", "f.md")
    assert out is not None
    assert out["confidence"] == ingest._METADATA_MIN_CONFIDENCE


def test_unknown_document_type_collapses_to_other(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            document_type="invoice-receipt-thing",
            tags=[],
            date=None,
            confidence=0.9,
        ),
    )
    out = ingest._ai_metadata_extraction("text", "f.md")
    assert out["document_type"] == "other"


def test_document_type_case_insensitive(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(document_type="CONTRACT", tags=[], date=None, confidence=0.9),
    )
    out = ingest._ai_metadata_extraction("text", "f.md")
    assert out["document_type"] == "contract"


def test_tags_deduped_and_capped(monkeypatch):
    many = [f"tag{i}" for i in range(20)] + ["TAG0", "tag0"]
    _patch_router(
        monkeypatch,
        content=_json(document_type="other", tags=many, date=None, confidence=0.9),
    )
    out = ingest._ai_metadata_extraction("text", "f.md")
    assert len(out["tags"]) == ingest._METADATA_MAX_TAGS
    # Lower-cased + de-duplicated: "tag0" appears once.
    assert out["tags"].count("tag0") == 1


def test_tag_length_capped(monkeypatch):
    long_tag = "z" * 200
    _patch_router(
        monkeypatch,
        content=_json(
            document_type="other", tags=[long_tag], date=None, confidence=0.9
        ),
    )
    out = ingest._ai_metadata_extraction("text", "f.md")
    assert len(out["tags"][0]) == ingest._METADATA_TAG_MAX_LEN


def test_invalid_date_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            document_type="other", tags=[], date="last Tuesday", confidence=0.9
        ),
    )
    out = ingest._ai_metadata_extraction("text", "f.md")
    assert out["date"] is None


def test_impossible_calendar_date_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            document_type="other", tags=[], date="2026-13-40", confidence=0.9
        ),
    )
    out = ingest._ai_metadata_extraction("text", "f.md")
    assert out["date"] is None


def test_missing_confidence_returns_none(monkeypatch):
    _patch_router(
        monkeypatch, content=_json(document_type="report", tags=[], date=None)
    )
    assert ingest._ai_metadata_extraction("text", "f.md") is None


def test_strips_fenced_block(monkeypatch):
    body = _json(document_type="plan", tags=[], date=None, confidence=0.8)
    _patch_router(monkeypatch, content=f"```json\n{body}\n```")
    out = ingest._ai_metadata_extraction("text", "f.md")
    assert out["document_type"] == "plan"


def test_garbled_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json at all")
    assert ingest._ai_metadata_extraction("text", "f.md") is None


def test_input_truncated_to_budget(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(document_type="other", tags=[], date=None, confidence=0.9),
    )
    src = "q" * (ingest._METADATA_INPUT_CHARS + 5000)
    ingest._ai_metadata_extraction(src, "f.md")
    sent = _Router.last_request.messages[0]["content"]
    # Only the leading _METADATA_INPUT_CHARS characters reach the model.
    assert sent.count("q") == ingest._METADATA_INPUT_CHARS


def test_llm_failure_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    assert ingest._ai_metadata_extraction("some text", "f.md") is None


def test_ingest_outcome_to_dict_includes_metadata():
    md = {"document_type": "policy", "tags": ["a"], "date": None, "confidence": 0.9}
    oc = ingest.IngestOutcome(
        doc_id="d", version_id="v", collection_id="c", source_id="s",
        provider="p", chunks=1, chunks_embedded=1, kg_entities=0,
        kg_relationships=0, tenant_id="t", classification="UNCLASSIFIED",
        metadata=md,
    )
    assert oc.to_dict()["metadata"] == md


def test_ingest_outcome_metadata_defaults_empty():
    oc = ingest.IngestOutcome(
        doc_id="d", version_id="v", collection_id="c", source_id="s",
        provider="p", chunks=0, chunks_embedded=0, kg_entities=0,
        kg_relationships=0, tenant_id="t", classification="UNCLASSIFIED",
    )
    assert oc.metadata == {}
    assert oc.to_dict()["metadata"] == {}
