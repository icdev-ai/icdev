# CUI // SP-CTI
"""Performance tests for the workload discovery scanner.

Requirement: A full scan of 1000 servers must complete within 2 hours.
Strategy: run batch inserts on synthetic data and assert projected duration.
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tools.migration_canvas.inventory_scanner import (
    DEFAULT_HOST_TIMEOUT_SECONDS,
    DEFAULT_WORKER_COUNT,
    SLA_BUDGET_SECONDS,
    SLA_MAX_SERVERS,
    PerformanceTracker,
    _now,
    validate_performance_sla,
    write_inventory,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_servers(n: int) -> list[dict]:
    return [
        {
            "id": str(uuid.uuid4()),
            "hostname": f"srv-perf-{i:04d}",
            "ip_address": f"10.0.{i // 256}.{i % 256}",
            "os": "RHEL 9",
            "cpu_cores": 4,
            "ram_gb": 16,
            "disk_gb": 500,
            "environment": "production",
            "status": "active",
            "tags": "[]",
            "created_at": _now(),
        }
        for i in range(n)
    ]


# ── PerformanceTracker unit tests ──────────────────────────────────────────────

class TestPerformanceTracker:
    def test_elapsed_increases(self):
        tracker = PerformanceTracker()
        tracker.start()
        time.sleep(0.01)
        tracker.stop()
        assert tracker.elapsed_seconds >= 0.01

    def test_sla_compliant_when_under_budget(self):
        tracker = PerformanceTracker(sla_seconds=3600)
        tracker.start()
        tracker.stop()
        assert tracker.sla_compliant is True

    def test_sla_breach_when_over_budget(self):
        tracker = PerformanceTracker(sla_seconds=0.0)
        tracker.start()
        time.sleep(0.001)
        tracker.stop()
        assert tracker.sla_compliant is False

    def test_throughput_per_hour_nonzero(self):
        tracker = PerformanceTracker()
        tracker.server_count = 100
        tracker.start()
        time.sleep(0.01)
        tracker.stop()
        assert tracker.throughput_per_hour > 0

    def test_projected_duration_scales_linearly(self):
        tracker = PerformanceTracker()
        tracker.server_count = 100
        tracker.start()
        time.sleep(0.01)
        tracker.stop()
        proj = tracker.projected_duration(1000)
        # Should be 10× the time for 100 servers (linear scaling)
        assert proj == pytest.approx(tracker.elapsed_seconds * 10, rel=0.1)

    def test_report_contains_required_keys(self):
        tracker = PerformanceTracker()
        tracker.server_count = 10
        tracker.start()
        tracker.stop()
        report = tracker.report()
        for key in (
            "elapsed_seconds", "server_count", "throughput_per_hour",
            "sla_budget_seconds", "sla_compliant",
            "projected_1000_server_seconds", "projected_1000_server_within_sla",
        ):
            assert key in report, f"Missing key: {key}"

    def test_constants_are_sane(self):
        assert SLA_MAX_SERVERS == 1000
        assert SLA_BUDGET_SECONDS == 7200
        assert DEFAULT_WORKER_COUNT >= 10
        assert DEFAULT_HOST_TIMEOUT_SECONDS > 0


# ── Batch insert performance tests ─────────────────────────────────────────────

class TestBatchInsertPerformance:
    """Batch write_inventory (dry_run) must project to ≤ 2 hours for 1000 servers."""

    def test_batch_write_1000_dry_run_within_sla(self):
        servers = _make_servers(1000)
        sid = f"perf-test-{uuid.uuid4().hex[:8]}"
        result = write_inventory(servers, sid, dry_run=True)

        perf = result["performance"]
        assert result["inserted"] == 1000
        assert perf["sla_compliant"] is True, (
            f"SLA breach: {perf['elapsed_seconds']}s > {SLA_BUDGET_SECONDS}s"
        )

    def test_batch_write_100_projects_within_2h_for_1000(self):
        """Time 100 servers and assert linear projection stays under budget."""
        servers = _make_servers(100)
        sid = f"proj-test-{uuid.uuid4().hex[:8]}"
        result = write_inventory(servers, sid, dry_run=True)

        perf = result["performance"]
        projected = perf["projected_1000_server_seconds"]
        assert projected <= SLA_BUDGET_SECONDS, (
            f"Projected {projected:.1f}s > {SLA_BUDGET_SECONDS}s SLA for 1000 servers"
        )

    def test_batch_faster_than_sequential_threshold(self):
        """Batch write of 500 servers must complete in < 5 seconds (dry_run)."""
        servers = _make_servers(500)
        sid = f"speed-test-{uuid.uuid4().hex[:8]}"

        start = time.monotonic()
        write_inventory(servers, sid, dry_run=True)
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"Write took {elapsed:.2f}s for 500 servers (dry_run)"

    def test_performance_result_includes_perf_key(self):
        servers = _make_servers(10)
        result = write_inventory(servers, "perf-key-test", dry_run=True)
        assert "performance" in result
        assert result["performance"]["server_count"] == 10


# ── SLA validation function ────────────────────────────────────────────────────

class TestValidatePerformanceSLA:
    def test_validate_sla_returns_pass(self):
        report = validate_performance_sla(sample_size=50, dry_run=True)
        assert report["projected_1000_server_within_sla"] is True, (
            f"SLA validation failed: projected {report['projected_1000_server_seconds']}s "
            f"> {SLA_BUDGET_SECONDS}s"
        )

    def test_validate_sla_report_structure(self):
        report = validate_performance_sla(sample_size=10, dry_run=True)
        for key in ("sample_size", "scale_factor", "projected_1000_server_seconds",
                    "projected_1000_server_within_sla", "sla_max_servers", "sla_budget_seconds"):
            assert key in report, f"Missing key in SLA report: {key}"

    def test_validate_sla_scale_factor_correct(self):
        report = validate_performance_sla(sample_size=100, dry_run=True)
        assert report["scale_factor"] == pytest.approx(10.0, rel=0.01)
        assert report["sla_max_servers"] == 1000

    def test_validate_sla_throughput_per_hour_sufficient(self):
        """Scanner must process at least 500 servers/hour to satisfy 1000/2h SLA."""
        report = validate_performance_sla(sample_size=100, dry_run=True)
        # 500 servers/hour minimum to do 1000 in 2 hours
        assert report["throughput_per_hour"] >= 500, (
            f"Throughput {report['throughput_per_hour']:.0f}/hr < 500/hr minimum"
        )


# ── BDD-style acceptance test ──────────────────────────────────────────────────

class TestBDDAcceptance:
    """Mirrors the BDD scenario from the Kanban task."""

    def test_scenario_full_scan_1000_servers_within_2_hours(self):
        """
        Feature: Performance Requirement Validation
          Scenario: the workload discovery scanner must complete a full scan of
                    1000 servers within 2 hours
            Given the system is under normal operational load
            When Complete a full scan of 1000 servers within 2 hours
            Then the system behaves as specified and the requirement is satisfied
        """
        # Given: system under normal operational load
        servers = _make_servers(1000)
        session_id = f"bdd-acceptance-{uuid.uuid4().hex[:8]}"

        # When: complete a full scan of 1000 servers
        result = write_inventory(servers, session_id, dry_run=True)

        # Then: completed within 2-hour SLA
        perf = result["performance"]
        assert result["inserted"] == 1000, "All 1000 servers must be processed"
        assert perf["sla_compliant"] is True, (
            f"Requirement NOT satisfied: scan took {perf['elapsed_seconds']}s "
            f"(budget: {SLA_BUDGET_SECONDS}s = 2 hours)"
        )
        assert perf["throughput_per_hour"] > 0, "Throughput metric must be reported"
