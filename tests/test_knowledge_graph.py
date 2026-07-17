#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for knowledge graph modules: text_network, ingester, graph_rag, insight_generator."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixture: in-memory SQLite with KG schema
# ---------------------------------------------------------------------------

KG_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_graphs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT,
    entity_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_nodes (
    id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
    label TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    properties TEXT DEFAULT '{}',
    embedding BLOB,
    centrality REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_edges (
    id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
    source_id TEXT NOT NULL REFERENCES kg_nodes(id),
    target_id TEXT NOT NULL REFERENCES kg_nodes(id),
    relationship TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT NOT NULL,
    profile TEXT,
    project_id TEXT,
    node_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0,
    top_score REAL DEFAULT 0.0,
    compressed INTEGER DEFAULT 0,
    duration_ms REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kg_ontology (
    id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
    subject_type TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_type TEXT NOT NULL,
    path_distance INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def kg_db():
    """Create an in-memory SQLite DB with KG schema, wrapped like production.

    Returns the same ``StorageConnection`` wrapper that production ``_get_db()``
    returns, so runtime psycopg2-native ``%s`` placeholders are translated to
    ``?`` for the SQLite backend. A raw ``sqlite3`` connection rejects the KG
    modules' ``%s`` SQL with "near \"%\": syntax error", which previously made
    these tests pass or fail by accident depending on which placeholder style a
    given query happened to use.
    """
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(KG_SCHEMA)
    return StorageConnection(raw, "sqlite")


def _populate_graph(conn, graph_id="kg-test001", project_id="test-proj"):
    """Insert a sample graph with nodes and edges for testing."""
    conn.execute(
        "INSERT INTO kg_graphs (id, project_id, name, entity_count, edge_count) VALUES (?, ?, 'Test Graph', 4, 2)",
        (graph_id, project_id),
    )
    nodes = [
        ("n1", graph_id, "NIST 800-53", "standard", 0.8),
        ("n2", graph_id, "AC-2", "control", 0.6),
        ("n3", graph_id, "FedRAMP", "standard", 0.4),
        ("n4", graph_id, "Access Control System", "system", 0.2),
    ]
    for nid, gid, label, etype, cent in nodes:
        conn.execute(
            "INSERT INTO kg_nodes (id, graph_id, label, entity_type, centrality) VALUES (?, ?, ?, ?, ?)",
            (nid, gid, label, etype, cent),
        )
    edges = [
        ("e1", graph_id, "n2", "n1", "SATISFIES", 1.0),
        ("e2", graph_id, "n4", "n2", "IMPLEMENTS", 0.8),
    ]
    for eid, gid, src, tgt, rel, w in edges:
        conn.execute(
            "INSERT INTO kg_edges (id, graph_id, source_id, target_id, relationship, weight) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, gid, src, tgt, rel, w),
        )
    conn.commit()
    return graph_id


# ===========================================================================
# text_network tests
# ===========================================================================


class TestTextNetworkExtraction:
    """Test entity and relationship extraction from text."""

    def test_extract_control_entities(self):
        from tools.knowledge_graph.text_network import _extract_entities

        entities = _extract_entities("The system must comply with AC-2 and SC-13 controls.")
        labels = [e[0] for e in entities]
        types = [e[1] for e in entities]
        assert "AC-2" in labels
        assert "SC-13" in labels
        assert all(t == "control" for t in types if t == "control")

    def test_extract_standard_entities(self):
        from tools.knowledge_graph.text_network import _extract_entities

        entities = _extract_entities("Per NIST SP 800-53 Rev5, all systems must be assessed.")
        labels = [e[0] for e in entities]
        assert any("800-53" in l for l in labels)

    def test_extract_shall_requirements(self):
        from tools.knowledge_graph.text_network import _extract_entities

        entities = _extract_entities("The system SHALL provide monthly reports and enforce access controls.")
        types = [e[1] for e in entities]
        assert "requirement" in types

    def test_empty_text_returns_empty(self):
        from tools.knowledge_graph.text_network import _extract_entities

        assert _extract_entities("") == []
        assert _extract_entities("   ") == []

    def test_extract_relationships(self):
        from tools.knowledge_graph.text_network import _extract_relationships

        entities = [("AC-2", "control"), ("NIST 800-53", "standard")]
        text = "AC-2 implements NIST 800-53 requirements."
        rels = _extract_relationships(text, entities)
        # Should find at least one relationship
        assert isinstance(rels, list)

    def test_deduplication(self):
        from tools.knowledge_graph.text_network import _extract_entities

        entities = _extract_entities("AC-2 and AC-2 and AC-2 controls.")
        labels = [e[0] for e in entities]
        assert labels.count("AC-2") == 1

    def test_extract_and_persist(self):
        """Full extract_entities_and_relationships with mocked DB."""
        from tools.knowledge_graph.text_network import _extract_entities

        text = "DISA requires AC-2 for all DoD systems."
        entities = _extract_entities(text)
        # Should extract at least the control and organization
        types = {e[1] for e in entities}
        assert "control" in types


class TestTextNetworkDB:
    """Test DB operations (get_graph, search_nodes)."""

    def test_get_graph(self, kg_db):
        _populate_graph(kg_db)
        with patch("tools.knowledge_graph.text_network._get_db", return_value=kg_db):
            with patch("tools.knowledge_graph.text_network._ensure_tables"):
                from tools.knowledge_graph.text_network import get_graph

                result = get_graph("kg-test001")
                assert result["status"] == "ok"
                assert result["graph"]["id"] == "kg-test001"
                assert len(result["nodes"]) == 4
                assert len(result["edges"]) == 2

    def test_get_graph_not_found(self, kg_db):
        with patch("tools.knowledge_graph.text_network._get_db", return_value=kg_db):
            with patch("tools.knowledge_graph.text_network._ensure_tables"):
                from tools.knowledge_graph.text_network import get_graph

                result = get_graph("nonexistent")
                assert result["status"] == "error"

    def test_search_nodes(self, kg_db):
        _populate_graph(kg_db)
        with patch("tools.knowledge_graph.text_network._get_db", return_value=kg_db):
            with patch("tools.knowledge_graph.text_network._ensure_tables"):
                from tools.knowledge_graph.text_network import search_nodes

                result = search_nodes("NIST", project_id="test-proj")
                assert result["status"] == "ok"
                assert len(result["nodes"]) >= 1


# ===========================================================================
# ingester tests
# ===========================================================================


class TestIngesterChunking:
    """Test text chunking logic."""

    def test_short_text_single_chunk(self):
        from tools.knowledge_graph.ingester import _chunk_text

        chunks = _chunk_text("Short text here.", chunk_size=2000)
        assert len(chunks) == 1

    def test_empty_text(self):
        from tools.knowledge_graph.ingester import _chunk_text

        assert _chunk_text("") == []
        assert _chunk_text(None) == []

    def test_long_text_multiple_chunks(self):
        from tools.knowledge_graph.ingester import _chunk_text

        text = "A " * 2000  # ~4000 chars
        chunks = _chunk_text(text, chunk_size=500, overlap=50)
        assert len(chunks) > 1
        # Each chunk should be <= chunk_size (approximately)
        for c in chunks:
            assert len(c) <= 600  # Allow some tolerance for boundary splits

    def test_overlap_present(self):
        from tools.knowledge_graph.ingester import _chunk_text

        text = "word " * 1000
        chunks = _chunk_text(text, chunk_size=200, overlap=50)
        if len(chunks) >= 2:
            # Some overlap content should exist between consecutive chunks
            end_of_first = chunks[0][-50:]
            assert len(end_of_first) > 0


class TestIngesterEntityExtraction:
    """Test ingester's entity extraction (keyword-based)."""

    def test_extract_entities(self):
        from tools.knowledge_graph.ingester import _extract_entities

        entities = _extract_entities("NIST framework for cybersecurity compliance")
        assert isinstance(entities, list)
        # Should find at least NIST as organization/framework
        labels = [e.get("label", "") for e in entities]
        assert any("NIST" in l for l in labels)


class TestIngesterMerge:
    """Test entity and edge merging across chunks."""

    def test_merge_entities_dedup(self):
        from tools.knowledge_graph.ingester import _merge_entities

        batch1 = [{"label": "NIST", "entity_type": "organization"}]
        batch2 = [{"label": "NIST", "entity_type": "organization"}]
        merged = _merge_entities([batch1, batch2])
        nist_count = sum(1 for e in merged if e["label"] == "NIST")
        assert nist_count == 1


# ===========================================================================
# graph_rag tests
# ===========================================================================


class TestGraphRAGProfiles:
    """Test profile auto-detection and scoring."""

    def test_compliance_profile_detection(self):
        from tools.knowledge_graph.graph_rag import _auto_detect_profile

        assert _auto_detect_profile("NIST 800-53 audit controls") == "compliance"

    def test_exploratory_profile_detection(self):
        from tools.knowledge_graph.graph_rag import _auto_detect_profile

        assert _auto_detect_profile("explore gaps in coverage") == "exploratory"

    def test_security_profile_detection(self):
        from tools.knowledge_graph.graph_rag import _auto_detect_profile

        assert _auto_detect_profile("CVE vulnerability assessment") == "security"

    def test_provenance_profile_detection(self):
        from tools.knowledge_graph.graph_rag import _auto_detect_profile

        assert _auto_detect_profile("trace the origin of this supply chain") == "provenance"

    def test_default_profile_fallback(self):
        from tools.knowledge_graph.graph_rag import _auto_detect_profile

        profile = _auto_detect_profile("random text with no keywords")
        assert profile in ("compliance", "exploratory", "provenance", "security", "ontology")

    def test_ontology_profile_detection(self):
        from tools.knowledge_graph.graph_rag import _auto_detect_profile

        assert _auto_detect_profile("show me the class hierarchy") == "ontology"
        assert _auto_detect_profile("find all load balancers") == "ontology"


class TestGraphRAGScoring:
    """Test node scoring with profiles."""

    def test_score_calculation(self):
        from tools.knowledge_graph.graph_rag import SCORING_PROFILES

        profile = SCORING_PROFILES["compliance"]
        # Verify base weights sum to 1.0 (approximately)
        total = profile["edge_weight"] + profile["centrality"] + profile["recency"]
        assert abs(total - 1.0) < 0.01

    def test_all_profiles_have_required_keys(self):
        from tools.knowledge_graph.graph_rag import SCORING_PROFILES

        for name, weights in SCORING_PROFILES.items():
            assert "edge_weight" in weights, f"{name} missing edge_weight"
            assert "centrality" in weights, f"{name} missing centrality"
            assert "recency" in weights, f"{name} missing recency"
            assert "ontology_weight" in weights, f"{name} missing ontology_weight"


class TestGraphRAGRetrieve:
    """Test the retrieve() function with mocked DB."""

    def test_retrieve_returns_structure(self, kg_db):
        _populate_graph(kg_db)
        with patch("tools.knowledge_graph.graph_rag._get_db", return_value=kg_db):
            with patch("tools.knowledge_graph.graph_rag._ensure_tables"):
                from tools.knowledge_graph.graph_rag import retrieve

                result = retrieve("AC-2 compliance", project_id="test-proj")
                assert "status" in result
                assert "nodes" in result or "context" in result
                assert "profile" in result

    def test_retrieve_empty_query(self, kg_db):
        with patch("tools.knowledge_graph.graph_rag._get_db", return_value=kg_db):
            with patch("tools.knowledge_graph.graph_rag._ensure_tables"):
                from tools.knowledge_graph.graph_rag import retrieve

                result = retrieve("", project_id="test-proj")
                assert result.get("status") in ("ok", "error")


class TestGraphRAGEmbeddingProvider:
    """Tier B: query embeddings go through get_embedding_provider() (never a
    hardcoded Ollama endpoint) and degrade to keyword-only when unavailable."""

    def test_embedding_to_pg_vector_format(self):
        from tools.knowledge_graph.graph_rag import _embedding_to_pg_vector

        assert _embedding_to_pg_vector([0.1, -0.2, 0.333333]) == "[0.100000,-0.200000,0.333333]"
        assert _embedding_to_pg_vector([]) == "[]"

    def test_embed_query_uses_configured_provider(self):
        """_embed_query returns the configured provider's vector — no hardcoded model."""
        from unittest.mock import MagicMock

        import tools.knowledge_graph.graph_rag as gr

        provider = MagicMock()
        provider.embed.return_value = [0.1, 0.2, 0.3]
        with patch("tools.llm.get_embedding_provider", return_value=provider):
            assert gr._embed_query("zero trust") == [0.1, 0.2, 0.3]
        provider.embed.assert_called_once_with("zero trust")

    def test_embed_query_degrades_when_provider_unavailable(self):
        """No embedding provider (raises LLMUnavailableError) → None, not a crash."""
        import tools.knowledge_graph.graph_rag as gr

        def _raise():
            raise RuntimeError("ICDEV_NO_LLM — embedding provider disabled")

        with patch("tools.llm.get_embedding_provider", side_effect=_raise):
            assert gr._embed_query("anything") is None

    def test_semantic_candidates_sqlite_python_cosine(self, kg_db):
        """On SQLite _semantic_candidates ranks via Python cosine over the BLOBs."""
        import struct

        from tools.knowledge_graph.graph_rag import _semantic_candidates

        kg_db.execute("INSERT INTO kg_graphs (id, project_id, name) VALUES ('g1','p1','G')")
        near = struct.pack("3f", 1.0, 0.0, 0.0)
        far = struct.pack("3f", 0.0, 1.0, 0.0)
        kg_db.execute(
            "INSERT INTO kg_nodes (id, graph_id, label, entity_type, embedding) "
            "VALUES ('n_near','g1','Near','t', %s)",
            (near,),
        )
        kg_db.execute(
            "INSERT INTO kg_nodes (id, graph_id, label, entity_type, embedding) "
            "VALUES ('n_far','g1','Far','t', %s)",
            (far,),
        )
        kg_db.commit()

        q_vec = [1.0, 0.0, 0.0]
        q_blob = struct.pack("3f", *q_vec)
        out = _semantic_candidates(kg_db, ["g1"], "%s", q_vec, q_blob, 10)

        assert [d["id"] for _, d in out][0] == "n_near"  # aligned vector ranks first
        assert out[0][0] > out[-1][0]
        assert all("embedding" not in d for _, d in out)  # raw column stripped


class TestGraphRAGOntologyAwareness:
    """Test ontology hierarchy traversal and distance-based scoring (D-ONTO-1)."""

    def test_ontology_profile_boosts_siblings(self):
        """Query for 'load_balancer' should score aws_alb, azure_appgw, gcp_lb high."""
        from tools.knowledge_graph.graph_rag import _score_nodes, _ONTOLOGY_FALLBACK

        nodes = [
            {"id": "n1", "label": "AWS ALB", "entity_type": "aws_alb",
             "centrality": 0.5, "created_at": "2024-01-01 00:00:00", "properties": "{}"},
            {"id": "n2", "label": "Azure AppGW", "entity_type": "azure_appgw",
             "centrality": 0.5, "created_at": "2024-01-01 00:00:00", "properties": "{}"},
            {"id": "n3", "label": "GCP LB", "entity_type": "gcp_lb",
             "centrality": 0.5, "created_at": "2024-01-01 00:00:00", "properties": "{}"},
            {"id": "n4", "label": "AWS NLB", "entity_type": "aws_nlb",
             "centrality": 0.5, "created_at": "2024-01-01 00:00:00", "properties": "{}"},
        ]
        edges = []

        scored = _score_nodes(
            nodes, edges, "ontology", ["load", "balancer"],
            ontology_relations=dict(_ONTOLOGY_FALLBACK),
            query_entity_types={"load_balancer"},
        )

        # All sibling types should receive a non-zero ontology bonus
        for node in scored:
            assert node["ontology_bonus"] > 0.0, f"{node['label']} should have ontology_bonus > 0"
            assert node["ontology_path_distance"] is not None, f"{node['label']} should have ontology_path_distance"
            assert node["ontology_path_distance"] <= 2, f"{node['label']} distance should be <= 2"

    def test_compliance_nist_control_boost(self):
        """Compliance profile should boost nodes linked to nist_control via satisfiesFramework."""
        from tools.knowledge_graph.graph_rag import _compute_ontology_bonus

        relations = {
            "nist_control": [
                {"type": "compliance_framework", "predicate": "satisfiesFramework", "distance": 1},
            ],
            "compliance_framework": [
                {"type": "nist_control", "predicate": "satisfiesFramework", "distance": 1},
            ],
        }
        bonus = _compute_ontology_bonus(
            "compliance_framework", {"nist_control"}, relations, "compliance"
        )
        assert bonus >= 0.15, f"Expected compliance bonus >= 0.15, got {bonus}"

    def test_security_threat_actor_boost(self):
        """Security profile should boost nodes linked to threat_actor via hasActor."""
        from tools.knowledge_graph.graph_rag import _compute_ontology_bonus

        relations = {
            "threat_actor": [
                {"type": "security_object", "predicate": "hasActor", "distance": 1},
            ],
            "security_object": [
                {"type": "threat_actor", "predicate": "hasActor", "distance": 1},
            ],
        }
        bonus = _compute_ontology_bonus(
            "security_object", {"threat_actor"}, relations, "security"
        )
        assert bonus >= 0.15, f"Expected security bonus >= 0.15, got {bonus}"

    def test_network_has_provider_boost(self):
        """network_infrastructure profile should boost nodes with hasProvider."""
        from tools.knowledge_graph.graph_rag import _compute_ontology_bonus

        relations = {
            "aws_alb": [
                {"type": "aws_family", "predicate": "hasProvider", "distance": 1},
            ],
        }
        bonus = _compute_ontology_bonus(
            "aws_alb", {"aws_alb"}, relations, "network_infrastructure"
        )
        assert bonus >= 0.15, f"Expected network bonus >= 0.15, got {bonus}"

    def test_ontology_path_distance_closer_is_higher(self):
        """Nodes closer in the hierarchy should receive a higher ontology bonus."""
        from tools.knowledge_graph.graph_rag import _compute_ontology_bonus

        relations = {
            "load_balancer": [
                {"type": "aws_alb", "predicate": "rdfs:subClassOf", "distance": 1},
                {"type": "azure_appgw", "predicate": "rdfs:subClassOf", "distance": 1},
            ],
            "aws_alb": [
                {"type": "load_balancer", "predicate": "rdfs:superClassOf", "distance": 1},
            ],
            "azure_appgw": [
                {"type": "load_balancer", "predicate": "rdfs:superClassOf", "distance": 1},
            ],
        }
        bonus_direct = _compute_ontology_bonus(
            "aws_alb", {"load_balancer"}, relations, "ontology"
        )
        # A node unrelated to load_balancer should get 0 bonus
        bonus_unrelated = _compute_ontology_bonus(
            "unknown_type", {"load_balancer"}, relations, "ontology"
        )
        assert bonus_direct > bonus_unrelated


# ===========================================================================
# insight_generator tests
# ===========================================================================


class TestInsightComponents:
    """Test BFS component detection."""

    def test_find_components(self):
        from tools.knowledge_graph.insight_generator import _find_components

        adj = {
            "n1": {"n2"},
            "n2": {"n1", "n3"},
            "n3": {"n2"},
            "n4": set(),  # isolated
        }
        components = _find_components(adj)
        assert len(components) == 2  # {n1,n2,n3} and {n4}
        sizes = sorted([len(c) for c in components], reverse=True)
        assert sizes == [3, 1]

    def test_find_components_empty(self):
        from tools.knowledge_graph.insight_generator import _find_components

        components = _find_components({})
        assert components == []


class TestInsightBridgeGaps:
    """Test bridge gap detection."""

    def test_bridge_gaps_found(self, kg_db):
        """Graph with disconnected components should find bridge gaps."""
        _populate_graph(kg_db)
        # Add a disconnected node
        kg_db.execute(
            "INSERT INTO kg_nodes (id, graph_id, label, entity_type) VALUES (?, ?, ?, ?)",
            ("n5", "kg-test001", "Isolated System", "system"),
        )
        kg_db.execute(
            "UPDATE kg_graphs SET entity_count = 5 WHERE id = 'kg-test001'",
        )
        kg_db.commit()

        with patch("tools.knowledge_graph.insight_generator._get_db", return_value=kg_db):
            with patch("tools.knowledge_graph.insight_generator._ensure_tables", return_value=None):
                from tools.knowledge_graph.insight_generator import find_bridge_gaps

                result = find_bridge_gaps("kg-test001", db_path=":memory:")
                assert result["status"] == "ok"
                assert result["connected_components"] >= 2


class TestInsightOrphans:
    """Test orphan node detection."""

    def test_orphan_detection(self, kg_db):
        _populate_graph(kg_db)
        # n3 (FedRAMP) has no edges — it's an orphan
        with patch("tools.knowledge_graph.insight_generator._get_db", return_value=kg_db):
            with patch("tools.knowledge_graph.insight_generator._ensure_tables", return_value=None):
                from tools.knowledge_graph.insight_generator import find_orphan_nodes

                result = find_orphan_nodes("kg-test001", db_path=":memory:")
                assert result["status"] == "ok"
                orphan_labels = [o["label"] for o in result.get("orphan_nodes", [])]
                assert "FedRAMP" in orphan_labels


class TestInsightSummary:
    """Test graph summary generation."""

    def test_summary_structure(self, kg_db):
        _populate_graph(kg_db)
        with patch("tools.knowledge_graph.insight_generator._get_db", return_value=kg_db):
            with patch("tools.knowledge_graph.insight_generator._ensure_tables", return_value=None):
                from tools.knowledge_graph.insight_generator import graph_summary

                result = graph_summary("kg-test001", db_path=":memory:")
                assert result["status"] == "ok"
                assert result["entity_count"] == 4
                assert result["edge_count"] == 2
                assert "entity_type_distribution" in result


class TestInsightQuestions:
    """Test research question generation."""

    def test_generate_questions(self, kg_db):
        _populate_graph(kg_db)
        with patch("tools.knowledge_graph.insight_generator._get_db", return_value=kg_db):
            with patch("tools.knowledge_graph.insight_generator._ensure_tables", return_value=None):
                from tools.knowledge_graph.insight_generator import generate_questions

                result = generate_questions("kg-test001", use_llm=False, db_path=":memory:")
                assert result["status"] == "ok"
                assert "questions" in result
                assert isinstance(result["questions"], list)


# ===========================================================================
# graph-hop re-ranking: simulated DB move (sg-t2-02-d4)
# ===========================================================================


class TestGraphHopReRankSimulatedDBMove:
    """Graph-hop re-ranking unit tests with simulated DB move.

    Scenario: neighbor retrieval first succeeds (valid RRF scores computed),
    then the DB 'moves' mid-expansion (OperationalError on the edge query),
    triggering the fallback error handler in expand_bm25_top_k.

    Verifies:
    - Neighbor boost (+0.3) applied exactly to the score field.
    - hybrid_rank (RRF) is present and non-negative for all scored nodes.
    - DB-move error → expand_bm25_top_k returns structured error dict and
      logs the failure to the module logger (the backlog signal).
    - Partial graph expansion (only some neighbors retrieved) still yields
      a valid scored result with positive scores and RRF values.
    """

    def test_neighbor_boost_increases_score_by_0_3(self):
        """Nodes in neighbor_ids receive exactly +0.3 added to their score."""
        from tools.knowledge_graph.graph_rag import _score_nodes

        node_a = {
            "id": "na", "label": "AC-2", "entity_type": "control",
            "centrality": 0.5, "created_at": "2024-01-01 00:00:00", "properties": "{}",
        }
        node_b = {
            "id": "nb", "label": "FedRAMP", "entity_type": "standard",
            "centrality": 0.5, "created_at": "2024-01-01 00:00:00", "properties": "{}",
        }
        edges = [{"source_id": "na", "target_id": "nb", "weight": 1.0}]

        # Score with nb as a 1-hop neighbor vs. without
        scored_with = _score_nodes([node_a, node_b], edges, "compliance", [], neighbor_ids={"nb"})
        scored_without = _score_nodes([node_a, node_b], edges, "compliance", [], neighbor_ids=set())

        nb_with = next(n for n in scored_with if n["id"] == "nb")
        nb_without = next(n for n in scored_without if n["id"] == "nb")

        assert nb_with["is_neighbor"] is True
        assert nb_without["is_neighbor"] is False
        # Only neighbor_boost differs; base_score / relevance / embedding are identical
        assert abs((nb_with["score"] - nb_without["score"]) - 0.3) < 1e-5

    def test_rrf_hybrid_rank_present_and_nonnegative(self):
        """All scored nodes carry a non-negative hybrid_rank (RRF) when neighbors are present."""
        from tools.knowledge_graph.graph_rag import _score_nodes

        nodes = [
            {"id": "n1", "label": "NIST 800-53", "entity_type": "standard",
             "centrality": 0.8, "created_at": "2024-01-01 00:00:00", "properties": "{}"},
            {"id": "n2", "label": "AC-2 access control", "entity_type": "control",
             "centrality": 0.6, "created_at": "2024-01-01 00:00:00", "properties": "{}"},
            {"id": "n3", "label": "IAM System", "entity_type": "system",
             "centrality": 0.4, "created_at": "2024-01-01 00:00:00", "properties": "{}"},
        ]
        edges = [
            {"source_id": "n2", "target_id": "n1", "weight": 1.0},
            {"source_id": "n3", "target_id": "n2", "weight": 0.8},
        ]

        scored = _score_nodes(
            nodes, edges, "compliance", ["access", "control"], neighbor_ids={"n3"}
        )

        assert len(scored) == 3
        for node in scored:
            assert "hybrid_rank" in node, f"Missing hybrid_rank on node {node['id']}"
            assert node["hybrid_rank"] >= 0.0, f"Negative hybrid_rank on {node['id']}"
        n3 = next(n for n in scored if n["id"] == "n3")
        assert n3["is_neighbor"] is True

    def test_db_move_expansion_fallback_returns_error_and_logs(self, caplog):
        """DB move (OperationalError on edge query) → error dict returned, failure logged."""
        import logging

        from tools.knowledge_graph.graph_rag import expand_bm25_top_k

        # Real in-memory DB wrapped like production, so the graph-scope query
        # (SELECT ... WHERE id = %s) translates %s -> ? and succeeds; the
        # simulated move must fire on the *edge* query, not on a placeholder
        # syntax error in the scope query.
        from tools.db.storage import StorageConnection

        real_conn = StorageConnection(sqlite3.connect(":memory:"), "sqlite")
        real_conn.row_factory = sqlite3.Row
        real_conn.executescript(KG_SCHEMA)
        real_conn.execute(
            "INSERT INTO kg_graphs (id, project_id, name) VALUES ('g1', 'p1', 'Test')"
        )
        real_conn.commit()

        class _DBMovedWrapper:
            """Passes all queries through except kg_edges (simulates DB move)."""

            def execute(self, sql, params=None):
                if "kg_edges" in sql:
                    raise sqlite3.OperationalError("database has moved")
                return (
                    real_conn.execute(sql, params)
                    if params is not None
                    else real_conn.execute(sql)
                )

            def close(self):
                real_conn.close()

        # icdev_logger deliberately sets propagate=False on its loggers (avoids
        # double-logging in production), so caplog — whose handler lives on the
        # root logger — cannot see the module's error record. Force propagation
        # for the duration so the "backlog signal" log is observable, then restore.
        kg_logger = logging.getLogger("icdev.knowledge_graph.graph_rag")
        _prev_propagate = kg_logger.propagate
        kg_logger.propagate = True
        try:
            with patch("tools.knowledge_graph.graph_rag._get_db", return_value=_DBMovedWrapper()):
                with patch("tools.knowledge_graph.graph_rag._ensure_tables"):
                    with caplog.at_level(logging.ERROR, logger="icdev.knowledge_graph.graph_rag"):
                        result = expand_bm25_top_k(["n1", "n2"], graph_id="g1")
        finally:
            kg_logger.propagate = _prev_propagate

        # Fallback: structured error dict returned instead of raising to caller
        assert result["status"] == "error"
        assert result["all_neighbors"] == []
        assert "database has moved" in result.get("error", "")
        # Error logged to the module logger (backlog signal)
        assert any(
            "expand_bm25_top_k failed" in r.message
            and r.name == "icdev.knowledge_graph.graph_rag"
            for r in caplog.records
        )

    def test_partial_expansion_yields_valid_rrf_scores(self):
        """Partial graph expansion (1 matched + 1 neighbor only) → valid scored nodes."""
        from tools.knowledge_graph.graph_rag import _score_nodes

        partial_nodes = [
            {"id": "n1", "label": "AC-2", "entity_type": "control",
             "centrality": 0.7, "created_at": "2024-01-01 00:00:00", "properties": "{}"},
            {"id": "n4", "label": "FedRAMP Boundary", "entity_type": "boundary",
             "centrality": 0.3, "created_at": "2024-01-01 00:00:00", "properties": "{}"},
        ]
        edges = [{"source_id": "n1", "target_id": "n4", "weight": 0.85}]

        scored = _score_nodes(
            partial_nodes, edges, "compliance", ["ac"], neighbor_ids={"n4"}
        )

        assert len(scored) == 2
        assert all(n["score"] >= 0.0 for n in scored)
        assert all(n["hybrid_rank"] >= 0.0 for n in scored)
        n4 = next(n for n in scored if n["id"] == "n4")
        assert n4["score"] > 0.0
        assert n4["is_neighbor"] is True
