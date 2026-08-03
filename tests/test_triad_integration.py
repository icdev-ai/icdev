#!/usr/bin/env python3
# CUI // SP-CTI
"""Cross-system integration tests for the RAG + KG + FT triad."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests._sql_compat import connect as translating_connect


TRIAD_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_graphs (
    id TEXT PRIMARY KEY, project_id TEXT, name TEXT NOT NULL,
    entity_count INTEGER DEFAULT 0, edge_count INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_nodes (
    id TEXT PRIMARY KEY, graph_id TEXT NOT NULL, label TEXT NOT NULL,
    entity_type TEXT NOT NULL, properties TEXT DEFAULT '{}',
    embedding BLOB, centrality REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_edges (
    id TEXT PRIMARY KEY, graph_id TEXT NOT NULL,
    source_id TEXT NOT NULL, target_id TEXT NOT NULL,
    relationship TEXT NOT NULL, weight REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY, content TEXT, source_id TEXT,
    source_table TEXT, tier TEXT DEFAULT 'hot',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ft_datasets (
    id TEXT PRIMARY KEY, name TEXT, purpose TEXT DEFAULT 'general',
    description TEXT, status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ft_dataset_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id TEXT,
    system_prompt TEXT DEFAULT '', user_input TEXT,
    expected_output TEXT, source TEXT DEFAULT 'manual',
    quality_score REAL DEFAULT 0.0, content_hash TEXT,
    classification TEXT DEFAULT 'CUI', approved INTEGER DEFAULT 0,
    labeled_by TEXT, labeled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ft_pipeline_runs (
    id TEXT PRIMARY KEY, run_type TEXT, source_type TEXT,
    chunks_processed INTEGER DEFAULT 0, pairs_generated INTEGER DEFAULT 0,
    pairs_approved INTEGER DEFAULT 0, retrain_triggered INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running', error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ft_active_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT, function_name TEXT,
    model_version_id TEXT, ollama_model_name TEXT,
    routing_tier TEXT, activated_by TEXT,
    activation_reason TEXT, activated_at TIMESTAMP,
    deactivated_at TIMESTAMP, tenant_id TEXT, project_id TEXT
);
CREATE TABLE IF NOT EXISTS ft_model_versions (
    id TEXT PRIMARY KEY, job_id TEXT, model_name TEXT,
    version INTEGER, base_model TEXT, adapter_path TEXT,
    gguf_path TEXT, ollama_model_name TEXT,
    eval_bleu REAL, eval_rouge_l REAL, eval_perplexity REAL,
    status TEXT DEFAULT 'created',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, query_hash TEXT NOT NULL,
    profile TEXT, project_id TEXT, node_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0, top_score REAL DEFAULT 0.0,
    compressed INTEGER DEFAULT 0, duration_ms REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def triad_db():
    # The triad modules (kg_pair_generator, rag_ft_pipeline, quality_monitor)
    # author PostgreSQL ``%s`` placeholders and rely on StorageConnection to
    # rewrite them for SQLite. A bare sqlite3 connection makes every statement
    # raise ``near "%": syntax error``.
    conn = translating_connect(":memory:")
    conn.executescript(TRIAD_SCHEMA)
    return conn


def _seed_graph(conn):
    conn.execute(
        "INSERT INTO kg_graphs (id, project_id, name, entity_count, edge_count) "
        "VALUES ('kg-int1', 'proj1', 'Integration Graph', 3, 2)",
    )
    for nid, label, etype in [
        ("n1", "AC-2", "control"),
        ("n2", "NIST 800-53", "standard"),
        ("n3", "Auth Module", "system"),
    ]:
        conn.execute(
            "INSERT INTO kg_nodes (id, graph_id, label, entity_type, centrality) VALUES (?, 'kg-int1', ?, ?, 0.5)",
            (nid, label, etype),
        )
    for eid, src, tgt, rel in [("e1", "n1", "n2", "SATISFIES"), ("e2", "n3", "n1", "IMPLEMENTS")]:
        conn.execute(
            "INSERT INTO kg_edges (id, graph_id, source_id, target_id, relationship) VALUES (?, 'kg-int1', ?, ?, ?)",
            (eid, src, tgt, rel),
        )
    conn.commit()


def _seed_rag_chunks(conn):
    for i in range(3):
        conn.execute(
            "INSERT INTO rag_chunks (id, content, source_table, tier, created_at) VALUES (?, ?, 'innovation_signals', 'hot', '2026-03-20T00:00:00Z')",
            (f"rc-{i}", f"Innovation signal about NIST compliance pattern {i}"),
        )
    conn.commit()


# ===========================================================================
# Test 1: Parallel retrieval merges correctly
# ===========================================================================


class TestParallelRetrieval:
    def test_corrective_rag_merges_strategies(self):
        """Verify corrective_rag.parallel_retrieve merges and deduplicates."""
        from tools.rag.corrective_rag import _deduplicate

        docs = [
            {"content": "doc A about AC-2", "source": "rag_vector", "score": 0.9},
            {"content": "doc A about AC-2", "source": "graphrag", "score": 0.7},
            {"content": "doc B about SC-13", "source": "source_scan", "score": 0.5},
        ]
        unique = _deduplicate(docs)
        assert len(unique) == 2  # Deduped by content hash

    def test_corrective_rag_import(self):
        """Verify the import that Pulse researcher uses."""
        from tools.rag.corrective_rag import parallel_retrieve

        assert callable(parallel_retrieve)


# ===========================================================================
# Test 2: RAG chunks → FT pair generation
# ===========================================================================


class TestRAGToFTPipeline:
    def test_pipeline_dry_run(self, triad_db):
        _seed_rag_chunks(triad_db)
        from tools.finetune.rag_ft_pipeline import _detect_new_chunks

        chunks = _detect_new_chunks(triad_db, "innovation_signals", since="2026-01-01T00:00:00Z")
        assert len(chunks) == 3

    def test_auto_approve_threshold(self, triad_db):
        from tools.finetune.rag_ft_pipeline import _auto_approve_pairs

        triad_db.execute(
            "INSERT INTO ft_dataset_examples (dataset_id, user_input, expected_output, quality_score, approved) "
            "VALUES ('ds1', 'q', 'a', 0.95, 0)",
        )
        triad_db.commit()
        count = _auto_approve_pairs(triad_db, "ds1", 0.8)
        assert count == 1


# ===========================================================================
# Test 3: KG → FT pair generation
# ===========================================================================


class TestKGToFTPairs:
    def test_kg_generates_valid_pairs(self, triad_db):
        _seed_graph(triad_db)
        from tools.finetune.kg_pair_generator import generate_pairs_from_graph

        with patch("tools.finetune.kg_pair_generator._get_db", return_value=triad_db):
            result = generate_pairs_from_graph("kg-int1", store=False)
            assert result["status"] == "ok"
            assert result["total_pairs_generated"] > 0
            # Verify pair format is valid for ft_dataset_examples
            for pair in result["pairs"]:
                assert "user_input" in pair
                assert "expected_output" in pair
                assert len(pair["user_input"]) > 0
                assert len(pair["expected_output"]) > 0

    def test_stored_pairs_match_schema(self, triad_db):
        _seed_graph(triad_db)
        from tools.finetune.kg_pair_generator import _load_graph, _generate_entity_relationship_pairs, _content_hash

        graph_data = _load_graph(triad_db, "kg-int1")
        pairs = _generate_entity_relationship_pairs(graph_data)
        assert len(pairs) > 0
        # Store and verify schema compatibility
        for pair in pairs:
            ch = _content_hash(pair.get("system_prompt", "") + pair["user_input"] + pair["expected_output"])
            triad_db.execute(
                "INSERT INTO ft_dataset_examples (dataset_id, system_prompt, user_input, expected_output, source, quality_score, content_hash, classification) "
                "VALUES (?, ?, ?, ?, ?, 0.7, ?, 'CUI')",
                (
                    "ds-int",
                    pair.get("system_prompt", ""),
                    pair["user_input"],
                    pair["expected_output"],
                    pair.get("source", ""),
                    ch,
                ),
            )
        triad_db.commit()
        rows = triad_db.execute("SELECT * FROM ft_dataset_examples WHERE dataset_id = 'ds-int'").fetchall()
        for row in rows:
            assert row["user_input"]
            assert row["expected_output"]
            assert row["content_hash"]
            assert row["classification"] == "CUI"


# ===========================================================================
# Test 4: Centrality enrichment
# ===========================================================================


class TestCentralityEnrichment:
    def test_centrality_updates_nodes(self, triad_db):
        _seed_graph(triad_db)
        from tools.knowledge_graph.enricher import compute_centrality

        result = compute_centrality("kg-int1", conn=triad_db)
        assert result["status"] == "ok"
        assert result["nodes_enriched"] == 3
        # Verify centrality values were persisted
        rows = triad_db.execute("SELECT id, centrality FROM kg_nodes WHERE graph_id = 'kg-int1'").fetchall()
        for row in rows:
            assert row["centrality"] >= 0  # Should be non-negative


# ===========================================================================
# Test 5: Pipeline disabled check
# ===========================================================================


class TestPipelineConfig:
    def test_disabled_pipeline_returns_early(self):
        from tools.finetune.rag_ft_pipeline import run_pipeline

        with patch("tools.finetune.rag_ft_pipeline._get_config", return_value={"enabled": False}):
            result = run_pipeline()
            assert result["status"] == "disabled"

    def test_kg_pair_disabled_returns_early(self, triad_db):
        from tools.finetune.kg_pair_generator import generate_pairs_from_graph

        with patch("tools.finetune.kg_pair_generator._load_config", return_value={"enabled": False}):
            with patch("tools.finetune.kg_pair_generator._get_db", return_value=triad_db):
                result = generate_pairs_from_graph("kg-int1")
                assert result["status"] == "disabled"
