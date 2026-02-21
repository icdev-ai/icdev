# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.marketplace.search_engine — hybrid BM25 + semantic marketplace search."""

import json
import math
import sqlite3
import struct
from unittest.mock import patch

import pytest

from tools.marketplace.search_engine import (
    _bm25_score,
    _bm25_score_corpus,
    _blob_to_embedding,
    _cosine_similarity,
    _embedding_to_blob,
    _generate_embedding_fallback,
    _tokenize,
    search_assets,
)


# ---------------------------------------------------------------------------
# Schema for marketplace test DB
# ---------------------------------------------------------------------------
MARKETPLACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketplace_assets (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    display_name TEXT,
    asset_type TEXT NOT NULL,
    description TEXT NOT NULL,
    current_version TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'CUI // SP-CTI',
    impact_level TEXT NOT NULL DEFAULT 'IL4',
    publisher_tenant_id TEXT,
    publisher_org TEXT,
    publisher_user TEXT,
    catalog_tier TEXT NOT NULL DEFAULT 'tenant_local',
    status TEXT NOT NULL DEFAULT 'draft',
    license TEXT DEFAULT 'USG-INTERNAL',
    tags TEXT,
    compliance_controls TEXT,
    supported_languages TEXT,
    min_icdev_version TEXT,
    download_count INTEGER DEFAULT 0,
    install_count INTEGER DEFAULT 0,
    avg_rating REAL DEFAULT 0.0,
    rating_count INTEGER DEFAULT 0,
    deprecated INTEGER DEFAULT 0,
    replacement_slug TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS marketplace_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    content_hash TEXT NOT NULL,
    embedding BLOB NOT NULL,
    embedding_model TEXT DEFAULT 'nomic-embed-text',
    embedding_dimensions INTEGER DEFAULT 768,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id)
);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def marketplace_db(tmp_path):
    """Temporary DB with marketplace tables and sample published assets."""
    db_path = tmp_path / "mkt.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MARKETPLACE_SCHEMA)

    assets = [
        ("asset-001", "acme/stig-checker", "STIG Checker", "skill",
         "Automated STIG compliance checker for RHEL and Ubuntu",
         "1.0.0", "IL4", "tenant-acme", "ACME Corp", "tenant_local", "published",
         '["stig", "compliance", "linux"]'),
        ("asset-002", "acme/bdd-generator", "BDD Test Generator", "goal",
         "Generates Gherkin BDD scenarios from requirements",
         "2.1.0", "IL5", "tenant-acme", "ACME Corp", "central_vetted", "published",
         '["bdd", "testing", "gherkin"]'),
        ("asset-003", "delta/oracle-stig", "Oracle STIG Scanner", "compliance",
         "Oracle database STIG scanning and remediation tool",
         "1.2.0", "IL5", "tenant-delta", "Delta Inc", "tenant_local", "published",
         '["oracle", "stig", "database"]'),
        ("asset-004", "acme/draft-tool", "Draft Tool", "skill",
         "A tool still in draft status",
         "0.1.0", "IL2", "tenant-acme", "ACME Corp", "tenant_local", "draft",
         '["draft"]'),
        ("asset-005", "echo/sbom-gen", "SBOM Generator", "skill",
         "Generate CycloneDX SBOMs for Python projects",
         "3.0.0", "IL4", "tenant-echo", "Echo LLC", "tenant_local", "published",
         '["sbom", "cyclonedx", "python"]'),
    ]
    for a in assets:
        conn.execute(
            """INSERT INTO marketplace_assets
               (id, slug, name, asset_type, description, current_version,
                impact_level, publisher_tenant_id, publisher_org,
                catalog_tier, status, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            a,
        )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def marketplace_db_with_embeddings(marketplace_db):
    """Marketplace DB with pre-computed fallback embeddings stored."""
    conn = sqlite3.connect(str(marketplace_db))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, name, description, tags FROM marketplace_assets WHERE status = 'published'"
    ).fetchall()

    for row in rows:
        parts = [row["name"] or "", row["description"] or ""]
        if row["tags"]:
            try:
                tags = json.loads(row["tags"])
                if isinstance(tags, list):
                    parts.extend(tags)
            except (json.JSONDecodeError, TypeError):
                pass
        text = " ".join(parts)
        import hashlib
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        embedding = _generate_embedding_fallback(text)
        blob = _embedding_to_blob(embedding)
        conn.execute(
            """INSERT INTO marketplace_embeddings
               (asset_id, content_hash, embedding, embedding_model, embedding_dimensions)
               VALUES (?, ?, ?, ?, ?)""",
            (row["id"], content_hash, blob, "hashlib-fallback", 256),
        )

    conn.commit()
    conn.close()
    return marketplace_db


# ---------------------------------------------------------------------------
# TestTokenize
# ---------------------------------------------------------------------------
class TestTokenize:
    """_tokenize: lowercase splitting on non-alphanumeric boundaries."""

    def test_simple_words(self):
        assert _tokenize("hello world") == ["hello", "world"]

    def test_uppercase_lowered(self):
        assert _tokenize("STIG Checker") == ["stig", "checker"]

    def test_punctuation_splits(self):
        result = _tokenize("foo-bar_baz.qux")
        assert result == ["foo", "bar", "baz", "qux"]

    def test_empty_string_returns_empty(self):
        assert _tokenize("") == []

    def test_only_punctuation_returns_empty(self):
        assert _tokenize("---!!!...") == []


# ---------------------------------------------------------------------------
# TestBM25Score
# ---------------------------------------------------------------------------
class TestBM25Score:
    """_bm25_score: single-document BM25 TF scoring."""

    def test_matching_term_positive_score(self):
        score = _bm25_score(["stig"], "Automated STIG compliance checker")
        assert score > 0.0

    def test_no_match_zero_score(self):
        score = _bm25_score(["oracle"], "Python BDD test generator")
        assert score == 0.0

    def test_empty_query_zero_score(self):
        assert _bm25_score([], "some document text") == 0.0

    def test_empty_document_zero_score(self):
        assert _bm25_score(["stig"], "") == 0.0

    def test_multiple_matches_higher_score(self):
        single = _bm25_score(["stig"], "STIG checker tool")
        double = _bm25_score(["stig", "checker"], "STIG checker tool")
        assert double > single


# ---------------------------------------------------------------------------
# TestCosineSimilarity
# ---------------------------------------------------------------------------
class TestCosineSimilarity:
    """_cosine_similarity: vector similarity computation."""

    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_zero_vector_returns_zero(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_opposite_vectors_negative(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_similarity(a, b) < 0.0


# ---------------------------------------------------------------------------
# TestBM25Fallback
# ---------------------------------------------------------------------------
class TestBM25Fallback:
    """_bm25_score_corpus: TF-IDF fallback when rank_bm25 is unavailable."""

    def test_corpus_scoring_returns_list(self):
        docs = ["stig compliance checker", "bdd test generator", "oracle database scanner"]
        with patch("tools.marketplace.search_engine._HAS_BM25", False):
            scores = _bm25_score_corpus(["stig"], docs)
        assert isinstance(scores, list)
        assert len(scores) == 3

    def test_matching_doc_scores_highest(self):
        docs = ["stig compliance checker", "bdd test generator", "oracle database scanner"]
        with patch("tools.marketplace.search_engine._HAS_BM25", False):
            scores = _bm25_score_corpus(["stig"], docs)
        # First doc contains "stig", should score highest
        assert scores[0] > scores[1]
        assert scores[0] > scores[2]

    def test_empty_query_all_zeros(self):
        docs = ["doc one", "doc two"]
        with patch("tools.marketplace.search_engine._HAS_BM25", False):
            scores = _bm25_score_corpus([], docs)
        assert scores == [0.0, 0.0]

    def test_empty_corpus_returns_empty(self):
        with patch("tools.marketplace.search_engine._HAS_BM25", False):
            scores = _bm25_score_corpus(["test"], [])
        assert scores == []


# ---------------------------------------------------------------------------
# TestCombinedSearch
# ---------------------------------------------------------------------------
class TestCombinedSearch:
    """search_assets: hybrid keyword + semantic search integration."""

    def test_keyword_search_returns_results(self, marketplace_db):
        result = search_assets("stig", db_path=marketplace_db)
        assert result["total"] > 0
        names = [r["name"] for r in result["results"]]
        assert "STIG Checker" in names

    def test_excludes_draft_assets(self, marketplace_db):
        result = search_assets("draft tool", db_path=marketplace_db)
        ids = [r["asset_id"] for r in result["results"]]
        assert "asset-004" not in ids

    def test_no_results_for_unmatched_query(self, marketplace_db):
        result = search_assets("zzzznonexistent", db_path=marketplace_db)
        assert result["total"] == 0
        assert result["results"] == []

    def test_hybrid_search_with_embeddings(self, marketplace_db_with_embeddings):
        result = search_assets("stig", db_path=marketplace_db_with_embeddings)
        assert result["search_method"] == "hybrid"
        assert result["total"] > 0
        # Results should have both bm25 and semantic scores
        first = result["results"][0]
        assert first["bm25_score"] is not None
        assert first["semantic_score"] is not None

    def test_results_sorted_by_relevance(self, marketplace_db):
        result = search_assets("stig compliance", db_path=marketplace_db)
        scores = [r["relevance_score"] for r in result["results"]]
        assert scores == sorted(scores, reverse=True)

    def test_limit_caps_results(self, marketplace_db):
        result = search_assets("stig", limit=1, db_path=marketplace_db)
        assert len(result["results"]) <= 1


# ---------------------------------------------------------------------------
# TestFiltering
# ---------------------------------------------------------------------------
class TestFiltering:
    """search_assets: post-scoring filter parameters."""

    def test_filter_by_asset_type(self, marketplace_db):
        result = search_assets("stig", asset_type="compliance", db_path=marketplace_db)
        for item in result["results"]:
            assert item["asset_type"] == "compliance"

    def test_filter_by_impact_level(self, marketplace_db):
        result = search_assets("stig", impact_level="IL5", db_path=marketplace_db)
        for item in result["results"]:
            assert item["impact_level"] == "IL5"

    def test_filter_by_tenant_id(self, marketplace_db):
        result = search_assets("stig", tenant_id="tenant-acme", db_path=marketplace_db)
        for item in result["results"]:
            assert item["publisher_tenant_id"] == "tenant-acme"

    def test_filter_by_catalog_tier(self, marketplace_db):
        result = search_assets("generator", catalog_tier="central_vetted", db_path=marketplace_db)
        for item in result["results"]:
            assert item["catalog_tier"] == "central_vetted"


# CUI // SP-CTI
