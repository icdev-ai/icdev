"""Tests for the LLM identifier-extraction enrichment in the DIC ingest orchestrator.

aiify-opp-5988: ocr_extraction_pipeline -> llm_generation. The external scan
flagged paperless-ngx ``src/documents/barcodes.py`` (the barcode/QR reader that
splits scans at separator barcodes and assigns an ASN). The repo is ephemeral,
so per the established aiify-opp pattern the augmentation lands in the analogous
ICDEV subsystem (DIC). These pin the load-bearing guarantees of
``_ai_extract_identifiers`` and its wiring:

* it grounds the model on the document text alone and only sends the leading
  ``_IDENTIFIER_INPUT_CHARS`` slice (cheap, bounded regardless of size);
* ``kind`` is constrained to the closed ``_IDENTIFIER_KINDS`` enum — anything
  else is dropped, so the model can never invent an identifier class;
* ``value`` must match the compact identifier shape (no free prose) AND its
  alphanumeric core must appear verbatim in the source text — a hard
  anti-hallucination guard;
* a confidence score gates each item and the whole suggestion — below
  ``_IDENTIFIER_MIN_CONFIDENCE`` results are dropped for the HITL / manual path;
* items are de-duplicated by (kind, value) and count-capped;
* it degrades silently to ``None`` on empty input, blank/garbled output, or any
  LLM failure — ingestion must never break on enrichment;
* the proposal is surfaced under ``IngestOutcome.metadata["identifiers"]`` and
  never persisted.
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


def _json(**kw):
    return json.dumps(kw)


# A document body that literally contains the codes the model "extracts" — the
# grounding guard requires each value's alphanumeric core to appear here.
_DOC = (
    "Invoice INV-2026-00123 issued under contract CW-9981 / PO 44120.\n"
    "Archive Serial Number: ASN000457. Reference: REF/2026/AB."
)


def test_returns_normalized_identifiers(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[
                {"kind": "invoice_number", "value": "INV-2026-00123", "confidence": 0.95},
                {"kind": "asn", "value": "ASN000457", "confidence": 0.9},
            ],
            confidence=0.93,
        ),
    )
    out = ingest._ai_extract_identifiers(_DOC)
    assert out == [
        {"kind": "invoice_number", "value": "INV-2026-00123", "confidence": 0.95},
        {"kind": "asn", "value": "ASN000457", "confidence": 0.9},
    ]


def test_empty_text_returns_none_without_calling_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_extract_identifiers("   ") is None
    assert _Router.last_request is None


def test_low_overall_confidence_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[{"kind": "asn", "value": "ASN000457", "confidence": 0.9}],
            confidence=0.4,
        ),
    )
    # Below _IDENTIFIER_MIN_CONFIDENCE -> whole suggestion discarded (HITL).
    assert ingest._ai_extract_identifiers(_DOC) is None


def test_overall_confidence_at_threshold_kept(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[{"kind": "asn", "value": "ASN000457", "confidence": 0.9}],
            confidence=ingest._IDENTIFIER_MIN_CONFIDENCE,
        ),
    )
    out = ingest._ai_extract_identifiers(_DOC)
    assert out is not None and out[0]["kind"] == "asn"


def test_unknown_kind_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[
                {"kind": "magic_barcode", "value": "ASN000457", "confidence": 0.9},
                {"kind": "po_number", "value": "44120", "confidence": 0.9},
            ],
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_identifiers(_DOC)
    assert out == [{"kind": "po_number", "value": "44120", "confidence": 0.9}]


def test_kind_case_insensitive(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[{"kind": "CONTRACT_NUMBER", "value": "CW-9981", "confidence": 0.9}],
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_identifiers(_DOC)
    assert out[0]["kind"] == "contract_number"


def test_value_not_in_text_dropped(monkeypatch):
    # The model fabricates a code that never appears in the document.
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[{"kind": "asn", "value": "ASN999999", "confidence": 0.99}],
            confidence=0.99,
        ),
    )
    assert ingest._ai_extract_identifiers(_DOC) is None


def test_grounding_tolerates_source_spacing(monkeypatch):
    # The source prints the code with stray spacing/separators; the model returns
    # the compact code. The alphanumeric-core membership guard still matches.
    doc = "Archive Serial Number: ASN 000-457 (filed)."
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[{"kind": "asn", "value": "ASN000457", "confidence": 0.9}],
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_identifiers(doc)
    assert out == [{"kind": "asn", "value": "ASN000457", "confidence": 0.9}]


def test_prose_value_rejected(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[
                {"kind": "reference_number",
                 "value": "the invoice issued under contract", "confidence": 0.9}
            ],
            confidence=0.9,
        ),
    )
    assert ingest._ai_extract_identifiers(_DOC) is None


def test_overlong_value_rejected(monkeypatch):
    long_val = "A" * (ingest._IDENTIFIER_VALUE_MAX_LEN + 5)
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[{"kind": "asn", "value": long_val, "confidence": 0.9}],
            confidence=0.9,
        ),
    )
    assert ingest._ai_extract_identifiers(_DOC) is None


def test_per_item_low_confidence_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[
                {"kind": "asn", "value": "ASN000457", "confidence": 0.2},
                {"kind": "po_number", "value": "44120", "confidence": 0.9},
            ],
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_identifiers(_DOC)
    assert out == [{"kind": "po_number", "value": "44120", "confidence": 0.9}]


def test_duplicates_deduped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[
                {"kind": "asn", "value": "ASN000457", "confidence": 0.9},
                {"kind": "asn", "value": "asn000457", "confidence": 0.8},
            ],
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_identifiers(_DOC)
    # Same (kind, value-lower) collapses to one entry.
    assert len(out) == 1


def test_count_capped(monkeypatch):
    # Build a doc + matching identifiers exceeding the cap.
    n = ingest._IDENTIFIER_MAX_ITEMS + 5
    doc = " ".join(f"REF{i:04d}" for i in range(n))
    items = [
        {"kind": "reference_number", "value": f"REF{i:04d}", "confidence": 0.9}
        for i in range(n)
    ]
    _patch_router(monkeypatch, content=_json(identifiers=items, confidence=0.9))
    out = ingest._ai_extract_identifiers(doc)
    assert len(out) == ingest._IDENTIFIER_MAX_ITEMS


def test_missing_overall_confidence_returns_none(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            identifiers=[{"kind": "asn", "value": "ASN000457", "confidence": 0.9}]
        ),
    )
    assert ingest._ai_extract_identifiers(_DOC) is None


def test_empty_identifiers_list_returns_none(monkeypatch):
    _patch_router(monkeypatch, content=_json(identifiers=[], confidence=0.9))
    assert ingest._ai_extract_identifiers(_DOC) is None


def test_strips_fenced_block(monkeypatch):
    body = _json(
        identifiers=[{"kind": "po_number", "value": "44120", "confidence": 0.9}],
        confidence=0.9,
    )
    _patch_router(monkeypatch, content=f"```json\n{body}\n```")
    out = ingest._ai_extract_identifiers(_DOC)
    assert out[0]["value"] == "44120"


def test_garbled_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json at all")
    assert ingest._ai_extract_identifiers(_DOC) is None


def test_input_truncated_to_budget(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(identifiers=[], confidence=0.9),
    )
    src = "q" * (ingest._IDENTIFIER_INPUT_CHARS + 5000)
    ingest._ai_extract_identifiers(src)
    sent = _Router.last_request.messages[0]["content"]
    # Only the leading _IDENTIFIER_INPUT_CHARS characters reach the model.
    assert sent.count("q") == ingest._IDENTIFIER_INPUT_CHARS


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
    assert ingest._ai_extract_identifiers(_DOC) is None


def test_ingest_outcome_surfaces_identifiers_in_metadata():
    md = {"identifiers": [{"kind": "asn", "value": "ASN000457", "confidence": 0.9}]}
    oc = ingest.IngestOutcome(
        doc_id="d", version_id="v", collection_id="c", source_id="s",
        provider="p", chunks=1, chunks_embedded=1, kg_entities=0,
        kg_relationships=0, tenant_id="t", classification="UNCLASSIFIED",
        metadata=md,
    )
    assert oc.to_dict()["metadata"]["identifiers"][0]["value"] == "ASN000457"
