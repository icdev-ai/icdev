"""Tests for LLM workflow mutation proposals in the DIC ingest orchestrator.

aiify-opp-111: metadata_extraction -> llm_generation. These pin the
load-bearing guarantees of ``_ai_propose_workflow_mutations`` and its wiring:

* caller-supplied custom field definitions are normalised and bounded before
  the LLM call — unsupported types and blank names are skipped, at most
  ``_MUTATION_MAX_FIELDS`` fields are passed;
* ``select`` fields: proposed value must be one of the caller-supplied options
  (membership guard, case-folded) — anything else is dropped;
* ``boolean`` fields: only True/False accepted; truthy strings also accepted;
* ``date`` fields: must be a real ISO (YYYY-MM-DD) calendar date or dropped;
* ``integer``/``monetary`` fields: must parse to int/float and lie within
  clamped bounds;
* ``url`` fields: shape-validated (must contain ://); alphanumeric core must
  appear verbatim in the source text (anti-hallucination);
* ``string`` fields: length-capped; alphanumeric core must appear in the
  source text (anti-hallucination);
* the overall confidence gates the whole proposal — below
  ``_MUTATION_MIN_CONFIDENCE`` the result is dropped for HITL;
* per-field confidence gates individual items — items below
  ``_MUTATION_FIELD_MIN_CONFIDENCE`` are silently dropped;
* it degrades silently to ``None`` on empty text, empty/invalid field list,
  garbled output, or any LLM failure — ingestion must never break on
  enrichment;
* the proposal is surfaced under ``IngestOutcome.metadata["workflow_mutations"]``
  and never silently persisted.
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
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)


# ── Shorthand ────────────────────────────────────────────────────────────────

def _run(text, fields, monkeypatch, content):
    _patch_router(monkeypatch, content)
    return ingest._ai_propose_workflow_mutations(text, fields)


# ── Degenerate inputs ─────────────────────────────────────────────────────────

def test_empty_text_returns_none(monkeypatch):
    _patch_router(monkeypatch, json.dumps({
        "mutations": [{"field": "author", "value": "Alice", "confidence": 0.9}],
        "storage_path": None,
        "confidence": 0.9,
    }))
    result = ingest._ai_propose_workflow_mutations(
        "", [{"name": "author", "type": "string"}]
    )
    assert result is None


def test_no_fields_returns_none(monkeypatch):
    _patch_router(monkeypatch, json.dumps({
        "mutations": [], "storage_path": None, "confidence": 0.9
    }))
    result = ingest._ai_propose_workflow_mutations("Some document text.", [])
    assert result is None


def test_all_blank_field_names_returns_none(monkeypatch):
    _patch_router(monkeypatch, json.dumps({
        "mutations": [], "storage_path": None, "confidence": 0.9
    }))
    result = ingest._ai_propose_workflow_mutations(
        "Some text.", [{"name": "", "type": "string"}, {"name": "  ", "type": "string"}]
    )
    assert result is None


def test_unsupported_type_skipped(monkeypatch):
    """Fields with unsupported types are silently skipped; if nothing valid remains → None."""
    _patch_router(monkeypatch, json.dumps({
        "mutations": [], "storage_path": None, "confidence": 0.9
    }))
    result = ingest._ai_propose_workflow_mutations(
        "Some text.", [{"name": "foo", "type": "imaginary_type"}]
    )
    assert result is None


def test_llm_unavailable_returns_none(monkeypatch):
    """LLM failure must never propagate — degrades to None."""
    class _BrokenRouter:
        def __init__(self, *a, **k):
            pass
        def invoke(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(router_mod, "LLMRouter", _BrokenRouter)
    result = ingest._ai_propose_workflow_mutations(
        "Quarterly report for Q1 2025.", [{"name": "period", "type": "string"}]
    )
    assert result is None


# ── Confidence gating ─────────────────────────────────────────────────────────

def test_low_overall_confidence_drops_proposal(monkeypatch):
    doc = "Quarterly report for Q1 2025 from division alpha."
    content = json.dumps({
        "mutations": [{"field": "period", "value": "Q1 2025", "confidence": 0.9}],
        "storage_path": None,
        "confidence": 0.30,  # below _MUTATION_MIN_CONFIDENCE default 0.70
    })
    result = _run(doc, [{"name": "period", "type": "string"}], monkeypatch, content)
    assert result is None


def test_per_field_low_confidence_drops_that_field(monkeypatch):
    doc = "Quarterly report for Q1 2025 from division alpha."
    content = json.dumps({
        "mutations": [
            {"field": "period", "value": "Q1 2025", "confidence": 0.90},
            {"field": "division", "value": "alpha", "confidence": 0.40},  # below 0.65
        ],
        "storage_path": None,
        "confidence": 0.85,
    })
    result = _run(
        doc,
        [{"name": "period", "type": "string"}, {"name": "division", "type": "string"}],
        monkeypatch,
        content,
    )
    assert result is not None
    names = {m["field"] for m in result["mutations"]}
    assert "period" in names
    assert "division" not in names


# ── String fields ─────────────────────────────────────────────────────────────

def test_string_field_happy_path(monkeypatch):
    doc = "Contract signed by AlphaCorp on behalf of the government."
    content = json.dumps({
        "mutations": [{"field": "party", "value": "AlphaCorp", "confidence": 0.92}],
        "storage_path": "contracts/alphacorp",
        "confidence": 0.92,
    })
    result = _run(doc, [{"name": "party", "type": "string"}], monkeypatch, content)
    assert result is not None
    assert result["mutations"][0]["field"] == "party"
    assert result["mutations"][0]["value"] == "AlphaCorp"
    assert result["storage_path"] == "contracts/alphacorp"


def test_string_field_not_in_text_dropped(monkeypatch):
    """Anti-hallucination: proposed string value whose core isn't in the text is dropped."""
    doc = "Quarterly report for division alpha."
    content = json.dumps({
        "mutations": [{"field": "party", "value": "InventedCompanyXYZ", "confidence": 0.9}],
        "storage_path": None,
        "confidence": 0.9,
    })
    result = _run(doc, [{"name": "party", "type": "string"}], monkeypatch, content)
    # Either None or empty mutations list
    assert result is None or result["mutations"] == []


