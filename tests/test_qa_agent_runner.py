# CUI // SP-CTI
"""Unit tests for icdev.tools.testing.qa_agent_runner.

Uses mock subprocess and mock DB — does not require a running Playwright
installation or a live PostgreSQL backend.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from icdev.tools.testing.qa_agent_runner import (
    QARunResult,
    TestFailure,
    discover_coverage_gaps,
    file_failure_tasks,
    generate_spec_stub,
    parse_playwright_json,
    record_failure,
    record_run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PLAYWRIGHT_JSON_PASS = json.dumps({
    "stats": {"expected": 3, "unexpected": 0, "skipped": 0},
    "suites": [
        {
            "title": "Dashboard health",
            "file": "tests/e2e/dashboard_health.spec.ts",
            "specs": [
                {
                    "title": "loads without error",
                    "tests": [{"results": [{"status": "passed", "attachments": []}]}],
                }
            ],
        }
    ],
})

_PLAYWRIGHT_JSON_FAIL = json.dumps({
    "stats": {"expected": 1, "unexpected": 2, "skipped": 0},
    "suites": [
        {
            "title": "Auth flow",
            "file": "tests/e2e/auth.spec.ts",
            "specs": [
                {
                    "title": "login page loads",
                    "tests": [
                        {
                            "results": [
                                {
                                    "status": "failed",
                                    "error": {"message": "getByRole('button', {name: 'Login'}) not found"},
                                    "attachments": [
                                        {
                                            "contentType": "image/png",
                                            "path": "playwright/screenshots/qa-agent/run1/auth_login.png",
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                },
                {
                    "title": "rls enforcement",
                    "tests": [
                        {
                            "results": [
                                {
                                    "status": "failed",
                                    "error": {"message": "Expected 403, got 200"},
                                    "attachments": [],
                                }
                            ]
                        }
                    ],
                },
            ],
        }
    ],
})


# ---------------------------------------------------------------------------
# parse_playwright_json
# ---------------------------------------------------------------------------

class TestParsePlaywrightJson(unittest.TestCase):
    def test_empty_string_returns_empty(self):
        assert parse_playwright_json("") == []

    def test_invalid_json_returns_empty(self):
        assert parse_playwright_json("{not json}") == []

    def test_passing_suite_returns_no_failures(self):
        failures = parse_playwright_json(_PLAYWRIGHT_JSON_PASS)
        assert failures == []

    def test_failing_suite_returns_failures(self):
        failures = parse_playwright_json(_PLAYWRIGHT_JSON_FAIL)
        assert len(failures) == 2

    def test_auth_test_gets_critical_severity(self):
        failures = parse_playwright_json(_PLAYWRIGHT_JSON_FAIL)
        auth_failures = [f for f in failures if "auth" in f.test_name.lower() or "login" in f.test_name.lower()]
        assert all(f.severity == "critical" for f in auth_failures)

    def test_rls_test_gets_critical_severity(self):
        failures = parse_playwright_json(_PLAYWRIGHT_JSON_FAIL)
        rls_failures = [f for f in failures if "rls" in f.test_name.lower()]
        assert all(f.severity == "critical" for f in rls_failures)

    def test_screenshot_extracted_from_attachments(self):
        failures = parse_playwright_json(_PLAYWRIGHT_JSON_FAIL)
        login_failure = next(f for f in failures if "login" in f.test_name.lower())
        assert "auth_login.png" in login_failure.screenshot_path

    def test_error_message_extracted(self):
        failures = parse_playwright_json(_PLAYWRIGHT_JSON_FAIL)
        login_failure = next(f for f in failures if "login" in f.test_name.lower())
        assert "getByRole" in login_failure.error_message


# ---------------------------------------------------------------------------
# generate_spec_stub
# ---------------------------------------------------------------------------

class TestGenerateSpecStub(unittest.TestCase):
    def test_returns_typescript_string(self):
        stub = generate_spec_stub("my_canvas", "My Canvas", "/my-canvas")
        assert "test.describe" in stub
        assert "My Canvas QA Smoke" in stub

    def test_contains_screenshot_call(self):
        stub = generate_spec_stub("my_canvas", "My Canvas", "/my-canvas")
        assert "playwright/screenshots/qa-agent/my_canvas_smoke.png" in stub

    def test_contains_cui_banner_check(self):
        stub = generate_spec_stub("my_canvas", "My Canvas", "/my-canvas")
        assert "CUI" in stub

    def test_contains_route(self):
        stub = generate_spec_stub("network", "Network Canvas", "/network")
        assert "'/network'" in stub

    def test_contains_iqe_check(self):
        stub = generate_spec_stub("dic", "DIC Canvas", "/dic")
        assert "iqe" in stub.lower()


# ---------------------------------------------------------------------------
# discover_coverage_gaps
# ---------------------------------------------------------------------------

class TestDiscoverCoverageGaps(unittest.TestCase):
    def test_returns_list_when_registry_missing(self):
        with patch("icdev.tools.testing.qa_agent_runner.PROJECT_ROOT", Path("/nonexistent")):
            gaps = discover_coverage_gaps()
        assert isinstance(gaps, list)

    def test_gap_structure(self):
        registry_content = {
            "canvases": [
                {"key": "zta_canvas", "display_name": "ZTA Canvas", "enabled": True, "route": "/security/zta"},
            ]
        }
        with (
            patch("icdev.tools.testing.qa_agent_runner.PROJECT_ROOT", PROJECT_ROOT),
            patch("builtins.open", unittest.mock.mock_open(read_data="")),
            patch("yaml.safe_load", return_value=registry_content),
            patch("glob.glob", return_value=[]),
        ):
            gaps = discover_coverage_gaps()
        assert all({"canvas_key", "display_name", "route", "enabled"}.issubset(g) for g in gaps)

    def test_disabled_canvas_excluded(self):
        registry_content = {
            "canvases": [
                {"key": "disabled_canvas", "display_name": "Disabled", "enabled": False},
            ]
        }
        with (
            patch("builtins.open", unittest.mock.mock_open(read_data="")),
            patch("yaml.safe_load", return_value=registry_content),
            patch("glob.glob", return_value=[]),
        ):
            gaps = discover_coverage_gaps()
        assert not any(g["canvas_key"] == "disabled_canvas" for g in gaps)


# ---------------------------------------------------------------------------
# file_failure_tasks
# ---------------------------------------------------------------------------

class TestFileFailureTasks(unittest.TestCase):
    def test_empty_failures_returns_empty(self):
        result = file_failure_tasks([], "run-123")
        assert result == []

    def test_creates_task_per_failure(self):
        failures = [
            TestFailure(test_name="auth > login", spec_file="auth.spec.ts", error_message="Not found", severity="critical"),
            TestFailure(test_name="dashboard > loads", spec_file="dash.spec.ts", error_message="500 error", severity="high"),
        ]
        with patch("tools.kanban.task_factory.create_tasks", return_value=["qa-fail-aaa", "qa-fail-bbb"]) as mock_create:
            ids = file_failure_tasks(failures, "run-001")
        mock_create.assert_called_once()
        specs_passed = mock_create.call_args[0][0]
        assert len(specs_passed) == 2

    def test_critical_failure_gets_critical_priority(self):
        failures = [
            TestFailure(test_name="auth > login", spec_file="auth.spec.ts", error_message="fail", severity="critical"),
        ]
        with patch("tools.kanban.task_factory.create_tasks", return_value=["qa-fail-001"]) as mock_create:
            file_failure_tasks(failures, "run-001")
        spec = mock_create.call_args[0][0][0]
        assert spec["priority"] == "critical"

    def test_high_failure_gets_high_priority(self):
        failures = [
            TestFailure(test_name="canvas > loads", spec_file="canvas.spec.ts", error_message="fail", severity="high"),
        ]
        with patch("tools.kanban.task_factory.create_tasks", return_value=["qa-fail-002"]) as mock_create:
            file_failure_tasks(failures, "run-001")
        spec = mock_create.call_args[0][0][0]
        assert spec["priority"] == "high"

    def test_idempotency_key_set(self):
        failures = [TestFailure(test_name="X > Y", error_message="fail")]
        with patch("tools.kanban.task_factory.create_tasks", return_value=[]) as mock_create:
            file_failure_tasks(failures, "run-001")
        spec = mock_create.call_args[0][0][0]
        assert spec.get("idempotency_key")

    def test_import_error_returns_empty(self):
        with patch.dict("sys.modules", {"tools.kanban.task_factory": None}):
            result = file_failure_tasks(
                [TestFailure(test_name="X", error_message="fail")], "run-x"
            )
        assert result == []


# ---------------------------------------------------------------------------
# record_run / record_failure (DB mocked)
# ---------------------------------------------------------------------------

class TestRecordRun(unittest.TestCase):
    def _make_result(self) -> QARunResult:
        return QARunResult(
            run_id="qa-12345",
            trigger="deploy.complete",
            status="failed",
            total=5,
            passed=3,
            failed=2,
        )

    def test_returns_run_id(self):
        mock_conn = MagicMock()
        with patch("icdev.tools.db.storage.get_canvas_connection", return_value=mock_conn):
            run_id = record_run(self._make_result())
        assert run_id == "qa-12345"

    def test_connection_closed_on_success(self):
        mock_conn = MagicMock()
        with patch("icdev.tools.db.storage.get_canvas_connection", return_value=mock_conn):
            record_run(self._make_result())
        mock_conn.close.assert_called_once()

    def test_connection_closed_on_error(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("DB error")
        with patch("icdev.tools.db.storage.get_canvas_connection", return_value=mock_conn):
            # Should not raise — falls back to SQLite dialect
            record_run(self._make_result())
        mock_conn.close.assert_called_once()


class TestRecordFailure(unittest.TestCase):
    def test_returns_failure_id_string(self):
        mock_conn = MagicMock()
        failure = TestFailure(test_name="auth > login", error_message="not found")
        with patch("icdev.tools.db.storage.get_canvas_connection", return_value=mock_conn):
            fid = record_failure(failure, "run-123", "qa-fail-001")
        assert isinstance(fid, str) and len(fid) > 0

    def test_deterministic_id_for_same_input(self):
        mock_conn = MagicMock()
        failure = TestFailure(test_name="X > Y", error_message="fail")
        with patch("icdev.tools.db.storage.get_canvas_connection", return_value=mock_conn):
            id1 = record_failure(failure, "run-abc")
            id2 = record_failure(failure, "run-abc")
        assert id1 == id2

    def test_connection_closed_always(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("boom")
        failure = TestFailure(test_name="X", error_message="fail")
        with patch("icdev.tools.db.storage.get_canvas_connection", return_value=mock_conn):
            record_failure(failure, "run-x")
        mock_conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
