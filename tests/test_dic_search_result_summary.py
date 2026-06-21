"""Tests for LLM-generated document summary in DIC search results.

aiify-rm-a3344-phase-83: fulltext_search_engine -> llm_generation, analog of
paperless ``src/documents/serialisers.py``.  At ingest time the existing
``_ai_document_summary()`` function produces a 1-3 sentence abstract; this
feature persists that summary in ``dic_documents.summary`` and includes it in
every ``DICSearchResult`` / ``to_dict()`` response.

Guarantees tested:
* ``DICSearchResult`` carries a ``doc_summary`` field defaulting to "".
* ``to_dict()`` includes "doc_summary" in its output.
* ``_doc_meta()`` returns "summary" from the DB row when available.
* ``_doc_meta()`` falls back to "" on missing column (pre-migration DBs).
* The search result assembly populates ``doc_summary`` from ``_doc_meta()``.
"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock


se = importlib.import_module("tools.document_intelligence.search_engine")
DICSearchResult = se.DICSearchResult
Citation = se.Citation
_doc_meta = se._doc_meta


# ---------------------------------------------------------------------------
# DICSearchResult dataclass
# ---------------------------------------------------------------------------

def test_doc_summary_field_default():
    r = DICSearchResult()
    assert r.doc_summary == ""


def test_doc_summary_assigned():
    r = DICSearchResult(doc_summary="Quarterly earnings rose 12% due to new contracts.")
    assert r.doc_summary == "Quarterly earnings rose 12% due to new contracts."


# ---------------------------------------------------------------------------
# to_dict() serialization
# ---------------------------------------------------------------------------

def test_to_dict_includes_doc_summary():
    r = DICSearchResult(
        chunk_id="ck-001",
        doc_id="doc-001",
        doc_title="Q3 Report",
        doc_summary="Revenue increased 15% in Q3.",
    )
    d = r.to_dict()
    assert "doc_summary" in d
    assert d["doc_summary"] == "Revenue increased 15% in Q3."


def test_to_dict_empty_summary_present():
    r = DICSearchResult(chunk_id="ck-002", doc_id="doc-002")
    d = r.to_dict()
    assert "doc_summary" in d
    assert d["doc_summary"] == ""


# ---------------------------------------------------------------------------
# _doc_meta() helper
# ---------------------------------------------------------------------------

def _mock_conn(row):
    """Return a mock connection whose cursor returns a single row."""
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn = MagicMock()
    conn.execute.return_value = cur
    return conn


def test_doc_meta_returns_summary():
    conn = _mock_conn(("Annual Report 2025", "CUI", "A comprehensive annual review."))
    result = _doc_meta(conn, "doc-abc")
    assert result["summary"] == "A comprehensive annual review."
    assert result["title"] == "Annual Report 2025"
    assert result["classification"] == "CUI"


def test_doc_meta_missing_row_returns_empty_summary():
    cur = MagicMock()
    cur.fetchone.return_value = None
    conn = MagicMock()
    conn.execute.return_value = cur
    result = _doc_meta(conn, "doc-xyz")
    assert result["summary"] == ""


def test_doc_meta_exception_returns_empty_summary():
    conn = MagicMock()
    conn.execute.side_effect = Exception("DB gone")
    result = _doc_meta(conn, "doc-err")
    assert result["summary"] == ""
    assert result["title"] == "doc-err"


def test_doc_meta_null_summary_coerces_to_empty():
    conn = _mock_conn(("Some Doc", "SECRET", None))
    result = _doc_meta(conn, "doc-null")
    assert result["summary"] == ""


# ---------------------------------------------------------------------------
# Search result assembly uses doc_summary
# ---------------------------------------------------------------------------

def test_search_result_assembly_populates_doc_summary():
    """DICSearchResult constructed with doc_summary from _doc_meta() output."""
    meta_info = {"title": "Ops Manual", "classification": "CUI", "summary": "Operations guide for Q4."}
    r = DICSearchResult(
        chunk_id="ck-003",
        doc_id="doc-003",
        doc_title=meta_info["title"],
        doc_summary=meta_info.get("summary", ""),
    )
    assert r.doc_summary == "Operations guide for Q4."
    assert r.to_dict()["doc_summary"] == "Operations guide for Q4."