def test_string_field_too_long_dropped(monkeypatch):
    doc = "Some short document."
    long_value = "x" * 300
    # long_value's core won't be in the doc anyway, but length check fires first
    content = json.dumps({
        "mutations": [{"field": "notes", "value": long_value, "confidence": 0.9}],
        "storage_path": None,
        "confidence": 0.9,
    })
    result = _run(doc, [{"name": "notes", "type": "string"}], monkeypatch, content)
    assert result is None or result["mutations"] == []


# ── Boolean fields ────────────────────────────────────────────────────────────

def test_boolean_true(monkeypatch):
    doc = "This document is classified as confidential."
    content = json.dumps({
        "mutations": [{"field": "confidential", "value": True, "confidence": 0.95}],
        "storage_path": None,
        "confidence": 0.95,
    })
    result = _run(doc, [{"name": "confidential", "type": "boolean"}], monkeypatch, content)
    assert result is not None
    assert result["mutations"][0]["value"] is True


def test_boolean_string_truthy(monkeypatch):
    doc = "This document is classified as confidential."
    content = json.dumps({
        "mutations": [{"field": "confidential", "value": "yes", "confidence": 0.95}],
        "storage_path": None,
        "confidence": 0.95,
    })
    result = _run(doc, [{"name": "confidential", "type": "boolean"}], monkeypatch, content)
    assert result is not None
    assert result["mutations"][0]["value"] is True


def test_boolean_invalid_value_dropped(monkeypatch):
    doc = "This document is classified as confidential."
    content = json.dumps({
        "mutations": [{"field": "confidential", "value": "maybe", "confidence": 0.95}],
        "storage_path": None,
        "confidence": 0.95,
    })
    result = _run(doc, [{"name": "confidential", "type": "boolean"}], monkeypatch, content)
    assert result is None or result["mutations"] == []


# ── Integer fields ────────────────────────────────────────────────────────────

