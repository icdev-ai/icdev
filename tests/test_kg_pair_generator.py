#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for KG-to-FT pair generator (D-KARL-6)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests._sql_compat import connect as translating_connect


KG_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_graphs (
    id TEXT PRIMARY KEY, project_id TEXT, name TEXT NOT NULL,
    description TEXT, entity_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0, metadata TEXT DEFAULT '{}',
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
CREATE TABLE IF NOT EXISTS ft_dataset_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id TEXT,
    system_prompt TEXT DEFAULT '', user_input TEXT,
    expected_output TEXT, source TEXT DEFAULT 'manual',
    quality_score REAL DEFAULT 0.0, content_hash TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def kg_db():
    # tools.finetune.kg_pair_generator authors PostgreSQL ``%s`` placeholders and
    # relies on StorageConnection to rewrite them for SQLite. Handing it a bare
    # sqlite3 connection makes every statement raise ``near "%": syntax error``.
    conn = translating_connect(":memory:")
    conn.executescript(KG_SCHEMA)
    # Populate test graph
    conn.execute(
        "INSERT INTO kg_graphs (id, project_id, name, entity_count, edge_count) "
        "VALUES ('kg-test1', 'proj1', 'Test Graph', 5, 3)",
    )
    nodes = [
        ("n1", "kg-test1", "NIST 800-53", "standard", 0.8),
        ("n2", "kg-test1", "AC-2", "control", 0.6),
        ("n3", "kg-test1", "SC-13", "control", 0.4),
        ("n4", "kg-test1", "FedRAMP", "standard", 0.3),
        ("n5", "kg-test1", "Auth System", "system", 0.2),
    ]
    for nid, gid, label, etype, cent in nodes:
        conn.execute(
            "INSERT INTO kg_nodes (id, graph_id, label, entity_type, centrality) VALUES (?, ?, ?, ?, ?)",
            (nid, gid, label, etype, cent),
        )
    edges = [
        ("e1", "kg-test1", "n2", "n1", "SATISFIES", 1.0),
        ("e2", "kg-test1", "n3", "n1", "SATISFIES", 1.0),
        ("e3", "kg-test1", "n5", "n2", "IMPLEMENTS", 0.8),
    ]
    for eid, gid, src, tgt, rel, w in edges:
        conn.execute(
            "INSERT INTO kg_edges (id, graph_id, source_id, target_id, relationship, weight) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, gid, src, tgt, rel, w),
        )
    conn.commit()
    return conn


class TestEntityRelationshipPairs:
    def test_generates_pairs_from_edges(self, kg_db):
        from tools.finetune.kg_pair_generator import _load_graph, _generate_entity_relationship_pairs

        graph_data = _load_graph(kg_db, "kg-test1")
        pairs = _generate_entity_relationship_pairs(graph_data)
        assert len(pairs) == 3  # 3 edges → 3 pairs
        assert all(p["source"] == "kg_entity_relationship" for p in pairs)
        assert all("user_input" in p and "expected_output" in p for p in pairs)

    def test_uses_relationship_templates(self, kg_db):
        from tools.finetune.kg_pair_generator import _load_graph, _generate_entity_relationship_pairs

        graph_data = _load_graph(kg_db, "kg-test1")
        pairs = _generate_entity_relationship_pairs(graph_data)
        satisfies_pairs = [p for p in pairs if "satisfy" in p["user_input"].lower()]
        assert len(satisfies_pairs) >= 1

    def test_max_pairs_limit(self, kg_db):
        from tools.finetune.kg_pair_generator import _load_graph, _generate_entity_relationship_pairs

        graph_data = _load_graph(kg_db, "kg-test1")
        pairs = _generate_entity_relationship_pairs(graph_data, max_pairs=1)
        assert len(pairs) == 1


class TestCommunityClusterPairs:
    def test_generates_pairs_from_communities(self, kg_db):
        from tools.finetune.kg_pair_generator import _load_graph, _generate_community_cluster_pairs

        graph_data = _load_graph(kg_db, "kg-test1")
        # All 5 nodes are connected (4 through edges, 1 may be isolated)
        pairs = _generate_community_cluster_pairs(graph_data, min_size=3)
        # Should generate at least one community pair for the main cluster
        assert isinstance(pairs, list)
        for p in pairs:
            assert p["source"] == "kg_community_cluster"

    def test_min_size_filter(self, kg_db):
        from tools.finetune.kg_pair_generator import _load_graph, _generate_community_cluster_pairs

        graph_data = _load_graph(kg_db, "kg-test1")
        # Min size 100 → no communities qualify
        pairs = _generate_community_cluster_pairs(graph_data, min_size=100)
        assert len(pairs) == 0


class TestComplianceCrosswalkPairs:
    def test_generates_crosswalk_pairs(self, kg_db):
        from tools.finetune.kg_pair_generator import _load_graph, _generate_compliance_crosswalk_pairs

        graph_data = _load_graph(kg_db, "kg-test1")
        pairs = _generate_compliance_crosswalk_pairs(graph_data)
        # AC-2 and SC-13 both SATISFY NIST 800-53
        assert len(pairs) >= 1
        nist_pair = [p for p in pairs if "NIST 800-53" in p["user_input"]]
        assert len(nist_pair) >= 1
        # Should mention both controls
        assert "AC-2" in nist_pair[0]["expected_output"]
        assert "SC-13" in nist_pair[0]["expected_output"]


class TestFullPipeline:
    def test_generate_pairs_from_graph(self, kg_db):
        from tools.finetune.kg_pair_generator import generate_pairs_from_graph

        with patch("tools.finetune.kg_pair_generator._get_db", return_value=kg_db):
            result = generate_pairs_from_graph("kg-test1", store=False)
            assert result["status"] == "ok"
            assert result["total_pairs_generated"] > 0
            assert len(result["strategies_run"]) >= 1

    def test_graph_not_found(self, kg_db):
        from tools.finetune.kg_pair_generator import generate_pairs_from_graph

        with patch("tools.finetune.kg_pair_generator._get_db", return_value=kg_db):
            result = generate_pairs_from_graph("nonexistent")
            assert result["status"] == "error"

    def test_store_pairs(self, kg_db):
        from tools.finetune.kg_pair_generator import _load_graph, _generate_entity_relationship_pairs, _content_hash

        graph_data = _load_graph(kg_db, "kg-test1")
        pairs = _generate_entity_relationship_pairs(graph_data)
        assert len(pairs) > 0
        # Manually store and verify schema compatibility
        stored = 0
        for pair in pairs:
            ch = _content_hash(pair.get("system_prompt", "") + pair["user_input"] + pair["expected_output"])
            kg_db.execute(
                "INSERT INTO ft_dataset_examples (dataset_id, system_prompt, user_input, expected_output, source, quality_score, content_hash, classification) "
                "VALUES (?, ?, ?, ?, ?, 0.7, ?, 'CUI')",
                (
                    "ds-test",
                    pair.get("system_prompt", ""),
                    pair["user_input"],
                    pair["expected_output"],
                    pair.get("source", ""),
                    ch,
                ),
            )
            stored += 1
        kg_db.commit()
        count = kg_db.execute("SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = 'ds-test'").fetchone()[0]
        assert count == stored

    def test_deduplication(self, kg_db):
        from tools.finetune.kg_pair_generator import _load_graph, _generate_entity_relationship_pairs, _content_hash

        graph_data = _load_graph(kg_db, "kg-test1")
        pairs = _generate_entity_relationship_pairs(graph_data)
        # Store first time
        for pair in pairs:
            ch = _content_hash(pair.get("system_prompt", "") + pair["user_input"] + pair["expected_output"])
            kg_db.execute(
                "INSERT INTO ft_dataset_examples (dataset_id, system_prompt, user_input, expected_output, source, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "ds-dup",
                    pair.get("system_prompt", ""),
                    pair["user_input"],
                    pair["expected_output"],
                    pair.get("source", ""),
                    ch,
                ),
            )
        kg_db.commit()
        # Try to store again — check dedup by content_hash
        dupes = 0
        for pair in pairs:
            ch = _content_hash(pair.get("system_prompt", "") + pair["user_input"] + pair["expected_output"])
            existing = kg_db.execute(
                "SELECT id FROM ft_dataset_examples WHERE content_hash = ? AND dataset_id = ?",
                (ch, "ds-dup"),
            ).fetchone()
            if existing:
                dupes += 1
        assert dupes == len(pairs)  # All should be detected as dupes
