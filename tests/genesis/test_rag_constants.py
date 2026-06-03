# CUI // SP-CTI
"""Tests for constant extraction in RAG subsystem files."""
import sys
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.rag.retriever import (
    _RRF_K_DEFAULT, _VECTOR_TOP_K_DEFAULT, _FINAL_TOP_K_DEFAULT,
    _BM25_WEIGHT_DEFAULT, _MIN_SCORE_THRESHOLD, _RESULT_PREVIEW_CHARS,
    _rrf_fusion, _weighted_sum_fusion,
)
from tools.rag.reranker import (
    _RERANKER_DEFAULT_TOP_K, _RERANKER_DEFAULT_WEIGHT,
    _TEACHING_BOOST_WEIGHT, _MIN_WORD_LENGTH,
)
from tools.rag.faiss_vector_store import (
    _DEFAULT_EMBEDDING_DIM, _OVER_FETCH_MULTIPLIER, _SEARCH_DEFAULT_TOP_K,
)
from tools.rag.codebase_indexer import (
    _GIT_HISTORY_LIMIT, _GIT_LOG_TIMEOUT, _MAX_CHANGED_FILES,
    _DOCSTRING_MAX_WIDTH, _BACKGROUND_INTERVAL, _HASH_DIGEST_LEN,
    MAX_TEXT_CHUNK_CHARS,
)


class TestRetrieverConstants:
    def test_rrf_k_positive(self):
        assert _RRF_K_DEFAULT > 0

    def test_top_k_hierarchy(self):
        assert _VECTOR_TOP_K_DEFAULT > _FINAL_TOP_K_DEFAULT > 0

    def test_bm25_weight_valid(self):
        assert 0.0 < _BM25_WEIGHT_DEFAULT < 1.0

    def test_min_score_nonnegative(self):
        assert _MIN_SCORE_THRESHOLD >= 0.0

    def test_preview_chars_positive(self):
        assert _RESULT_PREVIEW_CHARS > 0

    def test_rrf_fusion_runs(self):
        # Empty input returns empty
        assert _rrf_fusion("query", []) == []

    def test_weighted_sum_runs(self):
        assert _weighted_sum_fusion("query", []) == []


class TestRerankerConstants:
    def test_top_k_positive(self):
        assert _RERANKER_DEFAULT_TOP_K > 0

    def test_weight_in_range(self):
        assert 0.0 < _RERANKER_DEFAULT_WEIGHT < 1.0

    def test_teaching_boost_nonnegative(self):
        assert _TEACHING_BOOST_WEIGHT >= 0.0

    def test_min_word_length_positive(self):
        assert _MIN_WORD_LENGTH > 0


class TestFaissConstants:
    def test_embedding_dim_positive(self):
        assert _DEFAULT_EMBEDDING_DIM > 0

    def test_over_fetch_gt_one(self):
        assert _OVER_FETCH_MULTIPLIER > 1

    def test_search_top_k_positive(self):
        assert _SEARCH_DEFAULT_TOP_K > 0


class TestCodebaseIndexerConstants:
    def test_git_limits_positive(self):
        assert _GIT_HISTORY_LIMIT > 0
        assert _GIT_LOG_TIMEOUT > 0

    def test_max_changed_files_positive(self):
        assert _MAX_CHANGED_FILES > 0

    def test_docstring_max_width_positive(self):
        assert _DOCSTRING_MAX_WIDTH > 0

    def test_background_interval_positive(self):
        assert _BACKGROUND_INTERVAL > 0

    def test_hash_digest_len_valid(self):
        assert 0 < _HASH_DIGEST_LEN <= 64  # SHA-256 produces 64 hex chars

    def test_chunk_chars_consistent(self):
        assert MAX_TEXT_CHUNK_CHARS > 0