def test_integer_field_happy_path(monkeypatch):
    doc = "The invoice covers 42 line items."
    content = json.dumps({
        "mutations": [{"field": "line_items", "value": 42, "confidence": 0.88}],
        "storage_path": None,
        "confidence": 0.88,
    })
    result = _run(doc, [{"name": "line_items", "type": "integer"}], monkeypatch, content)
    assert result is not None
    assert result["mutations"][0]["value"] == 42


def test_integer_out_of_bounds_dropped(monkeypatch):
    doc = "Invoice for 999 items."
    content = json.dumps({
        "mutations": [{"field": "amount", "value": 2_000_000_000, "confidence": 0.9}],
        "storage_path": None,
        "confidence": 0.9,
    })
    result = _run(doc, [{"name": "amount", "type": "integer"}], monkeypatch, content)
    assert result is None or result["mutations"] == []


# ── Monetary fields ───────────────────────────────────────────────────────────

def test_monetary_field_happy_path(monkeypatch):
    doc = "Total invoice value is 12500.00 dollars."
    content = json.dumps({
        "mutations": [{"field": "total", "value": 12500.00, "confidence": 0.91}],
        "storage_path": None,
        "confidence": 0.91,
    })
    result = _run(doc, [{"name": "total", "type": "monetary"}], monkeypatch, content)
    assert result is not None
    assert result["mutations"][0]["value"] == 12500.00


def test_monetary_negative_dropped(monkeypatch):
    doc = "Credit note for -500 dollars."
    content = json.dumps({
        "mutations": [{"field": "total", "value": -500, "confidence": 0.9}],
        "storage_path": None,
        "confidence": 0.9,
    })
    result = _run(doc, [{"name": "total", "type": "monetary"}], monkeypatch, content)
    assert result is None or result["mutations"] == []


# ── Date fields ───────────────────────────────────────────────────────────────

def test_date_field_valid_iso(monkeypatch):
    doc = "This contract is effective from 2025-03-15."
    content = json.dumps({
        "mutations": [{"field": "effective_date", "value": "2025-03-15", "confidence": 0.93}],
        "storage_path": None,
        "confidence": 0.93,
    })
    result = _run(doc, [{"name": "effective_date", "type": "date"}], monkeypatch, content)
    assert result is not None
    assert result["mutations"][0]["value"] == "2025-03-15"


def test_date_field_invalid_format_dropped(monkeypatch):
    doc = "Contract dated March 15, 2025."
    content = json.dumps({
        "mutations": [{"field": "effective_date", "value": "March 15, 2025", "confidence": 0.88}],
        "storage_path": None,
        "confidence": 0.88,
    })
    result = _run(doc, [{"name": "effective_date", "type": "date"}], monkeypatch, content)
    assert result is None or result["mutations"] == []


# ── Select fields ─────────────────────────────────────────────────────────────

def test_select_field_valid_option(monkeypatch):
    doc = "This document is a quarterly financial report."
    fields = [{"name": "category", "type": "select", "options": ["Financial", "Legal", "HR"]}]
    content = json.dumps({
        "mutations": [{"field": "category", "value": "Financial", "confidence": 0.94}],
        "storage_path": None,
        "confidence": 0.94,
    })
    result = _run(doc, fields, monkeypatch, content)
    assert result is not None
    assert result["mutations"][0]["value"] == "Financial"


def test_select_field_case_insensitive_match(monkeypatch):
    doc = "This document is a quarterly financial report."
    fields = [{"name": "category", "type": "select", "options": ["Financial", "Legal", "HR"]}]
    content = json.dumps({
        "mutations": [{"field": "category", "value": "financial", "confidence": 0.94}],
        "storage_path": None,
        "confidence": 0.94,
    })
    result = _run(doc, fields, monkeypatch, content)
    assert result is not None
    # Canonical casing is restored
    assert result["mutations"][0]["value"] == "Financial"


def test_select_field_invalid_option_dropped(monkeypatch):
    doc = "This document is a quarterly financial report."
    fields = [{"name": "category", "type": "select", "options": ["Financial", "Legal", "HR"]}]
    content = json.dumps({
        "mutations": [{"field": "category", "value": "InventedCategory", "confidence": 0.94}],
        "storage_path": None,
        "confidence": 0.94,
    })
    result = _run(doc, fields, monkeypatch, content)
    assert result is None or result["mutations"] == []


