# CUI // SP-CTI
"""Fixture-driven tests for tools/rag/embedding_feasibility.py (rce-eval-02).

No live corpus, no DB file — an in-memory sqlite fixture stands in for the RCE
vector store so the suite is green in a fresh worktree.
"""
import sqlite3

import pytest

from tools.rag.embedding_feasibility import (
    DEFAULT_MIN_ELIGIBLE,
    assess_feasibility,
    build_report,
    corpus_stats,
)


def _make_store(rows):
    """In-memory rag_chunks with (source_type, count) rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE rag_chunks (id INTEGER PRIMARY KEY, source_type TEXT)")
    for source_type, count in rows:
        for _ in range(count):
            conn.execute("INSERT INTO rag_chunks (source_type) VALUES (?)", (source_type,))
    conn.commit()
    return conn


def test_corpus_stats_counts_and_splits_eligible():
    # Mirrors the real rce-eval-02 finding: research-heavy, zero compliance.
    conn = _make_store([("research_challenges", 1259), ("innovation_signals", 83)])
    stats = corpus_stats(conn)
    assert stats["total_chunks"] == 1342
    assert stats["eligible_chunks"] == 0
    assert stats["ineligible_chunks"] == 1342
    assert stats["eligible_fraction"] == 0.0
    # by_source_type is sorted descending
    assert list(stats["by_source_type"]) == ["research_challenges", "innovation_signals"]


def test_corpus_stats_recognizes_compliance_source_types():
    conn = _make_store([("dic_document", 300), ("research_challenges", 100)])
    stats = corpus_stats(conn)
    assert stats["eligible_chunks"] == 300
    assert stats["ineligible_chunks"] == 100
    assert stats["eligible_fraction"] == 0.75


def test_assess_insufficient_when_below_threshold():
    conn = _make_store([("compliance_artifacts", 10)])
    assessment = assess_feasibility(corpus_stats(conn), min_eligible=DEFAULT_MIN_ELIGIBLE)
    assert assessment["training_data_viable"] is False
    assert assessment["signal"] == "TRAIN-DATA-INSUFFICIENT"
    assert "DEFER" in assessment["recommendation"]


def test_assess_sufficient_when_above_threshold():
    conn = _make_store([("nist_controls", 2500)])
    assessment = assess_feasibility(corpus_stats(conn), min_eligible=2000)
    assert assessment["training_data_viable"] is True
    assert assessment["signal"] == "TRAIN-DATA-SUFFICIENT"


def test_build_report_shape_and_marking():
    conn = _make_store([("research_challenges", 50)])
    report = build_report(conn, min_eligible=2000)
    assert report["classification"] == "CUI // SP-CTI"
    assert report["card"] == "rce-eval-02"
    assert report["corpus"]["total_chunks"] == 50
    assert report["assessment"]["signal"] == "TRAIN-DATA-INSUFFICIENT"


def test_empty_corpus_is_safe():
    conn = _make_store([])
    stats = corpus_stats(conn)
    assert stats["total_chunks"] == 0
    assert stats["eligible_fraction"] == 0.0


def test_missing_db_path_raises():
    with pytest.raises(FileNotFoundError):
        corpus_stats("data/rag/does_not_exist_rce_eval_02.db")
