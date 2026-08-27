# CUI // SP-CTI
"""Unit tests for icdev.tools.testing.qa_agent_runner.

Uses mock subprocess and mock DB — does not require a running Playwright
installation or a live PostgreSQL backend.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from icdev.tools.testing.qa_agent_runner import (
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_NO_TESTS,
    STATUS_PASSED,
    QARunResult,
    TestFailure,
    _DEADLINE_SECONDS,
    _tally,
    batch_specs,
    build_playwright_cmd,
    derive_status,
    discover_coverage_gaps,
    file_failure_tasks,
    generate_spec_stub,
    parse_playwright_json,
    record_failure,
    record_run,
    resolve_spec_files,
    run_e2e_suite,
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


# A report in the shape Playwright ACTUALLY writes: the file suite carries the
# file name and NO specs; the `test.describe` suite under it carries them.
# Every spec under tests/e2e/ is inside a describe block, so this -- not the
# flat fixtures above -- is what `--run` parses on every batch.
_PLAYWRIGHT_JSON_NESTED_FAIL = json.dumps({
    "stats": {"expected": 2, "unexpected": 1, "skipped": 0},
    "suites": [
        {
            "title": "coworker_lifecycle.spec.ts",
            "file": "coworker_lifecycle.spec.ts",
            "specs": [],
            "suites": [
                {
                    "title": "ACE Co-Worker Engine Lifecycle",
                    "file": "coworker_lifecycle.spec.ts",
                    "specs": [
                        {
                            "title": "GET /coworker/<id> instance detail loads",
                            "tests": [
                                {
                                    "results": [
                                        {
                                            "status": "failed",
                                            "error": {"message": "Expected substring: \"CUI // SP-CTI\""},
                                            "attachments": [
                                                {
                                                    "name": "screenshot",
                                                    "contentType": "image/png",
                                                    "path": "C:/x/test-results/cw-detail/test-failed-1.png",
                                                }
                                            ],
                                        }
                                    ]
                                }
                            ],
                        },
                        {
                            "title": "GET /coworker/ lists instances",
                            "tests": [{"results": [{"status": "passed", "attachments": []}]}],
                        },
                    ],
                }
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

    def test_nested_describe_suite_failure_is_found(self):
        """The walker must descend: a one-level walk finds ZERO specs in every
        real report (measured 2026-08-22: 6 file suites per batch, 0 specs
        each, 1 child each) and a red sweep parses as green."""
        failures = parse_playwright_json(_PLAYWRIGHT_JSON_NESTED_FAIL)
        assert len(failures) == 1
        f = failures[0]
        assert f.test_name == "ACE Co-Worker Engine Lifecycle > GET /coworker/<id> instance detail loads"
        assert f.spec_file == "coworker_lifecycle.spec.ts"
        assert f.screenshot_path.endswith("test-failed-1.png")
        assert "CUI // SP-CTI" in f.error_message

    def test_nested_suite_inherits_file_from_its_ancestor(self):
        report = json.loads(_PLAYWRIGHT_JSON_NESTED_FAIL)
        report["suites"][0]["suites"][0].pop("file")
        failures = parse_playwright_json(json.dumps(report))
        assert [f.spec_file for f in failures] == ["coworker_lifecycle.spec.ts"]

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
            file_failure_tasks(failures, "run-001")
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

    def test_task_type_is_accepted_by_the_real_validator(self):
        """The filed task_type must be one create_tasks will actually accept.

        It hardcoded "bug", which is not in VALID_TASK_TYPES — create_tasks
        raises before any insert, so file_failure_tasks had never filed a task.
        Asserted against the live constant, not the literal "fix", so any other
        rejected value fails here too.
        """
        from icdev.tools.kanban.task_factory import VALID_TASK_TYPES

        failures = [TestFailure(test_name="auth > login", error_message="fail")]
        with patch("tools.kanban.task_factory.create_tasks", return_value=[]) as mock_create:
            file_failure_tasks(failures, "run-001")
        spec = mock_create.call_args[0][0][0]
        assert spec["task_type"] in VALID_TASK_TYPES

    def test_no_failure_spec_uses_the_rejected_bug_type(self):
        failures = [
            TestFailure(test_name="a", error_message="x"),
            TestFailure(test_name="b", error_message="y", severity="critical"),
        ]
        with patch("tools.kanban.task_factory.create_tasks", return_value=[]) as mock_create:
            file_failure_tasks(failures, "run-002")
        specs = mock_create.call_args[0][0]
        assert all(s["task_type"] != "bug" for s in specs)


# ---------------------------------------------------------------------------
# build_playwright_cmd — the canvas filter could not run at all
# ---------------------------------------------------------------------------

class TestBuildPlaywrightCmd(unittest.TestCase):
    def test_project_flag_is_a_single_token(self):
        """`--project chromium <spec>` makes Playwright read the spec path as a
        second PROJECT name and die with `Project(s) "..." not found`."""
        cmd = build_playwright_cmd("npx", ["tests/e2e/a.spec.ts"])
        assert "--project=chromium" in cmd
        assert "--project" not in cmd

    def test_spec_files_follow_the_project_flag(self):
        cmd = build_playwright_cmd("npx", ["tests/e2e/a.spec.ts", "tests/e2e/b.spec.ts"])
        assert cmd[:4] == ["npx", "playwright", "test", "--project=chromium"]
        assert cmd[4:] == ["tests/e2e/a.spec.ts", "tests/e2e/b.spec.ts"]

    def test_does_not_override_the_configs_reporter(self):
        """A CLI --reporter REPLACES playwright.config.ts's reporter list, which
        is what writes the ICDEV_PW_RUN_TAG-suffixed json report we read back."""
        cmd = build_playwright_cmd("npx", ["tests/e2e/a.spec.ts"])
        assert "--reporter" not in cmd
        assert not any(a.startswith("--reporter") for a in cmd)


# ---------------------------------------------------------------------------
# resolve_spec_files — a bare Playwright arg is a REGEX, not a path
# ---------------------------------------------------------------------------

class TestResolveSpecFiles(unittest.TestCase):
    def test_paths_are_relative_and_forward_slashed(self):
        """An absolute Windows path (backslashes, a drive colon) matches no test
        file: Playwright exits "No tests found" and still writes a 0/0/0 report."""
        specs = resolve_spec_files()
        assert specs, "expected tests/e2e/*.spec.ts to exist in this checkout"
        for path in specs:
            assert not os.path.isabs(path), path
            assert "\\" not in path, path
            assert ":" not in path, path
            assert path.startswith("tests/e2e/"), path

    def test_canvas_filter_narrows_the_set(self):
        every = resolve_spec_files()
        filtered = resolve_spec_files("dashboard")
        assert set(filtered).issubset(set(every))
        assert all("dashboard" in p for p in filtered)

    def test_unmatched_canvas_returns_empty(self):
        assert resolve_spec_files("no-such-canvas-zzz") == []


# ---------------------------------------------------------------------------
# batch_specs
# ---------------------------------------------------------------------------

class TestBatchSpecs(unittest.TestCase):
    def test_covers_every_spec_exactly_once(self):
        specs = [f"tests/e2e/{i}.spec.ts" for i in range(13)]
        batches = batch_specs(specs, 5)
        assert [s for b in batches for s in b] == specs

    def test_respects_batch_size(self):
        batches = batch_specs([f"{i}" for i in range(13)], 5)
        assert [len(b) for b in batches] == [5, 5, 3]

    def test_empty_input_yields_no_batches(self):
        assert batch_specs([], 5) == []

    def test_zero_batch_size_does_not_loop_forever(self):
        assert batch_specs(["a", "b"], 0) == [["a"], ["b"]]


# ---------------------------------------------------------------------------
# derive_status — a run that measured nothing is never `passed`
# ---------------------------------------------------------------------------

class TestDeriveStatus(unittest.TestCase):
    def test_zero_test_report_is_not_passed(self):
        """Playwright writes a 0/0/0 report when its path regex matched nothing.
        Tallied as `passed`, that is indistinguishable from a green suite."""
        result = QARunResult(spec_files_total=3, spec_files_run=["a", "b", "c"])
        assert derive_status(result) == STATUS_NO_TESTS
        assert derive_status(result) != STATUS_PASSED

    def test_unreached_spec_files_make_the_run_incomplete(self):
        result = QARunResult(total=10, passed=10, spec_files_not_run=["tests/e2e/z.spec.ts"])
        assert derive_status(result) == STATUS_INCOMPLETE

    def test_missing_report_makes_the_run_incomplete(self):
        result = QARunResult(total=10, passed=10, spec_files_no_report=["tests/e2e/z.spec.ts"])
        assert derive_status(result) == STATUS_INCOMPLETE

    def test_failures_win_over_incompleteness(self):
        result = QARunResult(total=10, passed=9, failed=1, spec_files_not_run=["x"])
        assert derive_status(result) == STATUS_FAILED

    def test_full_green_sweep_is_passed(self):
        result = QARunResult(total=10, passed=10, spec_files_total=2, spec_files_run=["a", "b"])
        assert derive_status(result) == STATUS_PASSED


# ---------------------------------------------------------------------------
# _tally — accumulates ACROSS batches
# ---------------------------------------------------------------------------

class TestTally(unittest.TestCase):
    def test_accumulates_across_batches(self):
        result = QARunResult()
        _tally({"stats": {"expected": 3, "unexpected": 1, "skipped": 2}}, result)
        _tally({"stats": {"expected": 5, "unexpected": 0, "skipped": 1}}, result)
        assert result.total == 12
        assert result.passed == 8
        assert result.skipped == 3

    def test_missing_stats_contribute_nothing(self):
        result = QARunResult()
        _tally({}, result)
        assert (result.total, result.passed, result.skipped, result.failed) == (0, 0, 0, 0)

    def test_unexpected_is_counted_as_failed(self):
        """`failed` is Playwright's own `unexpected`, never the parser's count.
        On the 2026-08-22 sweep `failed` read 0 against 31 unexpected and the
        run was recorded `passed`."""
        result = QARunResult()
        _tally({"stats": {"expected": 88, "unexpected": 12, "skipped": 8}}, result)
        _tally({"stats": {"expected": 60, "unexpected": 7, "skipped": 0}}, result)
        assert result.failed == 19
        assert derive_status(result) == STATUS_FAILED


# ---------------------------------------------------------------------------
# run_e2e_suite — the deadline
# ---------------------------------------------------------------------------

class TestRunE2ESuiteDeadline(unittest.TestCase):
    def test_default_deadline_exceeds_the_measured_suite_duration(self):
        """playwright.config.ts records the full suite at 41.5m on one worker.
        The old 1200s budget was under half of that, so --run ALWAYS timed out
        and returned a single synthetic TestFailure(test_name="timeout")."""
        measured_seconds = 41.5 * 60
        assert _DEADLINE_SECONDS > measured_seconds

    def test_no_matching_spec_is_no_tests_not_passed(self):
        result = run_e2e_suite(canvas_filter="no-such-canvas-zzz")
        assert result.status == STATUS_NO_TESTS
        assert result.spec_files_total == 0

    def test_deadline_names_every_spec_file_it_did_not_reach(self):
        """A truncated sweep that reported only its successes reads as full
        coverage. The unmeasured spec files must come back NAMED."""
        specs = [f"tests/e2e/s{i}.spec.ts" for i in range(4)]
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch(
                "icdev.tools.testing.qa_agent_runner.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="npx", timeout=1),
            ),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=2)
        assert sorted(result.spec_files_not_run) == sorted(specs)
        assert result.spec_files_run == []
        assert result.status == STATUS_INCOMPLETE

    def test_deadline_no_longer_collapses_to_one_synthetic_timeout_failure(self):
        specs = [f"tests/e2e/s{i}.spec.ts" for i in range(4)]
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch(
                "icdev.tools.testing.qa_agent_runner.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="npx", timeout=1),
            ),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=2)
        assert [f.test_name for f in result.failures] != ["timeout"]

    def test_finished_batches_survive_a_later_deadline_kill(self):
        """The point of batching: a batch that completed keeps its real report
        even though the sweep was cut short."""
        specs = [f"tests/e2e/s{i}.spec.ts" for i in range(4)]
        report = json.dumps({"stats": {"expected": 2, "unexpected": 0, "skipped": 0}, "suites": []})
        good = MagicMock(returncode=0, stdout=report, stderr="")
        calls = {"n": 0}

        def _run(*_a, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return good
            raise subprocess.TimeoutExpired(cmd="npx", timeout=1)

        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", side_effect=_run),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=2)

        assert result.spec_files_run == specs[:2]
        assert result.spec_files_not_run == specs[2:]
        assert result.total == 2 and result.passed == 2
        assert result.status == STATUS_INCOMPLETE

    def test_batch_producing_no_report_is_not_counted_as_coverage(self):
        specs = ["tests/e2e/s0.spec.ts", "tests/e2e/s1.spec.ts"]
        empty = MagicMock(returncode=1, stdout="", stderr="boom")
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", return_value=empty),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=2)
        assert result.spec_files_no_report == specs
        assert result.spec_files_run == []
        assert result.status == STATUS_INCOMPLETE

    def test_zero_test_batch_carries_the_reason_it_ran_nothing(self):
        """`no_tests` on its own is a shrug. A webServer that never came up and
        an empty suite are different fixes, and the report says which."""
        specs = ["tests/e2e/s0.spec.ts"]
        report = json.dumps({
            "stats": {"expected": 0, "unexpected": 0, "skipped": 0},
            "errors": [{"message": "Error: Timed out waiting 60000ms from config.webServer."}],
            "suites": [],
        })
        proc = MagicMock(returncode=1, stdout=report, stderr="")
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", return_value=proc),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=1)
        assert result.status == STATUS_NO_TESTS
        assert any("webServer" in e for e in result.batches[0]["errors"])

    def test_nested_failing_report_makes_the_run_failed(self):
        """The 2026-08-22 sweep: 11 batches, 31 `unexpected`, reported
        `status=passed failed=0` because nothing below the file suite was read."""
        specs = ["tests/e2e/coworker_lifecycle.spec.ts"]
        proc = MagicMock(returncode=1, stdout=_PLAYWRIGHT_JSON_NESTED_FAIL, stderr="")
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", return_value=proc),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=1)
        assert result.status == STATUS_FAILED
        assert result.failed == 1
        assert [f.test_name for f in result.failures] == [
            "ACE Co-Worker Engine Lifecycle > GET /coworker/<id> instance detail loads"
        ]
        assert result.failures_unparsed == 0

    def test_unexpected_the_parser_cannot_name_still_fails_the_run(self):
        """A parser blind spot must be REPORTED as unnamed failures, never
        resolved in favour of the parser: Playwright said red."""
        specs = ["tests/e2e/s0.spec.ts"]
        report = json.dumps({"stats": {"expected": 0, "unexpected": 1, "skipped": 0}, "suites": []})
        proc = MagicMock(returncode=1, stdout=report, stderr="")
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", return_value=proc),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=1)
        assert result.status == STATUS_FAILED
        assert result.failed == 1
        assert result.failures == []
        assert result.failures_unparsed == 1

    def test_run_uses_the_single_token_project_flag(self):
        specs = ["tests/e2e/s0.spec.ts"]
        report = json.dumps({"stats": {"expected": 1, "unexpected": 0, "skipped": 0}, "suites": []})
        proc = MagicMock(returncode=0, stdout=report, stderr="")
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", return_value=proc) as run_mock,
        ):
            run_e2e_suite(deadline_seconds=600, batch_size=1)
        argv = run_mock.call_args[0][0]
        assert "--project=chromium" in argv
        assert "--project" not in argv
        assert argv[-1] == "tests/e2e/s0.spec.ts"


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

class TestCLIFlags(unittest.TestCase):
    def test_deadline_and_batch_size_are_settable(self):
        from icdev.tools.testing.qa_agent_runner import main

        captured = {}

        def _fake(**kwargs):
            captured.update(kwargs)
            return QARunResult(run_id="qa-1", status=STATUS_PASSED)

        argv = [
            "qa_agent_runner.py", "--run",
            "--deadline-seconds", "1800", "--batch-size", "3",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch("icdev.tools.testing.qa_agent_runner.run_e2e_suite", side_effect=_fake),
        ):
            rc = main()
        assert rc == 0
        assert captured["deadline_seconds"] == 1800
        assert captured["batch_size"] == 3

    def test_no_tests_run_exits_nonzero(self):
        from icdev.tools.testing.qa_agent_runner import main

        with (
            patch.object(sys, "argv", ["qa_agent_runner.py", "--run"]),
            patch(
                "icdev.tools.testing.qa_agent_runner.run_e2e_suite",
                return_value=QARunResult(run_id="qa-1", status=STATUS_NO_TESTS),
            ),
        ):
            assert main() == 1


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


class TestMainPersistsTheRun(unittest.TestCase):
    """`--run` must PERSIST the sweep, and must not file cards uninvited.

    task-qa-sweep-96fa73e9: `main()` ran the whole 65-file suite — 770 tests,
    ~41m of wall clock — and called neither `record_run` nor
    `file_failure_tasks`, so the documented CLI measured everything and wrote
    nothing an hour later anyone could cite. Both seams existed, were exported,
    were tested in isolation, and were reachable from no runtime path: the
    declared-but-unconsumed shape, one layer down.

    Filing stays OPT-IN. One shared cause becomes N `qa-fail-*` cards and N
    duplicate PRs, so a CLI that files by default is a board-spam generator.
    """

    def _module(self):
        import importlib

        return importlib.import_module("icdev.tools.testing.qa_agent_runner")

    def _run_main(self, argv, result):
        mod = self._module()
        calls = {"record": [], "file": []}

        def fake_run_e2e_suite(**kwargs):
            return result

        def fake_record_run(res):
            calls["record"].append(res)
            return res.run_id

        def fake_file_failure_tasks(failures, run_id, instance_id=""):
            calls["file"].append((failures, run_id))
            return ["qa-fail-deadbeef"]

        with patch.object(mod, "run_e2e_suite", fake_run_e2e_suite),                 patch.object(mod, "record_run", fake_record_run),                 patch.object(mod, "file_failure_tasks", fake_file_failure_tasks),                 patch.object(sys, "argv", ["qa_agent_runner.py", *argv]):
            rc = mod.main()
        return rc, calls

    def _passing_result(self):
        r = QARunResult(run_id="qa-test-1", trigger="unit")
        r.status = STATUS_PASSED
        r.total = 3
        r.passed = 3
        return r

    def _failing_result(self):
        r = QARunResult(run_id="qa-test-2", trigger="unit")
        r.status = STATUS_FAILED
        r.total = 2
        r.passed = 1
        r.failed = 1
        r.failures = [TestFailure(test_name="a > b", error_message="boom")]
        return r

    def test_run_records_the_sweep_by_default(self):
        rc, calls = self._run_main(["--run"], self._passing_result())
        assert rc == 0
        assert len(calls["record"]) == 1, "main() --run did not call record_run"
        assert calls["record"][0].run_id == "qa-test-1"

    def test_no_record_opts_out(self):
        _, calls = self._run_main(["--run", "--no-record"], self._passing_result())
        assert calls["record"] == []

    def test_failures_do_not_file_cards_unless_asked(self):
        rc, calls = self._run_main(["--run"], self._failing_result())
        assert rc == 1
        assert len(calls["record"]) == 1
        assert calls["file"] == [], "failures filed kanban cards without --file-failures"

    def test_file_failures_flag_files_them(self):
        _, calls = self._run_main(["--run", "--file-failures"], self._failing_result())
        assert len(calls["file"]) == 1
        failures, run_id = calls["file"][0]
        assert run_id == "qa-test-2"
        assert failures[0].test_name == "a > b"

    def test_json_output_reports_both_outcomes(self):
        mod = self._module()
        result = self._passing_result()
        buf = io.StringIO()
        with patch.object(mod, "run_e2e_suite", lambda **kw: result),                 patch.object(mod, "record_run", lambda res: res.run_id),                 patch.object(sys, "argv", ["qa_agent_runner.py", "--run", "--json"]),                 contextlib.redirect_stdout(buf):
            mod.main()
        payload = json.loads(buf.getvalue())
        assert payload["persistence"]["recorded"] == "qa-test-1"
        # Never attempted is not the same as attempted and failed.
        assert payload["persistence"]["filed_tasks"] is None
        assert payload["persistence"]["record_error"] is None

    def test_a_record_failure_is_reported_never_swallowed(self):
        mod = self._module()
        result = self._passing_result()
        buf = io.StringIO()

        def boom(_res):
            raise RuntimeError("ace db unreachable")

        with patch.object(mod, "run_e2e_suite", lambda **kw: result),                 patch.object(mod, "record_run", boom),                 patch.object(sys, "argv", ["qa_agent_runner.py", "--run", "--json"]),                 contextlib.redirect_stdout(buf):
            rc = mod.main()
        payload = json.loads(buf.getvalue())
        assert rc == 0, "a persistence failure must not change the suite's verdict"
        assert payload["persistence"]["recorded"] is None
        assert "ace db unreachable" in payload["persistence"]["record_error"]


if __name__ == "__main__":
    unittest.main()
