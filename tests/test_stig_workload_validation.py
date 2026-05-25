# CUI // SP-CTI
"""Acceptance tests for STIG workload checklist validation.

BDD Scenario: The compliance checker must validate a single workload STIG
checklist in under 5 minutes.

  Given  the system enforces security controls per the accreditation boundary
  When   validate a single workload STIG checklist
  Then   the system behaves as specified and completes within 300 seconds
"""

import time
import pytest
from unittest.mock import patch

from tools.govlift.stig_checker import (
    validate_workload_checklist,
    STIG_CHECK_STATUSES,
)

# Re-export constants referenced from the module-level import
from tools.govlift.constants import STIG_SEVERITIES

WORKLOAD_ID = "wl-test-stig-validation"
TIMEOUT_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_get_connection(tmp_path):
    """Replace get_connection with a temp-file SQLite database for isolation."""
    import sqlite3

    db_path = tmp_path / "test_stig.db"

    def make_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS govlift_stig_checks (
                id TEXT PRIMARY KEY,
                workload_id TEXT NOT NULL,
                stig_benchmark TEXT,
                check_id TEXT,
                check_name TEXT,
                severity TEXT DEFAULT 'cat2',
                status TEXT DEFAULT 'not_reviewed',
                finding TEXT,
                remediation TEXT,
                checked_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()
        return conn

    def _get_connection(**_kwargs):
        conn = make_conn()
        print(f"[_get_connection] created conn {id(conn)}, closed={conn.total_changes}", file=__import__('sys').stderr)
        return conn

    _original_translate_sql = lambda sql, *a, **k: sql

    def _translate_sql(sql, *args, **kwargs):
        return sql

    with patch("tools.govlift.stig_checker.get_connection", _get_connection), \
         patch("tools.govlift.stig_checker.translate_sql", _translate_sql):
        yield


# ---------------------------------------------------------------------------
# Scenario: validate a single workload STIG checklist in under 5 minutes
# ---------------------------------------------------------------------------

class TestSTIGWorkloadValidation:
    """Given the system enforces security controls per the accreditation boundary."""

    def test_validation_completes_within_budget(self, _patch_get_connection):
        """When validate_workload_checklist runs, it finishes under 300 seconds."""
        result = validate_workload_checklist(WORKLOAD_ID, timeout_seconds=TIMEOUT_SECONDS)

        assert result["within_budget"] is True, (
            f"Validation exceeded {TIMEOUT_SECONDS}s budget: "
            f"elapsed={result['elapsed_seconds']}s"
        )
        assert result["elapsed_seconds"] < TIMEOUT_SECONDS

    def test_result_contains_required_fields(self, _patch_get_connection):
        """Then the result shape matches the accreditation boundary specification."""
        result = validate_workload_checklist(WORKLOAD_ID)

        required = {
            "workload_id", "benchmark", "check_count", "elapsed_seconds",
            "within_budget", "summary", "cat1_open", "gate_passed", "checks",
        }
        assert required.issubset(result.keys()), (
            f"Missing fields: {required - result.keys()}"
        )

    def test_workload_id_echoed(self, _patch_get_connection):
        """Then the result references the correct workload."""
        result = validate_workload_checklist(WORKLOAD_ID)
        assert result["workload_id"] == WORKLOAD_ID

    def test_summary_covers_all_severities(self, _patch_get_connection):
        """Then summary covers all DoD CAT severity levels."""
        result = validate_workload_checklist(WORKLOAD_ID)
        for sev in STIG_SEVERITIES:
            assert sev in result["summary"], f"Severity '{sev}' missing from summary"

    def test_summary_covers_all_statuses(self, _patch_get_connection):
        """Then each severity bucket has counts for every disposition status."""
        result = validate_workload_checklist(WORKLOAD_ID)
        for sev in STIG_SEVERITIES:
            for st in STIG_CHECK_STATUSES:
                assert st in result["summary"][sev], (
                    f"Status '{st}' missing from summary['{sev}']"
                )

    def test_check_count_matches_checks_list(self, _patch_get_connection):
        """Then check_count is consistent with the returned checks list."""
        result = validate_workload_checklist(WORKLOAD_ID)
        assert result["check_count"] == len(result["checks"])

    def test_gate_reflects_cat1_open(self, _patch_get_connection):
        """Then gate_passed is False when any CAT1 finding is open."""
        result = validate_workload_checklist(WORKLOAD_ID)
        expected_gate = result["cat1_open"] == 0
        assert result["gate_passed"] is expected_gate, (
            f"gate_passed={result['gate_passed']} but cat1_open={result['cat1_open']}"
        )

    def test_seeded_when_no_prior_checks(self, _patch_get_connection):
        """Then the validator auto-seeds checks when the workload has none."""
        result = validate_workload_checklist("wl-brand-new-workload")
        # run_quick_scan seeds 10 sample checks
        assert result["check_count"] > 0

    def test_benchmark_filter_accepted(self, _patch_get_connection):
        """Then passing a benchmark filter does not raise and echoes the filter."""
        result = validate_workload_checklist(
            WORKLOAD_ID, benchmark="RHEL-09-STIG-V1R1"
        )
        assert result["benchmark"] == "RHEL-09-STIG-V1R1"

    def test_validation_is_fast_in_practice(self, _patch_get_connection):
        """Then a single-workload validation completes well under 5 minutes
        (practical wall-clock check — must finish in under 10 seconds)."""
        t0 = time.monotonic()
        validate_workload_checklist(WORKLOAD_ID)
        elapsed = time.monotonic() - t0
        assert elapsed < 10, (
            f"Validation too slow for a single workload: {elapsed:.2f}s "
            "(should be near-instant, well inside the 5-minute budget)"
        )
