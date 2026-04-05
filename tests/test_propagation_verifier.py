#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Post-Propagation Verifier — NemoClaw migration verification (D-NC-5)."""

import json
import sqlite3
import subprocess
import sys

import pytest

from tools.registry.propagation_verifier import PropagationVerifier


@pytest.fixture
def verifier_db(tmp_path):
    """Create temp DB with propagation_log and supporting tables."""
    db_path = tmp_path / "test_verifier.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS propagation_log (
            id TEXT PRIMARY KEY,
            capability_id TEXT,
            capability_name TEXT,
            source_type TEXT,
            target_children_json TEXT,
            status TEXT DEFAULT 'prepared',
            genome_version_before TEXT,
            genome_version_after TEXT,
            rollback_plan TEXT,
            prepared_by TEXT DEFAULT 'system',
            approved_by TEXT,
            approved_at TEXT,
            executed_by TEXT,
            executed_at TEXT,
            completed_at TEXT,
            rollback_reason TEXT,
            rolled_back_at TEXT,
            rolled_back_by TEXT,
            execution_results_json TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS blueprint_digests (
            id TEXT, entity_type TEXT, entity_id TEXT, digest TEXT,
            file_count INTEGER, total_bytes INTEGER, computed_at TEXT,
            verification_result TEXT
        );
        CREATE TABLE IF NOT EXISTS child_telemetry (
            id TEXT, child_id TEXT, metric_type TEXT, metric_data TEXT,
            health_status TEXT, collected_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def verifier(verifier_db):
    return PropagationVerifier(db_path=verifier_db)


def _insert_propagation(db_path, prop_id="prop-1", status="completed", cap_id="cap-1", children=None, results=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO propagation_log
           (id, capability_id, capability_name, status, target_children_json,
            completed_at, execution_results_json, created_at)
           VALUES (?, ?, 'test-cap', ?, ?, '2026-01-01T00:00:00Z', ?, '2026-01-01')""",
        (
            prop_id,
            cap_id,
            status,
            json.dumps(children or ["child-1"]),
            json.dumps(results or {"success_count": 1, "fail_count": 0}),
        ),
    )
    conn.commit()
    conn.close()


class TestVerifyPropagation:
    def test_verify_completed_propagation(self, verifier, verifier_db):
        _insert_propagation(verifier_db)
        result = verifier.verify_propagation("prop-1")
        assert result["propagation_id"] == "prop-1"
        assert result["passed"] is True
        assert result["pass_count"] >= 1

    def test_verify_not_found(self, verifier):
        result = verifier.verify_propagation("nonexistent")
        assert "error" in result
        assert result["error"] == "propagation_not_found"

    def test_verify_not_finished(self, verifier, verifier_db):
        _insert_propagation(verifier_db, status="executing")
        result = verifier.verify_propagation("prop-1")
        assert "error" in result
        assert result["error"] == "propagation_not_finished"

    def test_verify_detects_execution_failure(self, verifier, verifier_db):
        _insert_propagation(
            verifier_db,
            results={"success_count": 0, "fail_count": 2},
        )
        result = verifier.verify_propagation("prop-1")
        fail_checks = [c for c in result["checks"] if c["status"] == "fail"]
        assert len(fail_checks) >= 1

    def test_verify_detects_health_degradation(self, verifier, verifier_db):
        _insert_propagation(verifier_db)
        conn = sqlite3.connect(str(verifier_db))
        conn.execute(
            """INSERT INTO child_telemetry
               (id, child_id, metric_type, health_status, collected_at)
               VALUES ('t1', 'child-1', 'heartbeat', 'unhealthy', '2026-01-01T00:05:00Z')""",
        )
        conn.commit()
        conn.close()
        result = verifier.verify_propagation("prop-1")
        error_checks = [c for c in result["checks"] if c["name"] == "error_rate_check" and c["status"] == "fail"]
        assert len(error_checks) >= 1

    def test_verify_with_digest(self, verifier, verifier_db):
        _insert_propagation(verifier_db)
        conn = sqlite3.connect(str(verifier_db))
        conn.execute(
            """INSERT INTO blueprint_digests
               (id, entity_type, entity_id, digest, file_count, total_bytes,
                computed_at, verification_result)
               VALUES ('d1', 'capability', 'cap-1', 'abc123', 10, 5000,
                       '2026-01-01', 'pass')""",
        )
        conn.commit()
        conn.close()
        result = verifier.verify_propagation("prop-1")
        digest_checks = [c for c in result["checks"] if c["name"] == "digest_verified"]
        assert digest_checks[0]["status"] == "pass"

    def test_stores_verification_results(self, verifier, verifier_db):
        _insert_propagation(verifier_db)
        verifier.verify_propagation("prop-1")
        conn = sqlite3.connect(str(verifier_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM propagation_verifications WHERE propagation_id = 'prop-1'"
        ).fetchone()[0]
        conn.close()
        assert count >= 3  # At least status + digest + execution_results


class TestHistory:
    def test_history_returns_records(self, verifier, verifier_db):
        _insert_propagation(verifier_db)
        verifier.verify_propagation("prop-1")
        history = verifier.get_history(propagation_id="prop-1")
        assert len(history) >= 1

    def test_history_by_child(self, verifier, verifier_db):
        _insert_propagation(verifier_db, children=["child-abc"])
        verifier.verify_propagation("prop-1")
        history = verifier.get_history(child_id="child-abc")
        assert len(history) >= 1

    def test_empty_history(self, verifier):
        history = verifier.get_history()
        assert len(history) == 0


# ---------------------------------------------------------------------------
# Rollback scenario tests (after failed verification)
# ---------------------------------------------------------------------------


class TestRollbackScenarios:
    def test_verify_failed_propagation_status(self, verifier, verifier_db):
        """A propagation with status='failed' should be verifiable but report failure."""
        _insert_propagation(verifier_db, status="failed")
        result = verifier.verify_propagation("prop-1")
        assert result["propagation_id"] == "prop-1"
        status_check = [c for c in result["checks"] if c["name"] == "propagation_status"]
        assert len(status_check) == 1
        assert status_check[0]["status"] == "fail"
        assert result["passed"] is False

    def test_verify_rolled_back_propagation(self, verifier, verifier_db):
        """A propagation with rollback fields set should still be verifiable
        if its status is 'completed' or 'failed'."""
        conn = sqlite3.connect(str(verifier_db))
        conn.execute(
            """INSERT INTO propagation_log
               (id, capability_id, capability_name, status, target_children_json,
                completed_at, execution_results_json, created_at,
                rollback_reason, rolled_back_at, rolled_back_by)
               VALUES ('prop-rb', 'cap-1', 'test-cap', 'failed',
                       '["child-1"]', '2026-01-01T00:00:00Z',
                       '{"success_count": 0, "fail_count": 1}', '2026-01-01',
                       'Health degradation detected', '2026-01-01T00:10:00Z', 'operator')""",
        )
        conn.commit()
        conn.close()
        result = verifier.verify_propagation("prop-rb")
        assert result["passed"] is False
        assert result["fail_count"] >= 1

    def test_verify_after_rollback_multiple_failures(self, verifier, verifier_db):
        """Verify that multiple children failing produces multiple fail checks."""
        _insert_propagation(
            verifier_db,
            children=["child-a", "child-b"],
            results={"success_count": 0, "fail_count": 2},
        )
        # Insert degraded health for both children
        conn = sqlite3.connect(str(verifier_db))
        conn.execute(
            """INSERT INTO child_telemetry
               (id, child_id, metric_type, health_status, collected_at)
               VALUES ('ta', 'child-a', 'heartbeat', 'degraded', '2026-01-01T00:05:00Z')""",
        )
        conn.execute(
            """INSERT INTO child_telemetry
               (id, child_id, metric_type, health_status, collected_at)
               VALUES ('tb', 'child-b', 'heartbeat', 'unhealthy', '2026-01-01T00:05:00Z')""",
        )
        conn.commit()
        conn.close()
        result = verifier.verify_propagation("prop-1")
        assert result["passed"] is False
        error_rate_fails = [c for c in result["checks"] if c["name"] == "error_rate_check" and c["status"] == "fail"]
        assert len(error_rate_fails) == 2


# ---------------------------------------------------------------------------
# CUI marking preservation checks
# ---------------------------------------------------------------------------


class TestCUIMarkings:
    def test_module_has_cui_marking(self):
        """Verify the propagation_verifier.py module starts with CUI marking."""
        from pathlib import Path

        module_path = Path(__file__).resolve().parent.parent / "tools" / "registry" / "propagation_verifier.py"
        first_lines = module_path.read_text(encoding="utf-8").splitlines()[:3]
        cui_found = any("CUI // SP-CTI" in line for line in first_lines)
        assert cui_found, "Module must start with CUI // SP-CTI marking"

    def test_test_file_has_cui_marking(self):
        """Verify this test file itself starts with CUI marking."""
        from pathlib import Path

        test_path = Path(__file__).resolve()
        first_lines = test_path.read_text(encoding="utf-8").splitlines()[:3]
        cui_found = any("CUI // SP-CTI" in line for line in first_lines)
        assert cui_found, "Test file must start with CUI // SP-CTI marking"

    def test_verification_result_contains_timestamp(self, verifier, verifier_db):
        """CUI artifacts must have verified_at timestamps for audit trail."""
        _insert_propagation(verifier_db)
        result = verifier.verify_propagation("prop-1")
        assert "verified_at" in result
        assert result["verified_at"] is not None


# ---------------------------------------------------------------------------
# CLI gate output tests (exit code 0 pass vs exit code 1 fail)
# ---------------------------------------------------------------------------


class TestCLIGate:
    def test_gate_pass_exit_code_0(self, verifier_db):
        """--gate should exit 0 when propagation passes all checks."""
        _insert_propagation(verifier_db)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.registry.propagation_verifier",
                "--gate",
                "--propagation-id",
                "prop-1",
                "--json",
                "--db-path",
                str(verifier_db),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0

    def test_gate_fail_exit_code_1(self, verifier_db):
        """--gate should exit 1 when propagation has failures."""
        _insert_propagation(
            verifier_db,
            results={"success_count": 0, "fail_count": 3},
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.registry.propagation_verifier",
                "--gate",
                "--propagation-id",
                "prop-1",
                "--json",
                "--db-path",
                str(verifier_db),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 1

    def test_gate_nonexistent_propagation_exit_code_1(self, verifier_db):
        """--gate for a nonexistent propagation should exit 1 (not found = not passed)."""
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.registry.propagation_verifier",
                "--gate",
                "--propagation-id",
                "does-not-exist",
                "--json",
                "--db-path",
                str(verifier_db),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 1


# ---------------------------------------------------------------------------
# CLI --json output structure test
# ---------------------------------------------------------------------------


class TestCLIJsonOutput:
    def test_json_output_structure_verify(self, verifier_db):
        """--verify --json should produce valid JSON with expected keys."""
        _insert_propagation(verifier_db)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.registry.propagation_verifier",
                "--verify",
                "--propagation-id",
                "prop-1",
                "--json",
                "--db-path",
                str(verifier_db),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert "propagation_id" in data
        assert "passed" in data
        assert "checks" in data
        assert "pass_count" in data
        assert "fail_count" in data
        assert "skip_count" in data
        assert "verified_at" in data
        assert isinstance(data["checks"], list)

    def test_json_output_structure_history(self, verifier_db):
        """--history --json should produce valid JSON list."""
        _insert_propagation(verifier_db)
        # Run verify first to create history records
        subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.registry.propagation_verifier",
                "--verify",
                "--propagation-id",
                "prop-1",
                "--json",
                "--db-path",
                str(verifier_db),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.registry.propagation_verifier",
                "--history",
                "--propagation-id",
                "prop-1",
                "--json",
                "--db-path",
                str(verifier_db),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert isinstance(data, list)
        assert len(data) >= 1
        # Each record should have key fields
        record = data[0]
        assert "propagation_id" in record
        assert "check_name" in record
        assert "check_status" in record


# ---------------------------------------------------------------------------
# History query with multiple propagation events
# ---------------------------------------------------------------------------


class TestHistoryMultiple:
    def test_history_multiple_propagations(self, verifier, verifier_db):
        """History should return records from multiple distinct propagation events."""
        _insert_propagation(verifier_db, prop_id="prop-a", children=["child-1"])
        _insert_propagation(verifier_db, prop_id="prop-b", children=["child-1"])
        verifier.verify_propagation("prop-a")
        verifier.verify_propagation("prop-b")
        history = verifier.get_history()
        # Both propagations should have verification records
        prop_ids = {r["propagation_id"] for r in history}
        assert "prop-a" in prop_ids
        assert "prop-b" in prop_ids

    def test_history_filter_by_propagation_id(self, verifier, verifier_db):
        """History filtered by propagation_id should only return that propagation's records."""
        _insert_propagation(verifier_db, prop_id="prop-x", children=["child-1"])
        _insert_propagation(verifier_db, prop_id="prop-y", children=["child-2"])
        verifier.verify_propagation("prop-x")
        verifier.verify_propagation("prop-y")
        history = verifier.get_history(propagation_id="prop-x")
        prop_ids = {r["propagation_id"] for r in history}
        assert prop_ids == {"prop-x"}

    def test_history_limit_parameter(self, verifier, verifier_db):
        """History limit parameter should cap the number of returned records."""
        _insert_propagation(verifier_db, prop_id="prop-l1")
        _insert_propagation(verifier_db, prop_id="prop-l2")
        verifier.verify_propagation("prop-l1")
        verifier.verify_propagation("prop-l2")
        # Each verify produces multiple checks; limit=2 should return at most 2
        history = verifier.get_history(limit=2)
        assert len(history) <= 2


# ---------------------------------------------------------------------------
# Append-only table enforcement (INSERT only, no UPDATE/DELETE)
# ---------------------------------------------------------------------------


class TestAppendOnly:
    def test_verifications_table_is_insert_only(self, verifier, verifier_db):
        """Verify that running verify twice creates new rows, never updates existing ones."""
        _insert_propagation(verifier_db)
        verifier.verify_propagation("prop-1")
        conn = sqlite3.connect(str(verifier_db))
        count_before = conn.execute("SELECT COUNT(*) FROM propagation_verifications").fetchone()[0]
        conn.close()

        # Run verification again — should INSERT new rows, not UPDATE
        verifier.verify_propagation("prop-1")
        conn = sqlite3.connect(str(verifier_db))
        count_after = conn.execute("SELECT COUNT(*) FROM propagation_verifications").fetchone()[0]
        conn.close()
        assert count_after > count_before, "Second verify should INSERT new rows"

    def test_verifications_unique_ids(self, verifier, verifier_db):
        """Each verification record should have a unique id (UUID)."""
        _insert_propagation(verifier_db)
        verifier.verify_propagation("prop-1")
        verifier.verify_propagation("prop-1")
        conn = sqlite3.connect(str(verifier_db))
        rows = conn.execute("SELECT id FROM propagation_verifications WHERE propagation_id = 'prop-1'").fetchall()
        conn.close()
        ids = [r[0] for r in rows]
        assert len(ids) == len(set(ids)), "All verification IDs must be unique (UUID)"

    def test_verifications_table_no_update_no_delete_in_source(self):
        """Verify the source code does not contain UPDATE or DELETE on propagation_verifications."""
        from pathlib import Path

        source = Path(__file__).resolve().parent.parent / "tools" / "registry" / "propagation_verifier.py"
        content = source.read_text(encoding="utf-8").upper()
        # Should have INSERT
        assert "INSERT INTO PROPAGATION_VERIFICATIONS" in content
        # Should NOT have UPDATE or DELETE
        assert "UPDATE PROPAGATION_VERIFICATIONS" not in content, (
            "propagation_verifications is append-only — no UPDATE allowed"
        )
        assert "DELETE FROM PROPAGATION_VERIFICATIONS" not in content, (
            "propagation_verifications is append-only — no DELETE allowed"
        )


# ---------------------------------------------------------------------------
# Error cases: nonexistent / edge cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_nonexistent_propagation_id_returns_error(self, verifier):
        """Verifying a nonexistent propagation_id returns structured error."""
        result = verifier.verify_propagation("prop-does-not-exist-999")
        assert result["error"] == "propagation_not_found"
        assert result["propagation_id"] == "prop-does-not-exist-999"

    def test_verify_with_empty_propagation_id(self, verifier):
        """Verifying with an empty string should return not found error."""
        result = verifier.verify_propagation("")
        assert result["error"] == "propagation_not_found"

    def test_verify_with_malformed_results_json(self, verifier, verifier_db):
        """Propagation with malformed execution_results_json should skip that check gracefully."""
        conn = sqlite3.connect(str(verifier_db))
        conn.execute(
            """INSERT INTO propagation_log
               (id, capability_id, capability_name, status, target_children_json,
                completed_at, execution_results_json, created_at)
               VALUES ('prop-bad', 'cap-1', 'test-cap', 'completed',
                       '["child-1"]', '2026-01-01T00:00:00Z', 'NOT_VALID_JSON', '2026-01-01')""",
        )
        conn.commit()
        conn.close()
        result = verifier.verify_propagation("prop-bad")
        exec_checks = [c for c in result["checks"] if c["name"] == "execution_results"]
        assert len(exec_checks) == 1
        assert exec_checks[0]["status"] == "skip"

    def test_verify_with_null_results_json(self, verifier, verifier_db):
        """Propagation with NULL execution_results_json should pass (defaults to empty, 0 failures)."""
        conn = sqlite3.connect(str(verifier_db))
        conn.execute(
            """INSERT INTO propagation_log
               (id, capability_id, capability_name, status, target_children_json,
                completed_at, execution_results_json, created_at)
               VALUES ('prop-null', 'cap-1', 'test-cap', 'completed',
                       '["child-1"]', '2026-01-01T00:00:00Z', NULL, '2026-01-01')""",
        )
        conn.commit()
        conn.close()
        result = verifier.verify_propagation("prop-null")
        exec_checks = [c for c in result["checks"] if c["name"] == "execution_results"]
        assert len(exec_checks) == 1
        # NULL defaults to {} via `or "{}"`, so fail_count=0, status=pass
        assert exec_checks[0]["status"] == "pass"

    def test_verify_prepared_status_returns_not_finished(self, verifier, verifier_db):
        """A propagation in 'prepared' status should return not_finished error."""
        _insert_propagation(verifier_db, status="prepared")
        result = verifier.verify_propagation("prop-1")
        assert result["error"] == "propagation_not_finished"
        assert result["status"] == "prepared"
