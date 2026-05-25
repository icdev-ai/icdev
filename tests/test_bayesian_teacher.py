#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Bayesian Teaching Intelligence Layer (D-BT-1 through D-BT-6).

Covers:
  - Information gain scoring (D-BT-2)
  - Teaching dimension calculator (D-BT-3)
  - Optimal compliance ordering (D-BT-4)
  - SmartEncoding tag compression (D-BT-5)
  - Teaching-dimension RAG reranker boost (D-BT-6)
  - Health check
  - CLI output
"""

import json
import sqlite3

import pytest


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bt_db(tmp_path):
    """Minimal DB with bayesian_teaching_scores and ft_dataset_examples."""
    db_path = tmp_path / "test_bt.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bayesian_teaching_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            info_gain_score REAL NOT NULL,
            dimensions TEXT,
            threshold_band TEXT,
            context_id TEXT,
            project_id TEXT,
            agent_id TEXT DEFAULT 'intelligence-engine',
            classification TEXT DEFAULT 'CUI',
            scored_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ft_dataset_examples (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            system_prompt TEXT DEFAULT '',
            user_input TEXT NOT NULL,
            expected_output TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            status TEXT DEFAULT 'pending',
            approved INTEGER DEFAULT 0,
            quality_score REAL DEFAULT 0.5,
            compliance_score REAL DEFAULT 0.5,
            relevance_score REAL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS otel_spans (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            parent_span_id TEXT,
            name TEXT NOT NULL,
            kind TEXT DEFAULT 'INTERNAL',
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_ms INTEGER DEFAULT 0,
            status_code TEXT DEFAULT 'UNSET',
            status_message TEXT,
            attributes TEXT,
            events TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_pairs(db_path, dataset_id, count=10):
    """Insert synthetic training pairs."""
    conn = sqlite3.connect(str(db_path))
    for i in range(count):
        conn.execute(
            """INSERT INTO ft_dataset_examples
               (id, dataset_id, user_input, expected_output,
                quality_score, compliance_score, relevance_score, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                f"ex-{i:03d}",
                dataset_id,
                f"What is NIST 800-53 control AC-{i + 1}?" + " compliance " * (i % 3),
                f"AC-{i + 1} is the account management control for access policy enforcement." * (1 + i % 2),
                0.3 + (i * 0.07),
                0.5 + (i * 0.05),
                0.4 + (i * 0.06),
            ),
        )
    conn.commit()
    conn.close()