def test_select_field_no_options_skipped(monkeypatch):
    """A select field with no options is unusable and must be skipped."""
    _patch_router(monkeypatch, json.dumps({
        "mutations": [], "storage_path": None, "confidence": 0.9
    }))
    result = ingest._ai_propose_workflow_mutations(
        "Some text.", [{"name": "cat", "type": "select", "options": []}]
    )
    assert result is None


# ── URL fields ────────────────────────────────────────────────────────────────

def test_url_field_happy_path(monkeypatch):
    doc = "See the full specification at https://example.gov/specs/v3."
    fields = [{"name": "reference_url", "type": "url"}]
    content = json.dumps({
        "mutations": [{"field": "reference_url", "value": "https://example.gov/specs/v3", "confidence": 0.91}],
        "storage_path": None,
        "confidence": 0.91,
    })
    result = _run(doc, fields, monkeypatch, content)
    assert result is not None
    assert result["mutations"][0]["value"] == "https://example.gov/specs/v3"


def test_url_field_not_in_text_dropped(monkeypatch):
    doc = "See the full specification in the appendix."
    fields = [{"name": "reference_url", "type": "url"}]
    content = json.dumps({
        "mutations": [{"field": "reference_url", "value": "https://invented.example.com/path", "confidence": 0.9}],
        "storage_path": None,
        "confidence": 0.9,
    })
    result = _run(doc, fields, monkeypatch, content)
    assert result is None or result["mutations"] == []


def test_url_field_no_scheme_dropped(monkeypatch):
    doc = "Find it at example.gov/specs."
    fields = [{"name": "reference_url", "type": "url"}]
    content = json.dumps({
        "mutations": [{"field": "reference_url", "value": "example.gov/specs", "confidence": 0.9}],
        "storage_path": None,
        "confidence": 0.9,
    })
    result = _run(doc, fields, monkeypatch, content)
    assert result is None or result["mutations"] == []


# ── Storage path ──────────────────────────────────────────────────────────────

def test_storage_path_propagated(monkeypatch):
    doc = "Financial report for FY2025 submitted by FinanceDivision."
    content = json.dumps({
        "mutations": [],
        "storage_path": "reports/financial/fy2025",
        "confidence": 0.85,
    })
    result = _run(doc, [{"name": "year", "type": "integer"}], monkeypatch, content)
    assert result is not None
    assert result["storage_path"] == "reports/financial/fy2025"


def test_storage_path_too_long_dropped(monkeypatch):
    doc = "Some document."
    content = json.dumps({
        "mutations": [],
        "storage_path": "a/" * 300,
        "confidence": 0.85,
    })
    # No mutations and no valid storage path → None
    result = _run(doc, [{"name": "year", "type": "integer"}], monkeypatch, content)
    # Either None or storage_path is None
    assert result is None or result.get("storage_path") is None


# ── Garbled output ────────────────────────────────────────────────────────────

def test_non_json_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, "sorry, I cannot help")
    result = ingest._ai_propose_workflow_mutations(
        "Quarterly financial report for Q1.", [{"name": "period", "type": "string"}]
    )
    assert result is None


def test_missing_confidence_key_returns_none(monkeypatch):
    _patch_router(monkeypatch, json.dumps({
        "mutations": [{"field": "period", "value": "Q1", "confidence": 0.9}],
        "storage_path": None,
        # confidence key absent at top level
    }))
    result = ingest._ai_propose_workflow_mutations(
        "Quarterly report for Q1.", [{"name": "period", "type": "string"}]
    )
    assert result is None


# ── ingest_file wiring ────────────────────────────────────────────────────────

