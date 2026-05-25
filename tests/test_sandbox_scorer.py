#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Sandbox Security Scorer — 8th evaluation dimension (D-NC-4)."""

import json
import sqlite3
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from tools.registry.sandbox_scorer import score_sandbox, RUBRIC, GATE_THRESHOLD


@pytest.fixture(autouse=True)
def mock_sandbox_executor():
    """Mock SandboxExecutor to avoid Docker calls in unit tests.

    sandbox_scorer._check_runtime_isolation calls SandboxExecutor().health_check()
    which probes Docker. This mock prevents slow/hanging Docker operations.
    """
    mock_executor = MagicMock()
    mock_executor._available = False
    mock_executor.health_check.return_value = {"docker_available": False}
    with patch("tools.security.sandbox_executor.SandboxExecutor", return_value=mock_executor):
        yield


@pytest.fixture
def scorer_db(tmp_path):
    """Create a temp DB with NemoClaw tables for testing."""
    db_path = tmp_path / "test_sandbox.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS credential_broker_log (
            id TEXT, agent_id TEXT, function TEXT, action TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS egress_policy_audit (
            id TEXT, agent_role TEXT, policy_hash TEXT, action TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS blueprint_digests (
            id TEXT, entity_type TEXT, entity_id TEXT, digest TEXT,
            file_count INTEGER, total_bytes INTEGER, computed_at TEXT,
            verification_result TEXT
        );
        CREATE TABLE IF NOT EXISTS prov_entities (
            id TEXT, entity_type TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT, event_type TEXT, created_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def empty_db(tmp_path):
    """Create a DB with no tables at all (simulates missing schema)."""
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.close()
    return db_path


@pytest.fixture
def populated_db(scorer_db):
    """Scorer DB pre-populated with passing data in all tables."""
    conn = sqlite3.connect(str(scorer_db))
    conn.execute("INSERT INTO credential_broker_log VALUES ('1','agent','fn','grant','2026-01-01')")
    conn.execute("INSERT INTO egress_policy_audit VALUES ('1','builder','hash','resolve','2026-01-01')")
    conn.execute("INSERT INTO blueprint_digests VALUES ('1','capability','cap-1','abc123',5,1024,'2026-01-01','pass')")
    conn.execute("INSERT INTO prov_entities VALUES ('1','agent','2026-01-01')")
    conn.execute("INSERT INTO audit_trail VALUES ('1','test','2026-01-01')")
    conn.commit()
    conn.close()
    return scorer_db


# ---------------------------------------------------------------------------
# Original tests (1-7)
# ---------------------------------------------------------------------------


class TestSandboxScoring:
    def test_full_isolation_scores_high(self, scorer_db):
        result = score_sandbox(
            source_metadata={
                "has_broker": True,
                "has_egress_policy": True,
                "has_digest": True,
                "has_provenance": True,
                "runs_in_container": True,
                "has_audit_trail": True,
            },
            db_path=scorer_db,
        )
        assert result["score"] == 1.0
        for dim_score in result["breakdown"].values():
            assert dim_score == 1.0

    def test_no_isolation_scores_low(self, scorer_db):
        result = score_sandbox(
            source_metadata={
                "has_broker": False,
                "has_egress_policy": False,
                "has_digest": False,
                "has_provenance": False,
                "runs_in_container": False,
                "has_audit_trail": False,
            },
            db_path=scorer_db,
        )
        assert result["score"] < 0.25
        assert len(result["recommendations"]) >= 5

    def test_partial_isolation(self, scorer_db):
        result = score_sandbox(
            source_metadata={
                "has_broker": True,
                "has_egress_policy": False,
                "has_digest": True,
                "has_provenance": False,
                "runs_in_container": True,
                "has_audit_trail": True,
            },
            db_path=scorer_db,
        )
        assert 0.4 < result["score"] < 0.9

    def test_rubric_weights_sum_to_one(self):
        total = sum(RUBRIC.values())
        assert abs(total - 1.0) < 0.001

    def test_returns_breakdown_and_recommendations(self, scorer_db):
        result = score_sandbox(db_path=scorer_db)
        assert "breakdown" in result
        assert "recommendations" in result
        assert "score" in result
        assert len(result["breakdown"]) == 6

    def test_db_checks_with_broker_data(self, scorer_db):
        conn = sqlite3.connect(str(scorer_db))
        conn.execute("INSERT INTO credential_broker_log VALUES ('1','agent','fn','grant','2026-01-01')")
        conn.execute("INSERT INTO egress_policy_audit VALUES ('1','builder','hash','resolve','2026-01-01')")
        conn.execute("INSERT INTO audit_trail VALUES ('1','test','2026-01-01')")
        conn.commit()
        conn.close()
        result = score_sandbox(db_path=scorer_db)
        assert result["breakdown"]["credential_isolation"] == 1.0
        assert result["breakdown"]["network_restriction"] == 1.0
        assert result["breakdown"]["audit_completeness"] == 1.0

    def test_source_type_affects_runtime_score(self, scorer_db):
        child_result = score_sandbox(
            capability_data={"source": "child_report"},
            db_path=scorer_db,
        )
        unknown_result = score_sandbox(
            capability_data={"source": "external"},
            db_path=scorer_db,
        )
        assert child_result["breakdown"]["runtime_isolation"] > unknown_result["breakdown"]["runtime_isolation"]


# ---------------------------------------------------------------------------
# Error case tests (8-12)
# ---------------------------------------------------------------------------


class TestSandboxErrorCases:
    def test_missing_tables_returns_zero_scores(self, empty_db):
        """DB without required tables should degrade gracefully to 0.0."""
        result = score_sandbox(db_path=empty_db)
        assert result["score"] < 0.25
        # DB-backed dimensions should all be 0.0
        assert result["breakdown"]["credential_isolation"] == 0.0
        assert result["breakdown"]["network_restriction"] == 0.0
        assert result["breakdown"]["digest_verified"] == 0.0
        assert result["breakdown"]["provenance_chain"] == 0.0
        assert result["breakdown"]["audit_completeness"] == 0.0

    def test_nonexistent_db_path_returns_zero(self, tmp_path):
        """Non-existent DB path should not crash; scores degrade to 0."""
        bad_path = tmp_path / "does_not_exist.db"
        result = score_sandbox(db_path=bad_path)
        assert result["score"] < 0.25
        assert result["breakdown"]["credential_isolation"] == 0.0

    def test_none_capability_data(self, scorer_db):
        """Passing None for capability_data is valid and defaults to {}."""
        result = score_sandbox(capability_data=None, db_path=scorer_db)
        assert "score" in result
        assert result["source"] == "unknown"

    def test_none_source_metadata(self, scorer_db):
        """Passing None for source_metadata is valid and defaults to {}."""
        result = score_sandbox(source_metadata=None, db_path=scorer_db)
        assert "score" in result
        assert len(result["breakdown"]) == 6

    def test_empty_dicts(self, scorer_db):
        """Empty dicts for both parameters should work and fall through to DB."""
        result = score_sandbox(
            capability_data={},
            source_metadata={},
            db_path=scorer_db,
        )
        assert "score" in result
        assert "breakdown" in result


# ---------------------------------------------------------------------------
# Boundary score tests (13-15)
# ---------------------------------------------------------------------------


class TestSandboxBoundaryScores:
    def test_all_false_metadata_boundary(self, scorer_db):
        """All-false metadata produces minimum score (0.02 from runtime floor)."""
        result = score_sandbox(
            source_metadata={
                "has_broker": False,
                "has_egress_policy": False,
                "has_digest": False,
                "has_provenance": False,
                "runs_in_container": False,
                "has_audit_trail": False,
            },
            db_path=scorer_db,
        )
        # runs_in_container=False gives 0.2 (not 0.0), weight 0.10 => 0.02
        expected = 0.2 * RUBRIC["runtime_isolation"]
        assert abs(result["score"] - expected) < 0.001

    def test_all_true_metadata_boundary(self, scorer_db):
        """All-true metadata produces exactly 1.0."""
        result = score_sandbox(
            source_metadata={
                "has_broker": True,
                "has_egress_policy": True,
                "has_digest": True,
                "has_provenance": True,
                "runs_in_container": True,
                "has_audit_trail": True,
            },
            db_path=scorer_db,
        )
        assert result["score"] == 1.0

    def test_score_never_exceeds_one(self, scorer_db):
        """Score must always be <= 1.0 regardless of inputs."""
        result = score_sandbox(
            source_metadata={
                "has_broker": True,
                "has_egress_policy": True,
                "has_digest": True,
                "has_provenance": True,
                "runs_in_container": True,
                "has_audit_trail": True,
            },
            db_path=scorer_db,
        )
        assert result["score"] <= 1.0

    def test_score_never_negative(self, scorer_db):
        """Score must always be >= 0.0 regardless of inputs."""
        result = score_sandbox(
            source_metadata={
                "has_broker": False,
                "has_egress_policy": False,
                "has_digest": False,
                "has_provenance": False,
                "runs_in_container": False,
                "has_audit_trail": False,
            },
            db_path=scorer_db,
        )
        assert result["score"] >= 0.0


# ---------------------------------------------------------------------------
# Individual rubric dimension tests (16-21)
# ---------------------------------------------------------------------------


class TestSandboxRubricDimensions:
    def test_credential_isolation_only(self, scorer_db):
        """Only credential isolation enabled — contributes exactly its weight."""
        result = score_sandbox(
            source_metadata={
                "has_broker": True,
                "has_egress_policy": False,
                "has_digest": False,
                "has_provenance": False,
                "runs_in_container": False,
                "has_audit_trail": False,
            },
            db_path=scorer_db,
        )
        expected = 1.0 * RUBRIC["credential_isolation"] + 0.2 * RUBRIC["runtime_isolation"]
        assert abs(result["score"] - round(expected, 4)) < 0.001
        assert result["breakdown"]["credential_isolation"] == 1.0

    def test_network_restriction_only(self, scorer_db):
        """Only network restriction enabled."""
        result = score_sandbox(
            source_metadata={
                "has_broker": False,
                "has_egress_policy": True,
                "has_digest": False,
                "has_provenance": False,
                "runs_in_container": False,
                "has_audit_trail": False,
            },
            db_path=scorer_db,
        )
        expected = 1.0 * RUBRIC["network_restriction"] + 0.2 * RUBRIC["runtime_isolation"]
        assert abs(result["score"] - round(expected, 4)) < 0.001
        assert result["breakdown"]["network_restriction"] == 1.0

    def test_digest_verified_only(self, scorer_db):
        """Only digest verified enabled."""
        result = score_sandbox(
            source_metadata={
                "has_broker": False,
                "has_egress_policy": False,
                "has_digest": True,
                "has_provenance": False,
                "runs_in_container": False,
                "has_audit_trail": False,
            },
            db_path=scorer_db,
        )
        expected = 1.0 * RUBRIC["digest_verified"] + 0.2 * RUBRIC["runtime_isolation"]
        assert abs(result["score"] - round(expected, 4)) < 0.001
        assert result["breakdown"]["digest_verified"] == 1.0

    def test_provenance_chain_only(self, scorer_db):
        """Only provenance chain enabled."""
        result = score_sandbox(
            source_metadata={
                "has_broker": False,
                "has_egress_policy": False,
                "has_digest": False,
                "has_provenance": True,
                "runs_in_container": False,
                "has_audit_trail": False,
            },
            db_path=scorer_db,
        )
        expected = 1.0 * RUBRIC["provenance_chain"] + 0.2 * RUBRIC["runtime_isolation"]
        assert abs(result["score"] - round(expected, 4)) < 0.001
        assert result["breakdown"]["provenance_chain"] == 1.0

    def test_runtime_isolation_only(self, scorer_db):
        """Only runtime isolation enabled (container)."""
        result = score_sandbox(
            source_metadata={
                "has_broker": False,
                "has_egress_policy": False,
                "has_digest": False,
                "has_provenance": False,
                "runs_in_container": True,
                "has_audit_trail": False,
            },
            db_path=scorer_db,
        )
        expected = 1.0 * RUBRIC["runtime_isolation"]
        assert abs(result["score"] - round(expected, 4)) < 0.001
        assert result["breakdown"]["runtime_isolation"] == 1.0

    def test_audit_completeness_only(self, scorer_db):
        """Only audit completeness enabled."""
        result = score_sandbox(
            source_metadata={
                "has_broker": False,
                "has_egress_policy": False,
                "has_digest": False,
                "has_provenance": False,
                "runs_in_container": False,
                "has_audit_trail": True,
            },
            db_path=scorer_db,
        )
        expected = 1.0 * RUBRIC["audit_completeness"] + 0.2 * RUBRIC["runtime_isolation"]
        assert abs(result["score"] - round(expected, 4)) < 0.001
        assert result["breakdown"]["audit_completeness"] == 1.0


# ---------------------------------------------------------------------------
# Source metadata partial/missing key tests (22-25)
# ---------------------------------------------------------------------------


class TestSandboxPartialMetadata:
    def test_metadata_with_only_broker_key(self, scorer_db):
        """Metadata with only has_broker falls through to DB for others."""
        result = score_sandbox(
            source_metadata={"has_broker": True},
            db_path=scorer_db,
        )
        assert result["breakdown"]["credential_isolation"] == 1.0
        # Other dimensions fall to DB checks (empty tables => 0.0)
        assert result["breakdown"]["network_restriction"] == 0.0

    def test_metadata_with_only_container_key(self, scorer_db):
        """Metadata with only runs_in_container; others from DB."""
        result = score_sandbox(
            source_metadata={"runs_in_container": True},
            db_path=scorer_db,
        )
        assert result["breakdown"]["runtime_isolation"] == 1.0
        assert result["breakdown"]["credential_isolation"] == 0.0

    def test_metadata_with_extra_unknown_keys(self, scorer_db):
        """Unknown keys in metadata should be silently ignored."""
        result = score_sandbox(
            source_metadata={
                "has_broker": True,
                "unknown_field": "ignored",
                "another_field": 42,
            },
            db_path=scorer_db,
        )
        assert result["breakdown"]["credential_isolation"] == 1.0
        assert "score" in result

    def test_metadata_with_none_values(self, scorer_db):
        """Metadata keys set to None should fall through to DB check."""
        result = score_sandbox(
            source_metadata={
                "has_broker": None,
                "has_egress_policy": None,
            },
            db_path=scorer_db,
        )
        # None triggers meta.get() is not None => False, falls to DB
        # Empty DB tables => 0.0
        assert result["breakdown"]["credential_isolation"] == 0.0
        assert result["breakdown"]["network_restriction"] == 0.0


# ---------------------------------------------------------------------------
# DB detail checks (26-28)
# ---------------------------------------------------------------------------


class TestSandboxDBChecks:
    def test_digest_pass_vs_fail(self, scorer_db):
        """Digest with verification_result='pass' scores 1.0, 'fail' scores 0.5."""
        conn = sqlite3.connect(str(scorer_db))
        conn.execute(
            "INSERT INTO blueprint_digests VALUES ('1','capability','cap-1','abc','5','1024','2026-01-01','pass')"
        )
        conn.commit()
        conn.close()
        result = score_sandbox(
            capability_data={"id": "cap-1"},
            db_path=scorer_db,
        )
        assert result["breakdown"]["digest_verified"] == 1.0

    def test_digest_failed_scores_half(self, scorer_db):
        """Digest with verification_result='fail' scores 0.5."""
        conn = sqlite3.connect(str(scorer_db))
        conn.execute(
            "INSERT INTO blueprint_digests VALUES ('1','capability','cap-2','abc','5','1024','2026-01-01','fail')"
        )
        conn.commit()
        conn.close()
        result = score_sandbox(
            capability_data={"id": "cap-2"},
            db_path=scorer_db,
        )
        assert result["breakdown"]["digest_verified"] == 0.5

    def test_all_db_populated_scores_high(self, populated_db):
        """All DB tables populated should produce high scores via DB checks."""
        result = score_sandbox(
            capability_data={"id": "cap-1"},
            db_path=populated_db,
        )
        assert result["breakdown"]["credential_isolation"] == 1.0
        assert result["breakdown"]["network_restriction"] == 1.0
        assert result["breakdown"]["digest_verified"] == 1.0
        assert result["breakdown"]["provenance_chain"] == 1.0
        assert result["breakdown"]["audit_completeness"] == 1.0


# ---------------------------------------------------------------------------
# Runtime isolation source types (29-30)
# ---------------------------------------------------------------------------


class TestSandboxRuntimeSources:
    def test_marketplace_source_high_isolation(self, scorer_db):
        """Marketplace source gets 1.0 runtime isolation."""
        result = score_sandbox(
            capability_data={"source": "marketplace"},
            db_path=scorer_db,
        )
        assert result["breakdown"]["runtime_isolation"] == 1.0

    def test_innovation_source_medium_isolation(self, scorer_db):
        """Innovation source gets 0.5 runtime isolation."""
        result = score_sandbox(
            capability_data={"source": "innovation"},
            db_path=scorer_db,
        )
        assert result["breakdown"]["runtime_isolation"] == 0.5

    def test_introspective_source_medium_isolation(self, scorer_db):
        """Introspective source gets 0.5 runtime isolation."""
        result = score_sandbox(
            capability_data={"source": "introspective"},
            db_path=scorer_db,
        )
        assert result["breakdown"]["runtime_isolation"] == 0.5

    def test_external_source_low_isolation(self, scorer_db):
        """External/unknown source gets 0.2 runtime isolation."""
        result = score_sandbox(
            capability_data={"source": "external"},
            db_path=scorer_db,
        )
        assert result["breakdown"]["runtime_isolation"] == 0.2

    def test_empty_source_defaults_low(self, scorer_db):
        """Empty source string defaults to 0.2 runtime isolation."""
        result = score_sandbox(
            capability_data={"source": ""},
            db_path=scorer_db,
        )
        assert result["breakdown"]["runtime_isolation"] == 0.2


# ---------------------------------------------------------------------------
# Output structure and return value tests (31-33)
# ---------------------------------------------------------------------------


class TestSandboxOutputStructure:
    def test_output_contains_all_keys(self, scorer_db):
        """Result dict must contain score, breakdown, rubric_weights, recommendations, source."""
        result = score_sandbox(db_path=scorer_db)
        assert "score" in result
        assert "breakdown" in result
        assert "rubric_weights" in result
        assert "recommendations" in result
        assert "source" in result

    def test_rubric_weights_in_output_match_constant(self, scorer_db):
        """rubric_weights in output should match the RUBRIC constant."""
        result = score_sandbox(db_path=scorer_db)
        assert result["rubric_weights"] == dict(RUBRIC)

    def test_source_field_from_capability_data(self, scorer_db):
        """source field should reflect capability_data's source key."""
        result = score_sandbox(
            capability_data={"source": "child_report"},
            db_path=scorer_db,
        )
        assert result["source"] == "child_report"

    def test_recommendations_are_strings(self, scorer_db):
        """All recommendations should be strings."""
        result = score_sandbox(db_path=scorer_db)
        for rec in result["recommendations"]:
            assert isinstance(rec, str)

    def test_breakdown_dimensions_match_rubric(self, scorer_db):
        """Breakdown keys must exactly match RUBRIC keys."""
        result = score_sandbox(db_path=scorer_db)
        assert set(result["breakdown"].keys()) == set(RUBRIC.keys())


# ---------------------------------------------------------------------------
# Gate threshold tests (34-35)
# ---------------------------------------------------------------------------


class TestSandboxGateThreshold:
    def test_gate_threshold_constant_value(self):
        """GATE_THRESHOLD should be 0.65."""
        assert GATE_THRESHOLD == 0.65

    def test_full_isolation_passes_gate(self, scorer_db):
        """Score of 1.0 should pass the gate threshold."""
        result = score_sandbox(
            source_metadata={
                "has_broker": True,
                "has_egress_policy": True,
                "has_digest": True,
                "has_provenance": True,
                "runs_in_container": True,
                "has_audit_trail": True,
            },
            db_path=scorer_db,
        )
        assert result["score"] >= GATE_THRESHOLD

    def test_no_isolation_fails_gate(self, scorer_db):
        """Low score should fail the gate threshold."""
        result = score_sandbox(
            source_metadata={
                "has_broker": False,
                "has_egress_policy": False,
                "has_digest": False,
                "has_provenance": False,
                "runs_in_container": False,
                "has_audit_trail": False,
            },
            db_path=scorer_db,
        )
        assert result["score"] < GATE_THRESHOLD


# ---------------------------------------------------------------------------
# CLI tests (36-37)
# ---------------------------------------------------------------------------


class TestSandboxCLI:
    def test_cli_score_json_output(self, scorer_db):
        """CLI --score --json should produce valid JSON with expected keys."""
        meta = json.dumps(
            {
                "has_broker": True,
                "has_egress_policy": True,
                "has_digest": True,
                "has_provenance": True,
                "runs_in_container": True,
                "has_audit_trail": True,
            }
        )
        proc = subprocess.run(
            [
                sys.executable,
                "tools/registry/sandbox_scorer.py",
                "--score",
                "--source-metadata",
                meta,
                "--db-path",
                str(scorer_db),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        output = json.loads(proc.stdout)
        assert "score" in output
        assert "breakdown" in output
        assert "rubric_weights" in output
        assert "recommendations" in output
        assert output["score"] == 1.0

    def test_cli_gate_pass(self, scorer_db):
        """CLI --gate --json with full isolation should exit 0 (pass)."""
        meta = json.dumps(
            {
                "has_broker": True,
                "has_egress_policy": True,
                "has_digest": True,
                "has_provenance": True,
                "runs_in_container": True,
                "has_audit_trail": True,
            }
        )
        proc = subprocess.run(
            [
                sys.executable,
                "tools/registry/sandbox_scorer.py",
                "--gate",
                "--source-metadata",
                meta,
                "--db-path",
                str(scorer_db),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        output = json.loads(proc.stdout)
        assert output["gate"] == "sandbox_security"
        assert output["passed"] is True
        assert output["threshold"] == GATE_THRESHOLD

    def test_cli_gate_fail(self, scorer_db):
        """CLI --gate --json with no isolation should exit 1 (fail)."""
        meta = json.dumps(
            {
                "has_broker": False,
                "has_egress_policy": False,
                "has_digest": False,
                "has_provenance": False,
                "runs_in_container": False,
                "has_audit_trail": False,
            }
        )
        proc = subprocess.run(
            [
                sys.executable,
                "tools/registry/sandbox_scorer.py",
                "--gate",
                "--source-metadata",
                meta,
                "--db-path",
                str(scorer_db),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 1
        output = json.loads(proc.stdout)
        assert output["passed"] is False