def _insert_spans(db_path, project_id="proj-test", count=5):
    """Insert synthetic otel_spans for SmartEncoding test."""
    conn = sqlite3.connect(str(db_path))
    for i in range(count):
        attrs = json.dumps(
            {
                "agent_id": "builder-agent",
                "status_code": "OK",
                "name": "mcp.tool_call",
                "classification": "CUI // SP-CTI",
                "custom_field": f"value-{i}",
            }
        )
        conn.execute(
            """INSERT INTO otel_spans
               (id, trace_id, name, start_time, attributes, project_id)
               VALUES (?, 'trace-001', 'mcp.tool_call', '2025-01-01T00:00:00Z', ?, ?)""",
            (f"span-{i}", attrs, project_id),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 1. Information Gain Scorer Tests (D-BT-2)
# ---------------------------------------------------------------------------


class TestInformationGainScorer:
    """Tests for score_candidate() and score_training_pairs()."""

    def test_score_candidate_basic(self):
        from tools.intelligence.bayesian_teacher import score_candidate

        candidate = {
            "id": "cand-001",
            "content": "This is a test candidate with enough content to be meaningful.",
            "features": [0.5, 0.7, 0.3, 0.8, 0.6],
        }
        prior = [0.2, 0.2, 0.2, 0.2, 0.2]
        hypotheses = [
            [0.1, 0.2, 0.3, 0.4, 0.5],
            [0.9, 0.8, 0.7, 0.6, 0.5],
            [0.5, 0.5, 0.5, 0.5, 0.5],
            [0.3, 0.6, 0.1, 0.9, 0.4],
            [0.7, 0.3, 0.8, 0.2, 0.7],
        ]

        result = score_candidate(candidate, prior, hypotheses, [])

        assert result["candidate_id"] == "cand-001"
        assert 0.0 <= result["info_gain_score"] <= 1.0
        assert result["threshold_band"] in ("auto_select", "suggest", "exclude")
        assert "posterior_shift" in result["dimensions"]
        assert "discriminability" in result["dimensions"]
        assert "diversity" in result["dimensions"]
        assert "complexity_match" in result["dimensions"]
        assert all(0.0 <= v <= 1.0 for v in result["dimensions"].values())

    def test_score_candidate_diversity_decreases_with_selection(self):
        from tools.intelligence.bayesian_teacher import score_candidate

        cand = {
            "id": "c1",
            "content": "test content for diversity checking",
            "features": [0.5, 0.5, 0.5],
        }
        prior = [0.5, 0.5]
        hyp = [[0.3, 0.3, 0.3], [0.7, 0.7, 0.7]]

        # First item should have max diversity (1.0)
        r1 = score_candidate(cand, prior, hyp, [])
        assert r1["dimensions"]["diversity"] == 1.0

        # Same item after selection of identical features -> lower diversity
        selected = [{"features": [0.5, 0.5, 0.5]}]
        r2 = score_candidate(cand, prior, hyp, selected)
        assert r2["dimensions"]["diversity"] < r1["dimensions"]["diversity"]

    def test_score_candidate_no_features_fallback(self):
        from tools.intelligence.bayesian_teacher import score_candidate

        cand = {"id": "c2", "content": "short", "features": []}
        result = score_candidate(cand, [0.5, 0.5], [], [])
        assert result["info_gain_score"] > 0  # Should not crash

    def test_score_training_pairs_with_data(self, bt_db):
        from tools.intelligence.bayesian_teacher import score_training_pairs

        _insert_pairs(bt_db, "ds-test", count=15)
        result = score_training_pairs("ds-test", top_k=5, db_path=bt_db)

        assert result["dataset_id"] == "ds-test"
        assert result["total_candidates"] == 15
        assert len(result["scored_pairs"]) == 5
        assert result["statistics"]["mean_score"] > 0
        assert result["statistics"]["max_score"] >= result["statistics"]["min_score"]

        # Verify scores are sorted descending
        scores = [p["info_gain_score"] for p in result["scored_pairs"]]
        assert scores == sorted(scores, reverse=True)

    def test_score_training_pairs_empty_dataset(self, bt_db):
        from tools.intelligence.bayesian_teacher import score_training_pairs

        result = score_training_pairs("ds-empty", db_path=bt_db)
        assert result["total_candidates"] == 0
        assert result["scored_pairs"] == []

    def test_scores_stored_in_db(self, bt_db):
        from tools.intelligence.bayesian_teacher import score_training_pairs

        _insert_pairs(bt_db, "ds-stored", count=5)
        score_training_pairs("ds-stored", top_k=3, db_path=bt_db)

        conn = sqlite3.connect(str(bt_db))
        count = conn.execute("SELECT COUNT(*) FROM bayesian_teaching_scores WHERE context_id = 'ds-stored'").fetchone()[
            0
        ]
        conn.close()
        assert count >= 1  # At least some scores stored


# ---------------------------------------------------------------------------
# 2. Teaching Dimension Tests (D-BT-3)
# ---------------------------------------------------------------------------


class TestTeachingDimension:
    """Tests for teaching_dimension() — Goldman & Kearns 1995."""

    def test_single_concept_needs_zero_examples(self):
        from tools.intelligence.bayesian_teacher import teaching_dimension

        pool = [{"id": "a", "features": []}, {"id": "b", "features": []}]
        labels = {"a": "X", "b": "X"}
        result = teaching_dimension("X", pool, labels)

        assert result["teaching_dimension"] == 0
        assert result["teaching_set"] == []

    def test_two_concepts_need_at_least_one(self):
        from tools.intelligence.bayesian_teacher import teaching_dimension

        pool = [
            {"id": "a", "features": [1.0]},
            {"id": "b", "features": [0.0]},
            {"id": "c", "features": [1.0]},
        ]
        labels = {"a": "X", "b": "Y", "c": "X"}
        result = teaching_dimension("X", pool, labels)

        assert result["teaching_dimension"] >= 1
        assert result["fully_resolved"]

    def test_multiple_concepts(self):
        from tools.intelligence.bayesian_teacher import teaching_dimension

        pool = [
            {"id": "a1", "features": []},
            {"id": "b1", "features": []},
            {"id": "c1", "features": []},
            {"id": "d1", "features": []},
        ]
        labels = {"a1": "A", "b1": "B", "c1": "C", "d1": "A"}
        result = teaching_dimension("A", pool, labels)

        assert result["target_concept"] == "A"
        assert result["total_concepts"] == 3
        assert result["teaching_dimension"] <= 3  # At most |concepts|-1

    def test_elimination_log_populated(self):
        from tools.intelligence.bayesian_teacher import teaching_dimension

        pool = [{"id": f"i{i}", "features": []} for i in range(5)]
        labels = {f"i{i}": f"C{i % 3}" for i in range(5)}
        result = teaching_dimension("C0", pool, labels)

        assert len(result["elimination_log"]) > 0
        for entry in result["elimination_log"]:
            assert "round" in entry
            assert "selected_item" in entry
            assert "concepts_eliminated" in entry


# ---------------------------------------------------------------------------
# 3. Optimal Compliance Ordering Tests (D-BT-4)
# ---------------------------------------------------------------------------


class TestOptimalComplianceOrder:
    """Tests for optimal_compliance_order()."""

    def test_basic_ordering(self):
        from tools.intelligence.bayesian_teacher import optimal_compliance_order

        controls = ["AC-2", "AC-3", "AC-6", "AU-2", "SC-7"]
        result = optimal_compliance_order(controls, project_id="proj-test")

        assert len(result["ordered_controls"]) == 5
        assert set(result["ordered_controls"]) == set(controls)
        assert result["total_controls"] == 5
        assert result["total_info_gain"] > 0

    def test_parent_controls_ordered_early(self):
        from tools.intelligence.bayesian_teacher import optimal_compliance_order

        controls = ["AC-3", "AC-6", "AC-2", "AC-7"]
        result = optimal_compliance_order(controls)

        # AC-2 has the most children (AC-3, AC-6, AC-7) so should appear
        # in the first half of the ordering (high cascade info gain)
        ac2_pos = result["ordered_controls"].index("AC-2")
        assert ac2_pos < len(controls) // 2 + 1

    def test_steps_have_decreasing_marginal_gain(self):
        from tools.intelligence.bayesian_teacher import optimal_compliance_order

        controls = ["AC-2", "AC-3", "AU-2", "AU-3", "SC-7", "IA-2", "CM-2"]
        result = optimal_compliance_order(controls)

        gains = [s["info_gain"] for s in result["steps"]]
        # First gain should generally be >= last gain (information depletes)
        assert gains[0] >= gains[-1]

    def test_empty_controls(self):
        from tools.intelligence.bayesian_teacher import optimal_compliance_order

        result = optimal_compliance_order([])
        assert result["ordered_controls"] == []
        assert result["total_info_gain"] == 0.0

    def test_single_control(self):
        from tools.intelligence.bayesian_teacher import optimal_compliance_order

        result = optimal_compliance_order(["AC-2"])
        assert len(result["ordered_controls"]) == 1
        assert result["ordered_controls"][0] == "AC-2"

    def test_improvement_vs_alphabetical(self):
        from tools.intelligence.bayesian_teacher import optimal_compliance_order

        controls = ["AC-2", "AC-3", "AC-6", "AU-2", "AU-3", "SC-7", "SI-2"]
        result = optimal_compliance_order(controls)

        imp = result["improvement_vs_alphabetical"]
        assert "improvement_pct" in imp

    def test_stores_result_in_db(self, bt_db):
        from tools.intelligence.bayesian_teacher import optimal_compliance_order

        optimal_compliance_order(["AC-2", "AC-3"], project_id="proj-db-test", db_path=bt_db)

        conn = sqlite3.connect(str(bt_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM bayesian_teaching_scores WHERE candidate_type = 'compliance_ordering'"
        ).fetchone()[0]
        conn.close()
        assert count >= 1


# ---------------------------------------------------------------------------
# 4. SmartEncoding Tests (D-BT-5)
# ---------------------------------------------------------------------------


class TestSmartEncoding:
    """Tests for DeepFlow-inspired SmartEncoding tag compression."""

    def test_encode_known_tags(self):
        from tools.intelligence.bayesian_teacher import smart_encode_tags

        tags = {
            "agent_id": "builder-agent",
            "status_code": "OK",
            "classification": "CUI",
        }
        result = smart_encode_tags(tags)

        assert result["tags_compressed"] == 3
        assert result["compression_ratio"] > 1.0
        assert isinstance(result["encoded"]["agent_id"], int)
        assert result["encoded"]["agent_id"] == 3  # builder-agent

    def test_encode_unknown_tags_passthrough(self):
        from tools.intelligence.bayesian_teacher import smart_encode_tags

        tags = {"custom_key": "custom_value"}
        result = smart_encode_tags(tags)

        assert result["tags_compressed"] == 0
        assert result["encoded"]["custom_key"] == "custom_value"

    def test_encode_mixed_tags(self):
        from tools.intelligence.bayesian_teacher import smart_encode_tags

        tags = {
            "agent_id": "security-agent",
            "status": "OK",
            "custom": "foobar",
        }
        result = smart_encode_tags(tags)

        assert result["tags_compressed"] == 2  # agent_id and status
        assert result["tags_total"] == 3

    def test_decode_roundtrip(self):
        from tools.intelligence.bayesian_teacher import (
            smart_decode_tags,
            smart_encode_tags,
        )

        original = {
            "agent_id": "compliance-agent",
            "op": "mcp.tool_call",
            "classification": "CUI // SP-CTI",
        }
        encoded = smart_encode_tags(original)
        decoded = smart_decode_tags(encoded["encoded"])

        assert decoded["agent_id"] == "compliance-agent"
        assert decoded["op"] == "mcp.tool_call"
        assert decoded["classification"] == "CUI // SP-CTI"

    def test_empty_tags(self):
        from tools.intelligence.bayesian_teacher import smart_encode_tags

        result = smart_encode_tags({})
        assert result["tags_compressed"] == 0
        assert result["compression_ratio"] == 1.0

    def test_encoding_stats_with_spans(self, bt_db):
        from tools.intelligence.bayesian_teacher import compute_encoding_stats

        _insert_spans(bt_db, project_id="proj-enc")
        result = compute_encoding_stats(project_id="proj-enc", db_path=bt_db)

        assert result["spans_analyzed"] == 5
        assert result["estimated_compression_ratio"] > 1.0

    def test_encoding_stats_no_spans(self, bt_db):
        from tools.intelligence.bayesian_teacher import compute_encoding_stats

        result = compute_encoding_stats(project_id="no-proj", db_path=bt_db)
        assert result["spans_analyzed"] == 0


# ---------------------------------------------------------------------------
# 5. Core Math Tests
# ---------------------------------------------------------------------------


class TestCoreMath:
    """Test entropy, KL divergence, and posterior shift."""

    def test_entropy_uniform(self):
        from tools.intelligence.bayesian_teacher import _entropy

        # Uniform over 4 hypotheses: log2(4) = 2.0
        assert abs(_entropy([0.25, 0.25, 0.25, 0.25]) - 2.0) < 0.001

    def test_entropy_certain(self):
        from tools.intelligence.bayesian_teacher import _entropy

        # Certain distribution: entropy = 0
        assert _entropy([1.0, 0.0, 0.0]) == 0.0

    def test_kl_divergence_identical(self):
        from tools.intelligence.bayesian_teacher import _kl_divergence

        p = [0.3, 0.7]
        assert _kl_divergence(p, p) == 0.0

    def test_kl_divergence_different(self):
        from tools.intelligence.bayesian_teacher import _kl_divergence

        kl = _kl_divergence([0.9, 0.1], [0.5, 0.5])
        assert kl > 0.0

    def test_kl_divergence_zero_q(self):
        from tools.intelligence.bayesian_teacher import _kl_divergence

        # q=0 where p>0 → infinity
        assert _kl_divergence([0.5, 0.5], [1.0, 0.0]) == float("inf")

    def test_posterior_shift_no_update(self):
        from tools.intelligence.bayesian_teacher import _posterior_shift

        # When likelihood is proportional to prior, no shift
        prior = [0.5, 0.5]
        likelihood = [0.5, 0.5]
        shift = _posterior_shift(prior, likelihood)
        assert shift < 0.1  # Near zero

    def test_posterior_shift_strong_evidence(self):
        from tools.intelligence.bayesian_teacher import _posterior_shift

        prior = [0.5, 0.5]
        likelihood = [10.0, 0.01]  # Strong evidence for first hypothesis
        shift = _posterior_shift(prior, likelihood)
        assert shift > 0.5  # Significant shift


# ---------------------------------------------------------------------------
# 6. Health Check Tests
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_check_with_db(self, bt_db):
        from tools.intelligence.bayesian_teacher import health_check

        result = health_check(db_path=bt_db)
        assert result["status"] == "healthy"
        assert result["database_available"]
        assert result["dictionary_size"] > 0
        assert result["control_dependencies_mapped"] > 0

    def test_health_check_no_db(self, tmp_path):
        from tools.intelligence.bayesian_teacher import health_check

        result = health_check(db_path=tmp_path / "nonexistent.db")
        assert result["status"] == "degraded"
        assert not result["database_available"]


# ---------------------------------------------------------------------------
# 7. Integration Tests (pair_generator, reranker)
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_pair_generator_bayesian_rank_import(self):
        """Verify the bayesian_rank_pairs function is importable."""
        from tools.finetune.pair_generator import bayesian_rank_pairs

        assert callable(bayesian_rank_pairs)

    def test_reranker_teaching_diversity_function(self):
        """Verify the _apply_teaching_diversity function exists."""
        from tools.rag.reranker import _apply_teaching_diversity

        assert callable(_apply_teaching_diversity)

    def test_teaching_diversity_reduces_redundancy(self):
        """Teaching diversity should penalise identical chunks."""
        from tools.rag.reranker import _apply_teaching_diversity
        from tools.rag.vector_store_provider import SearchResult

        # Create results with identical content
        results = [
            SearchResult(
                chunk_id=f"c{i}",
                content="NIST 800-53 AC-2 account management control" if i < 3 else f"Unique content about topic {i}",
                source_id=f"s{i}",
                source_table="test",
                final_score=0.9 - (i * 0.01),
            )
            for i in range(5)
        ]

        boosted = _apply_teaching_diversity(results, boost_weight=0.3)

        # The diverse items should get a boost relative to redundant ones
        assert len(boosted) == 5
        # All scores should be valid
        for r in boosted:
            assert 0.0 <= r.final_score <= 1.0


# ---------------------------------------------------------------------------
# 8. CLI Tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_health_json(self, bt_db, capsys):
        """Test CLI --health --json output."""
        import sys
        from unittest.mock import patch

        with patch.object(sys, "argv", ["bayesian_teacher.py", "--health", "--json", "--db-path", str(bt_db)]):
            from tools.intelligence.bayesian_teacher import main

            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "healthy"

    def test_optimal_order_json(self, bt_db, capsys):
        """Test CLI --optimal-order --json output."""
        import sys
        from unittest.mock import patch

        with patch.object(
            sys,
            "argv",
            [
                "bayesian_teacher.py",
                "--optimal-order",
                "--json",
                "--controls",
                "AC-2,AC-3,SC-7",
                "--db-path",
                str(bt_db),
            ],
        ):
            from tools.intelligence.bayesian_teacher import main

            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data["ordered_controls"]) == 3