def test_ingest_file_wires_workflow_mutations(tmp_path, monkeypatch):
    """workflow_custom_fields mutations are surfaced in IngestOutcome.metadata."""
    doc = tmp_path / "report.txt"
    doc.write_text("Financial report for FY2025 from AlphaCorp.", encoding="utf-8")

    # Stub out all LLM and DB side-effects.
    monkeypatch.setattr(ingest, "_embed_and_store", lambda *a, **k: 0)
    monkeypatch.setattr(ingest, "_ensure_schema", lambda *a: None)
    monkeypatch.setattr(ingest, "_resolve_context", lambda *a, **k: ("t1", "CUI"))

    _chunker = importlib.import_module("tools.rag.chunker")
    monkeypatch.setattr(_chunker, "chunk_content", lambda *a, **k: [])

    _storage = importlib.import_module("tools.db.storage")
    class _FakeConn:
        def execute(self, *a, **k):
            class _Cur:
                def fetchone(self): return None
                def fetchall(self): return []
            return _Cur()
        def cursor(self): return self
        def commit(self): pass
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(_storage, "get_connection", lambda: _FakeConn())
    monkeypatch.setattr(ingest, "get_connection", lambda: _FakeConn())

    # Override summarize/metadata/identifiers/classify/correspondence/anomaly
    # to keep the test focused on workflow_mutations.
    monkeypatch.setattr(ingest, "_ai_document_summary", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_ocr_cleanup", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_metadata_extraction", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_extract_identifiers", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_classify_into_taxonomy", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_extract_correspondence", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_metadata_anomaly_detection", lambda *a, **k: None)

    expected_wm: dict = {
        "mutations": [{"field": "company", "value": "AlphaCorp", "confidence": 0.9}],
        "storage_path": "reports/financial",
        "confidence": 0.9,
    }
    monkeypatch.setattr(
        ingest, "_ai_propose_workflow_mutations", lambda *a, **k: expected_wm
    )

    outcome = ingest.ingest_file(
        str(doc),
        "col-1",
        tenant_id="t1",
        classification="CUI",
        embed=False,
        bridge_kg=False,
        summarize=False,
        clean_ocr=False,
        extract_metadata=False,
        extract_identifiers=False,
        extract_correspondence=False,
        detect_anomalies=False,
        workflow_custom_fields=[{"name": "company", "type": "string"}],
    )
    assert "workflow_mutations" in outcome.metadata
    assert outcome.metadata["workflow_mutations"]["storage_path"] == "reports/financial"
    assert outcome.metadata["workflow_mutations"]["mutations"][0]["value"] == "AlphaCorp"


def test_ingest_file_no_workflow_fields_skips_call(tmp_path, monkeypatch):
    """When workflow_custom_fields is None, _ai_propose_workflow_mutations is not called."""
    doc = tmp_path / "report.txt"
    doc.write_text("Financial report.", encoding="utf-8")

    call_log = []

    def _spy(*a, **k):
        call_log.append(1)
        return None

    monkeypatch.setattr(ingest, "_embed_and_store", lambda *a, **k: 0)
    monkeypatch.setattr(ingest, "_ensure_schema", lambda *a: None)
    monkeypatch.setattr(ingest, "_resolve_context", lambda *a, **k: ("t1", "CUI"))
    _chunker2 = importlib.import_module("tools.rag.chunker")
    monkeypatch.setattr(_chunker2, "chunk_content", lambda *a, **k: [])
    _storage2 = importlib.import_module("tools.db.storage")
    class _FakeConn2:
        def execute(self, *a, **k):
            class _Cur:
                def fetchone(self): return None
                def fetchall(self): return []
            return _Cur()
        def cursor(self): return self
        def commit(self): pass
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(_storage2, "get_connection", lambda: _FakeConn2())
    monkeypatch.setattr(ingest, "_ai_document_summary", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_ocr_cleanup", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_metadata_extraction", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_extract_identifiers", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_classify_into_taxonomy", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_extract_correspondence", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_metadata_anomaly_detection", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "_ai_propose_workflow_mutations", _spy)

    ingest.ingest_file(
        str(doc),
        "col-1",
        tenant_id="t1",
        classification="CUI",
        embed=False,
        bridge_kg=False,
        summarize=False,
        clean_ocr=False,
        extract_metadata=False,
        extract_identifiers=False,
        extract_correspondence=False,
        detect_anomalies=False,
        workflow_custom_fields=None,
    )
    assert call_log == [], "_ai_propose_workflow_mutations must not be called when workflow_custom_fields is None"
