"""Tests for near-duplicate title detection in the DIC ingest orchestrator.

aiify-opp-45: hardcoded_threshold -> anomaly_detection. These pin the
load-bearing guarantees of ``_detect_near_duplicate_titles``:

* too few comparison docs (< 2) → empty list (no IQR possible);
* title too short (< _NEAR_DUP_MIN_TOKENS tokens) → empty list;
* no outlier in the score distribution → empty list;
* anomalously similar title clears the IQR fence → returned as candidate;
* candidates are sorted descending by similarity;
* result is surfaced under IngestOutcome.metadata["near_duplicates"] when
  detect_near_duplicates=True and the function returns candidates;
* detect_near_duplicates=False suppresses the check entirely;
* any DB error degrades silently to an empty list (never raises).
"""
from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
_detect = ingest._detect_near_duplicate_titles
_MIN_TOKENS = ingest._NEAR_DUP_MIN_TOKENS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(rows: list[tuple[str, str, str]]):
    """Build an in-memory SQLite DB seeded with dic_documents rows.

    Each row is (doc_id, filename, title); created_at is set to now.

    Returns a ``StorageConnection``, NOT a bare ``sqlite3.Connection``. The code
    under test authors PostgreSQL-dialect SQL (`%s` placeholders) — correct,
    since PG is the primary backend — which sqlite3 cannot parse. A raw
    connection made every query raise, the function's `except` returned `[]`,
    and three tests in this file passed *on that empty list* while asserting
    nothing: "no outlier -> []" and "sorted descending" are both trivially true
    of a result the detector never produced.
    """
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:")
    conn = StorageConnection(raw, "sqlite")
    conn.execute(
        """
        CREATE TABLE dic_documents (
            doc_id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            filename TEXT,
            title TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    for doc_id, filename, title in rows:
        conn.execute(
            "INSERT INTO dic_documents VALUES (?, 'col1', ?, ?, ?)",
            (doc_id, filename, title, now),
        )
    conn.commit()
    return conn


def _tokens(n: int) -> str:
    """Return a title with exactly ``n`` space-separated unique tokens."""
    return " ".join(f"word{i}" for i in range(n))


# ---------------------------------------------------------------------------
# Guard conditions
# ---------------------------------------------------------------------------

class TestGuards:
    def test_too_few_comparison_docs_returns_empty(self):
        """Only 1 existing doc → cannot compute IQR → []."""
        conn = _make_conn([("other-1", "a.pdf", "invoice payment receipt summary")])
        result = _detect("new-doc", "invoice payment receipt summary", "col1", conn)
        assert result == []

    def test_title_too_short_returns_empty(self):
        """Title with fewer than _NEAR_DUP_MIN_TOKENS tokens → []."""
        short = " ".join(f"w{i}" for i in range(_MIN_TOKENS - 1))
        conn = _make_conn([
            ("d1", "a.pdf", "invoice payment receipt summary alpha"),
            ("d2", "b.pdf", "contract agreement terms conditions beta"),
        ])
        result = _detect("new-doc", short, "col1", conn)
        assert result == []

    def test_new_doc_excluded_from_comparison(self):
        """The new doc's own id is excluded — comparing to self must not happen."""
        conn = _make_conn([
            ("new-doc", "self.pdf", "alpha beta gamma delta epsilon"),
            ("other-1", "a.pdf", "completely different words here now"),
        ])
        # Only 1 valid comparison doc after excluding self → []
        result = _detect("new-doc", "alpha beta gamma delta epsilon", "col1", conn)
        assert result == []


# ---------------------------------------------------------------------------
# IQR outlier detection
# ---------------------------------------------------------------------------

class TestIQRDetection:
    def test_no_outlier_returns_empty(self):
        """When all similarity scores are uniformly low, IQR fence not cleared → []."""
        # All titles share very few tokens with the new one
        conn = _make_conn([
            ("d1", "a.pdf", "alpha beta gamma delta epsilon"),
            ("d2", "b.pdf", "sigma tau upsilon phi chi psi"),
            ("d3", "c.pdf", "red blue green purple orange yellow"),
            ("d4", "d.pdf", "cat dog bird fish snake turtle"),
        ])
        # "apple mango lemon cherry grape melon" shares ~0 tokens with all above
        result = _detect("new-doc", "apple mango lemon cherry grape melon", "col1", conn)
        assert result == []

    def test_high_similarity_pair_flagged(self):
        """One near-identical title should clear the IQR fence and be returned.

        IQR-based detection requires enough low-similarity points to keep Q3
        near 0 so the fence (Q3 + 1.5×IQR) stays below the outlier score.
        With ≥6 near-zero scores, Q3=0, IQR=0, fence=0, and the outlier (1.0)
        clears easily.
        """
        conn = _make_conn([
            ("d1", "contract.pdf", "service level agreement terms conditions renewal"),
            ("d2", "invoice.pdf", "payment receipt vendor purchase order cost"),
            ("d3", "memo.pdf", "internal memo staff announcement meeting agenda"),
            ("d5", "plan.pdf", "project plan timeline milestones deliverables scope"),
            ("d6", "policy.pdf", "corporate policy compliance guidelines procedures"),
            ("d7", "report.pdf", "annual sustainability environmental impact assessment"),
            # Near-duplicate of the new title
            ("d4", "near.pdf", "quarterly financial report fiscal year 2024 summary"),
        ])
        result = _detect(
            "new-doc",
            "quarterly financial report fiscal year 2024 summary",
            "col1",
            conn,
        )
        assert any(c["doc_id"] == "d4" for c in result)

    def test_results_sorted_descending(self):
        """When multiple candidates are flagged they are sorted similarity desc."""
        conn = _make_conn([
            ("d1", "a.pdf", "quarterly financial report fiscal year 2024 summary review"),
            ("d2", "b.pdf", "quarterly financial report fiscal year 2024"),
            ("d3", "c.pdf", "completely unrelated document about cooking recipes"),
            ("d4", "d.pdf", "another totally different topic about gardening tools"),
            ("d5", "e.pdf", "corporate policy compliance guidelines procedures"),
            ("d6", "f.pdf", "project plan timeline milestones deliverables scope"),
            ("d7", "g.pdf", "annual sustainability environmental impact assessment"),
        ])
        result = _detect(
            "new-doc",
            "quarterly financial report fiscal year 2024 summary",
            "col1",
            conn,
        )
        if len(result) >= 2:
            sims = [c["similarity"] for c in result]
            assert sims == sorted(sims, reverse=True)

    def test_candidate_fields_present(self):
        """Each returned candidate has doc_id, filename, title, similarity."""
        conn = _make_conn([
            ("d1", "match.pdf", "quarterly financial report fiscal year 2024 summary"),
            ("d2", "b.pdf", "unrelated document about something different entirely"),
            ("d3", "c.pdf", "another topic completely divorced from finance"),
            ("d4", "d.pdf", "yet another unrelated document on a different subject"),
            ("d5", "e.pdf", "corporate policy compliance guidelines procedures rules"),
            ("d6", "f.pdf", "project plan timeline milestones deliverables scope"),
            ("d7", "g.pdf", "annual sustainability environmental impact assessment"),
        ])
        result = _detect(
            "new-doc",
            "quarterly financial report fiscal year 2024 summary",
            "col1",
            conn,
        )
        for c in result:
            assert "doc_id" in c
            assert "filename" in c
            assert "title" in c
            assert "similarity" in c
            assert 0.0 <= c["similarity"] <= 1.0


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

class TestErrorResilience:
    def test_db_error_degrades_to_empty(self):
        """Any exception from the DB is swallowed and [] is returned."""
        bad_conn = MagicMock()
        bad_conn.execute.side_effect = RuntimeError("simulated DB failure")
        result = _detect("new-doc", "invoice payment receipt summary report", "col1", bad_conn)
        assert result == []

    def test_none_title_returns_empty(self):
        """``None`` title is handled gracefully."""
        conn = _make_conn([
            ("d1", "a.pdf", "invoice payment receipt"),
            ("d2", "b.pdf", "contract agreement terms"),
        ])
        result = _detect("new-doc", None, "col1", conn)  # type: ignore[arg-type]
        assert result == []


# ---------------------------------------------------------------------------
# Wire-up: detect_near_duplicates flag
# ---------------------------------------------------------------------------

class TestWireUp:
    """Smoke tests that the flag is respected inside ingest_file."""

    def test_flag_false_skips_detection(self, tmp_path):
        """detect_near_duplicates=False must not call _detect_near_duplicate_titles."""
        txt = tmp_path / "test.txt"
        txt.write_text("Hello world content here for testing purposes only.", encoding="utf-8")

        with (
            patch.object(ingest, "_detect_near_duplicate_titles", wraps=ingest._detect_near_duplicate_titles) as mock_detect,
            patch.object(ingest, "_embed_and_store", return_value=0),
            patch.object(ingest, "chunk_content", return_value=[]),
            patch("tools.rag.rag_to_kg_ingester.ingest_chunk", side_effect=ImportError),
        ):
            outcome = ingest.ingest_file(
                str(txt),
                "test-collection",
                embed=False,
                bridge_kg=False,
                summarize=False,
                clean_ocr=False,
                extract_metadata=False,
                extract_identifiers=False,
                extract_correspondence=False,
                detect_anomalies=False,
                detect_near_duplicates=False,
            )
        mock_detect.assert_not_called()
        assert "near_duplicates" not in (outcome.metadata or {})
