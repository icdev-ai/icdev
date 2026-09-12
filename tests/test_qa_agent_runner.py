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
    SAMPLE_HEALTH_SLOW,
    SAMPLE_HOST_STALLED,
    SAMPLE_OK,
    SAMPLE_UNREACHABLE,
    STALL_KINDS,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_NO_TESTS,
    STATUS_PASSED,
    QARunResult,
    StallSample,
    StallSampler,
    TestFailure,
    _DEADLINE_SECONDS,
    _STALL_HEALTH_SLOW_SECONDS,
    _STALL_HOST_OVERSHOOT_SECONDS,
    _STALL_SAMPLE_SECONDS,
    _tally,
    annotate_failures_with_stalls,
    batch_specs,
    build_playwright_cmd,
    classify_sample,
    count_screenshot_attachments,
    derive_status,
    discover_coverage_gaps,
    failure_during_stall,
    file_failure_tasks,
    generate_spec_stub,
    parse_playwright_json,
    probe_health,
    probe_url,
    record_failure,
    record_run,
    resolve_e2e_base_url,
    resolve_spec_files,
    run_e2e_suite,
    summarize_stalls,
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
# count_screenshot_attachments — the count that could not move
# ---------------------------------------------------------------------------

class TestCountScreenshotAttachments(unittest.TestCase):
    """`screenshot_count` was `len(glob(<run dir>/*.png))` over a directory
    nothing writes to: `PLAYWRIGHT_SCREENSHOT_DIR` had ONE occurrence in the
    tree, the write. 16 of the 17 rows in `ace_qa_runs` read 0, including the
    sweeps that failed 39 and 31 tests."""

    def test_counts_image_attachment_on_a_flat_report(self):
        report = json.loads(_PLAYWRIGHT_JSON_FAIL)
        assert count_screenshot_attachments(report) == 1

    def test_counts_at_any_depth(self):
        """Every spec under tests/e2e/ sits inside a `test.describe`, so a
        top-level walk finds zero specs in every real report."""
        report = json.loads(_PLAYWRIGHT_JSON_NESTED_FAIL)
        assert count_screenshot_attachments(report) == 1

    def test_counts_passed_results_too(self):
        """playwright.config.ts sets `screenshot: 'on'`, which captures one per
        test. Restricting the walk to failures would under-report by design."""
        report = {
            "suites": [{
                "title": "file.spec.ts",
                "suites": [{
                    "title": "describe",
                    "specs": [{
                        "title": "green test",
                        "tests": [{"results": [{
                            "status": "passed",
                            "attachments": [
                                {"contentType": "image/png", "path": "/x/a.png"}
                            ],
                        }]}],
                    }],
                }],
            }]
        }
        assert count_screenshot_attachments(report) == 1

    def test_counts_every_retry_attempt(self):
        """Each attempt captured its own file; only the LAST result decides the
        verdict, which is a different question from what was captured."""
        report = {
            "suites": [{
                "title": "describe",
                "specs": [{
                    "title": "flaky",
                    "tests": [{"results": [
                        {"status": "failed",
                         "attachments": [{"contentType": "image/png", "path": "/x/1.png"}]},
                        {"status": "passed",
                         "attachments": [{"contentType": "image/png", "path": "/x/2.png"}]},
                    ]}],
                }],
            }]
        }
        assert count_screenshot_attachments(report) == 2

    def test_non_image_and_pathless_attachments_are_not_counted(self):
        """A trace/video is not a screenshot, and an attachment with no `path`
        names no file."""
        report = {
            "suites": [{
                "title": "describe",
                "specs": [{
                    "title": "t",
                    "tests": [{"results": [{
                        "status": "failed",
                        "attachments": [
                            {"contentType": "video/webm", "path": "/x/v.webm"},
                            {"contentType": "application/zip", "path": "/x/t.zip"},
                            {"contentType": "image/png"},
                            {"contentType": "image/png", "path": "/x/ok.png"},
                        ],
                    }]}],
                }],
            }]
        }
        assert count_screenshot_attachments(report) == 1

    def test_report_with_no_screenshots_counts_zero(self):
        assert count_screenshot_attachments(json.loads(_PLAYWRIGHT_JSON_PASS)) == 0

    def test_empty_report_counts_zero(self):
        assert count_screenshot_attachments({}) == 0

    def test_no_dead_screenshot_dir_env_var_remains(self):
        """The variable was EXPORTED and read by nothing. Re-exporting it would
        recreate the appearance of a screenshot pipeline that does not exist.

        Asserted over the AST as a bare string CONSTANT, not over the source
        text: the prose above explains the defect and names the variable, and a
        substring check cannot tell an explanation from a re-export.
        """
        import ast
        import importlib
        module = importlib.import_module("icdev.tools.testing.qa_agent_runner")
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        literals = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value == "PLAYWRIGHT_SCREENSHOT_DIR"
        ]
        assert not literals, "PLAYWRIGHT_SCREENSHOT_DIR is exported again and nothing reads it"


class TestRunE2ESuiteCountsScreenshots(unittest.TestCase):
    def test_screenshot_count_accumulates_across_batches(self):
        """The count is a sum over the reports the run COULD read. A batch that
        produced no report contributes nothing and is named in
        `spec_files_no_report` instead."""
        result = QARunResult()
        for raw in (_PLAYWRIGHT_JSON_FAIL, _PLAYWRIGHT_JSON_NESTED_FAIL):
            result.screenshot_count += count_screenshot_attachments(json.loads(raw))
        assert result.screenshot_count == 2


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


class TestRecordRunTimestamps(unittest.TestCase):
    """The row must say WHEN the run happened, measured by the runner.

    `record_run` named neither `started_at` nor `completed_at`, so the column
    default filled `started_at` with the moment of the INSERT -- which runs
    AFTER the sweep -- and `completed_at` stayed NULL. Measured 2026-09-10 on
    run qa-1789072164: batches began 20:29:24Z, the row reads 20:48:47Z, and
    all 21 rows in the table had a NULL `completed_at`. A 19-minute sweep was
    recorded as starting at its own finish, with no duration recoverable.
    """

    _DDL = (
        "CREATE TABLE ace_qa_runs (id TEXT PRIMARY KEY, trigger TEXT NOT NULL DEFAULT '', "
        "trigger_ref TEXT NOT NULL DEFAULT '', canvas_filter TEXT NOT NULL DEFAULT '', "
        "started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT, "
        "status TEXT NOT NULL DEFAULT 'running', total_tests INTEGER NOT NULL DEFAULT 0, "
        "passed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, "
        "screenshot_count INTEGER NOT NULL DEFAULT 0, report_path TEXT NOT NULL DEFAULT '')"
    )

    def _record_and_read(self, result: QARunResult) -> dict:
        import tempfile
        from tests._sql_compat import connect

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ace.db"
            setup = connect(db_path)
            setup.execute(self._DDL)
            setup.commit()
            setup.close()
            with patch(
                "icdev.tools.db.storage.get_canvas_connection",
                side_effect=lambda *_a, **_kw: connect(db_path),
            ):
                record_run(result)
            reader = connect(db_path)
            try:
                row = reader.execute(
                    "SELECT started_at, completed_at FROM ace_qa_runs WHERE id = %s",
                    (result.run_id,),
                ).fetchone()
            finally:
                reader.close()
        assert row is not None, "record_run wrote no row"
        return dict(row)

    def test_the_runners_own_timestamps_are_persisted(self):
        result = QARunResult(
            run_id="qa-ts-1", status="passed",
            started_at="2026-09-10 20:29:24.000000+00",
            completed_at="2026-09-10 20:48:46.000000+00",
        )
        row = self._record_and_read(result)
        assert row["started_at"] == "2026-09-10 20:29:24.000000+00"
        assert row["completed_at"] == "2026-09-10 20:48:46.000000+00"

    def test_an_unstamped_result_keeps_the_default_and_a_null_finish(self):
        """A caller that builds a result by hand has no measured times. The
        start falls back to the column default (it is NOT NULL) and the finish
        stays NULL -- never an empty string, never a guessed time."""
        row = self._record_and_read(QARunResult(run_id="qa-ts-2", status="passed"))
        assert row["started_at"]
        assert row["completed_at"] is None


class TestRunE2ESuiteTimestamps(unittest.TestCase):
    @staticmethod
    def _parse(ts: str):
        from datetime import datetime

        return datetime.strptime(ts[:-3] + "+0000", "%Y-%m-%d %H:%M:%S.%f%z")

    def test_run_is_stamped_before_the_first_batch_and_after_the_last(self):
        from datetime import datetime, timezone

        specs = ["tests/e2e/s0.spec.ts"]
        report = json.dumps({"stats": {"expected": 1, "unexpected": 0, "skipped": 0}, "suites": []})
        ok = MagicMock(returncode=0, stdout=report, stderr="")
        seen = {}

        def _run(*_a, **_kw):
            seen["batch"] = datetime.now(timezone.utc)
            return ok

        before = datetime.now(timezone.utc)
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", side_effect=_run),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=1)
        after = datetime.now(timezone.utc)

        started, completed = self._parse(result.started_at), self._parse(result.completed_at)
        assert before <= started <= seen["batch"] <= completed <= after

    def test_timestamps_use_the_text_form_the_column_default_writes(self):
        """The columns are TEXT and every existing row carries PostgreSQL's
        CURRENT_TIMESTAMP rendering. An ISO 'T' separator would sort every new
        row after every old row of the same day under the started_at index."""
        import re

        result = run_e2e_suite(canvas_filter="no-such-canvas-zzz")
        pattern = r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}\+00$"
        assert re.match(pattern, result.started_at), result.started_at
        assert re.match(pattern, result.completed_at), result.completed_at


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


class TestPersistenceSpeaksOneDialect(unittest.TestCase):
    """Runtime SQL here is authored for PostgreSQL, once.

    Each seam used to follow its `%s` statement with a SQLite-dialect `?`
    retry. On SQLite the retry never ran -- storage's connection translates
    `%s` to `?` -- and on PostgreSQL it could only raise, so all it did was
    replace the real error with its own in the log (record_run,
    record_failure) or log a spurious failure for an ordinary missing run
    (get_run_status).
    """

    _LOGGER = "icdev.tools.testing.qa_agent_runner"

    @staticmethod
    def _sql_of(mock_conn) -> list:
        return [c.args[0] for c in mock_conn.execute.call_args_list]

    def test_record_run_issues_one_statement_and_logs_its_real_error(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("primary failure")
        with (
            patch("icdev.tools.db.storage.get_canvas_connection", return_value=mock_conn),
            self.assertLogs(self._LOGGER, level="ERROR") as logs,
        ):
            record_run(QARunResult(run_id="qa-1"))
        sql = self._sql_of(mock_conn)
        assert len(sql) == 1 and "?" not in sql[0], sql
        assert any("primary failure" in line for line in logs.output)

    def test_record_failure_issues_one_statement_and_logs_its_real_error(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("primary failure")
        with (
            patch("icdev.tools.db.storage.get_canvas_connection", return_value=mock_conn),
            self.assertLogs(self._LOGGER, level="ERROR") as logs,
        ):
            record_failure(TestFailure(test_name="X"), "run-x")
        sql = self._sql_of(mock_conn)
        assert len(sql) == 1 and "?" not in sql[0], sql
        assert any("primary failure" in line for line in logs.output)

    def test_get_run_status_asks_once_for_a_missing_run(self):
        from icdev.tools.testing.qa_agent_runner import get_run_status

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch("icdev.tools.db.storage.get_canvas_connection", return_value=mock_conn):
            assert get_run_status("qa-missing") is None
        sql = self._sql_of(mock_conn)
        assert len(sql) == 1 and "?" not in sql[0], sql


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


# ---------------------------------------------------------------------------
# The persisted run report — the artifact `report_path` points a human at
# ---------------------------------------------------------------------------

class TestPersistedRunReportCarriesTheVerdict(unittest.TestCase):
    """`write_run_report` ran BEFORE `derive_status` and before `report_path`
    was assigned, so the file every reader is sent to could never carry either.

    MEASURED on this board: `.tmp/ace/qa/qa-1788898202-results.json` records
    840 tests, 831 passed, 1 failed -- and `status: "running"` with
    `report_path: ""`, twenty minutes after that sweep finished. The DB row for
    the same run says `failed`. A report that reads `running` for every sweep
    ever taken cannot distinguish a run still going from one that finished red.
    """

    def _module(self):
        import importlib
        return importlib.import_module("icdev.tools.testing.qa_agent_runner")

    def test_report_is_written_with_the_final_status(self):
        mod = self._module()
        specs = ["tests/e2e/s0.spec.ts"]
        seen = {}

        def _capture(res):
            seen["status"] = res.status
            return Path("unused-report.json")

        with (
            patch.object(mod, "resolve_spec_files", return_value=specs),
            patch.object(
                mod.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(cmd="npx", timeout=1),
            ),
            patch.object(mod, "write_run_report", _capture),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=1)

        assert result.status == STATUS_INCOMPLETE
        assert seen["status"] == STATUS_INCOMPLETE, (
            f"the report was written while status was {seen['status']!r} -- "
            "a persisted verdict of 'running' is never the run's verdict"
        )

    def test_written_report_names_its_own_path(self):
        import tempfile
        mod = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(mod, "PROJECT_ROOT", Path(tmp)):
                result = QARunResult(run_id="qa-selfref")
                result.status = STATUS_PASSED
                out = mod.write_run_report(result)
                data = json.loads(Path(out).read_text(encoding="utf-8"))

        assert data["status"] == STATUS_PASSED
        assert data["report_path"] == str(out), (
            "the persisted report does not name itself, so a reader holding "
            "only the file cannot say which run artifact they have"
        )


# ---------------------------------------------------------------------------
# get_run_status — a row that is ALREADY a mapping
# ---------------------------------------------------------------------------

class TestGetRunStatusReadsAMappingRow(unittest.TestCase):
    """psycopg2's RealDictRow IS a mapping and has no `.cursor`, so the
    description walk found no keys and every PostgreSQL lookup fell into the
    degraded `{"id": ..., "raw": str(row)}` branch.

    The CLI then printed `status=None total=None` for a run whose row says
    `failed / 840 / 831 / 1` -- the fields were in hand and stringified away.
    """

    def _module(self):
        import importlib
        return importlib.import_module("icdev.tools.testing.qa_agent_runner")

    def test_mapping_row_is_returned_as_its_fields(self):
        mod = self._module()
        row = {
            "id": "qa-1788898202",
            "status": "failed",
            "total_tests": 840,
            "passed": 831,
            "failed": 1,
        }
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row

        with patch.object(mod, "get_canvas_connection", conn, create=True):
            with patch("tools.db.storage.get_canvas_connection", return_value=conn):
                out = mod.get_run_status("qa-1788898202")

        assert out is not None
        assert "raw" not in out, f"row stringified instead of read: {out}"
        assert out["status"] == "failed"
        assert out["total_tests"] == 840
        assert out["passed"] == 831


# ---------------------------------------------------------------------------
# Host-stall sampling (qa-fail-5cacee65f1d03c8c)
# ---------------------------------------------------------------------------
#
# Run qa-1789161604 (2026-09-11): 4 of 849 tests timed out, every one inside a
# window where the isolated server's request log went silent for 20-59 s and
# a sampler beside the run saw its own 5 s loop stretch to 12-14 s while
# /api/health still answered in 0.10 s. The runner recorded that sweep
# IDENTICALLY to four product defects. These tests pin the sampler that now
# runs beside every batch and the per-failure verdict it yields.


def _stall_report(start_iso: str, duration_ms: int, title: str = "slow page") -> str:
    """A nested Playwright report with ONE failed result at the given window."""
    return json.dumps({
        "stats": {"expected": 0, "unexpected": 1, "skipped": 0},
        "suites": [{
            "title": "x.spec.ts", "file": "x.spec.ts", "specs": [],
            "suites": [{
                "title": "Suite", "file": "x.spec.ts",
                "specs": [{
                    "title": title,
                    "tests": [{"results": [{
                        "status": "failed",
                        "startTime": start_iso,
                        "duration": duration_ms,
                        "error": {"message": "TimeoutError: page.goto: Timeout 30000ms exceeded."},
                        "attachments": [],
                    }]}],
                }],
            }],
        }],
    })


def _sample(kind: str, window_start: float, at: float, **kw) -> StallSample:
    base = dict(
        at="", at_epoch=at, window_start_epoch=window_start,
        health_seconds=kw.pop("health_seconds", 0.1),
        sleep_overshoot_seconds=kw.pop("overshoot", 0.0), kind=kind,
    )
    base.update(kw)
    return StallSample(**base)


class TestResolveE2EBaseUrl(unittest.TestCase):
    """The Python mirror of tests/e2e/fixtures/base_url.ts::resolveBaseUrl.
    Same three variables, same precedence -- the sampler must probe the origin
    the suite is actually navigating to."""

    def test_e2e_base_url_wins(self):
        env = {
            "ICDEV_E2E_BASE_URL": "http://127.0.0.1:5095",
            "ICDEV_DASHBOARD_URL": "http://host.docker.internal:5050",
            "ICDEV_DASHBOARD_PORT": "5090",
        }
        assert resolve_e2e_base_url(env) == "http://127.0.0.1:5095"

    def test_dashboard_url_is_second(self):
        env = {"ICDEV_DASHBOARD_URL": "http://localhost:5060", "ICDEV_DASHBOARD_PORT": "5090"}
        assert resolve_e2e_base_url(env) == "http://localhost:5060"

    def test_port_derives_the_default(self):
        assert resolve_e2e_base_url({"ICDEV_DASHBOARD_PORT": "5090"}) == "http://localhost:5090"
        assert resolve_e2e_base_url({}) == "http://localhost:5050"

    def test_trailing_slash_is_dropped(self):
        assert resolve_e2e_base_url({"ICDEV_E2E_BASE_URL": "http://x:1/"}) == "http://x:1"


class TestProbeUrl(unittest.TestCase):
    """Measured 2026-09-11 on this host: `localhost` resolves to ::1 first,
    the dashboard binds IPv4 only, and the refusal takes 2.05 s -- so probing
    `http://localhost:5050` costs 2.08 s a sample against 0.06 s direct, which
    is the slow-health threshold to the decimal. The probe connects to
    127.0.0.1 for that one hostname and says so in the census."""

    def test_localhost_is_probed_over_ipv4(self):
        assert probe_url("http://localhost:5050") == "http://127.0.0.1:5050/api/health"
        assert probe_url("http://localhost") == "http://127.0.0.1/api/health"

    def test_other_hosts_are_probed_as_given(self):
        assert probe_url("http://127.0.0.1:5095") == "http://127.0.0.1:5095/api/health"
        assert probe_url("http://dashboard.internal:5050/") == "http://dashboard.internal:5050/api/health"

    def test_census_records_what_was_probed(self):
        sampler = StallSampler("http://localhost:5050", interval_seconds=5.0, probe=lambda _b: 0.1)
        assert sampler.census()["probe_url"] == "http://127.0.0.1:5050/api/health"
        sampler.samples.append(_sample(SAMPLE_OK, 0, 5))
        assert sampler.census()["probe_url"] == "http://127.0.0.1:5050/api/health"
        assert sampler.census()["base_url"] == "http://localhost:5050"

    def test_ipv4_refusal_falls_back_to_the_base_as_given(self):
        """An IPv6-only bind must still answer; a down server refuses both."""
        asked: list = []

        def _get(url, timeout):
            asked.append(url)
            return 0.05 if url.startswith("http://localhost") else None

        with patch("icdev.tools.testing.qa_agent_runner._time_get", side_effect=_get):
            assert probe_health("http://localhost:5050") == 0.05
        assert asked == ["http://127.0.0.1:5050/api/health", "http://localhost:5050/api/health"]

        asked.clear()
        with patch("icdev.tools.testing.qa_agent_runner._time_get", return_value=None):
            assert probe_health("http://127.0.0.1:5095") is None
        # A host probed as given is asked ONCE: there is no second spelling.
        with patch("icdev.tools.testing.qa_agent_runner._time_get", side_effect=_get):
            assert probe_health("http://127.0.0.1:5095") is None
        assert asked == ["http://127.0.0.1:5095/api/health"]


class TestClassifySample(unittest.TestCase):
    def test_unreachable_when_no_health_answer(self):
        assert classify_sample(None, 0.0) == SAMPLE_UNREACHABLE

    def test_host_stalled_on_sleep_overshoot(self):
        """The measured shape: health 0.10 s, loop period 12-14 s against 5 s."""
        assert classify_sample(0.10, 7.0) == SAMPLE_HOST_STALLED

    def test_health_slow_when_the_app_is_slow(self):
        assert classify_sample(_STALL_HEALTH_SLOW_SECONDS, 0.0) == SAMPLE_HEALTH_SLOW

    def test_host_stall_outranks_slow_health(self):
        """A starved host also makes the probe slow; the host verdict is the
        one that says where to look."""
        assert classify_sample(9.0, 9.0) == SAMPLE_HOST_STALLED

    def test_ordinary_sample_is_ok(self):
        assert classify_sample(0.1, 0.02) == SAMPLE_OK

    def test_unreachable_is_never_a_stall_kind(self):
        """Playwright starts and stops its own webServer per batch, so a probe
        before the server is up is EXPECTED, not a stall."""
        assert SAMPLE_UNREACHABLE not in STALL_KINDS
        assert {SAMPLE_HOST_STALLED, SAMPLE_HEALTH_SLOW} == set(STALL_KINDS)

    def test_thresholds_sit_between_normal_jitter_and_the_measured_stall(self):
        """Measured 2026-09-11: the sampler's loop stretched to 12 s and 14 s
        against a 5 s interval (overshoot 7-9 s); normal overshoot on this host
        is milliseconds. The threshold must catch the incident and ignore the
        jitter, and the default interval is the one the incident was measured at."""
        assert 0.1 < _STALL_HOST_OVERSHOOT_SECONDS < 7.0
        assert _STALL_SAMPLE_SECONDS == 5.0


class TestFailureDuringStall(unittest.TestCase):
    START = "2026-09-11T21:28:14.504Z"
    START_EPOCH = 1789162094.504
    DURATION_MS = 39732

    def test_none_when_nothing_was_sampled(self):
        """No samples is UNMEASURED, never 'no stall'."""
        assert failure_during_stall(self.START, self.DURATION_MS, []) is None

    def test_none_when_the_failure_has_no_window(self):
        s = _sample(SAMPLE_HOST_STALLED, self.START_EPOCH, self.START_EPOCH + 12, overshoot=7.0)
        assert failure_during_stall("", None, [s]) is None
        assert failure_during_stall("not-a-date", 10, [s]) is None

    def test_true_when_a_stall_sample_overlaps_the_window(self):
        s = _sample(SAMPLE_HOST_STALLED, self.START_EPOCH + 10, self.START_EPOCH + 22, overshoot=7.0)
        assert failure_during_stall(self.START, self.DURATION_MS, [s]) is True

    def test_false_when_samples_exist_but_none_overlap(self):
        before = _sample(SAMPLE_HOST_STALLED, self.START_EPOCH - 60, self.START_EPOCH - 48, overshoot=7.0)
        during_ok = _sample(SAMPLE_OK, self.START_EPOCH + 5, self.START_EPOCH + 10)
        after = _sample(SAMPLE_HEALTH_SLOW, self.START_EPOCH + 100, self.START_EPOCH + 105, health_seconds=4.0)
        assert failure_during_stall(self.START, self.DURATION_MS, [before, during_ok, after]) is False

    def test_unreachable_samples_inside_the_window_are_not_a_stall(self):
        s = _sample(SAMPLE_UNREACHABLE, self.START_EPOCH + 1, self.START_EPOCH + 6, health_seconds=None)
        assert failure_during_stall(self.START, self.DURATION_MS, [s]) is False

    def test_a_stall_that_ends_exactly_at_the_window_start_counts(self):
        """The sample's covered interval is [sleep started, probe finished]; the
        stall may have happened anywhere inside it, so touching the window is
        overlap."""
        s = _sample(SAMPLE_HOST_STALLED, self.START_EPOCH - 12, self.START_EPOCH, overshoot=7.0)
        assert failure_during_stall(self.START, self.DURATION_MS, [s]) is True

    def test_annotate_writes_the_verdict_and_the_detail(self):
        f = TestFailure(test_name="t", started_at=self.START, duration_ms=self.DURATION_MS)
        s = _sample(SAMPLE_HOST_STALLED, self.START_EPOCH + 10, self.START_EPOCH + 22, overshoot=7.0)
        annotate_failures_with_stalls([f], [s])
        assert f.during_stall is True
        assert "host_stalled" in f.stall_detail and "7.0" in f.stall_detail

    def test_annotate_leaves_unmeasured_failures_none(self):
        f = TestFailure(test_name="t", started_at=self.START, duration_ms=self.DURATION_MS)
        annotate_failures_with_stalls([f], [])
        assert f.during_stall is None
        assert "unmeasured" in f.stall_detail


class TestParseTimingFields(unittest.TestCase):
    def test_start_time_and_duration_are_carried_onto_the_failure(self):
        failures = parse_playwright_json(_stall_report("2026-09-11T21:34:30.000Z", 30500))
        assert len(failures) == 1
        assert failures[0].started_at == "2026-09-11T21:34:30.000Z"
        assert failures[0].duration_ms == 30500
        assert failures[0].during_stall is None

    def test_missing_timing_is_none_never_zero(self):
        failures = parse_playwright_json(_PLAYWRIGHT_JSON_NESTED_FAIL)
        assert failures[0].started_at == ""
        assert failures[0].duration_ms is None


class TestSamplerDoesNotShadowThreadInternals(unittest.TestCase):
    """A Thread subclass that shadows one of Thread's own private METHODS with a
    non-callable breaks `join()`, and the break is INVISIBLE locally.

    MEASURED: `self._stop = threading.Event()` passed every local run on Python
    3.14 -- which no longer has `Thread._stop` -- and failed ALL TWELVE sampler
    tests on CI with `TypeError: 'Event' object is not callable`, because
    .github/workflows/icdev-ci.yml pins python-version 3.11, where `_stop` IS a
    method and `join()` -> `_wait_for_tstate_lock()` calls it.

    So this asserts the GENERAL rule against whichever interpreter is running,
    rather than banning one name: nothing the sampler assigns may turn one of
    Thread's callables into a non-callable. On 3.14 it cannot see `_stop`; on
    3.11 it catches it before CI does.
    """

    def test_no_thread_callable_is_shadowed_by_a_non_callable(self):
        import threading as _t

        sampler = StallSampler(base_url="http://127.0.0.1:1", interval_seconds=0.01)
        shadowed = []
        for name in dir(_t.Thread):
            inherited = getattr(_t.Thread, name, None)
            if not callable(inherited):
                continue
            mine = getattr(sampler, name, None)
            if mine is not None and not callable(mine):
                shadowed.append((name, type(mine).__name__))
        self.assertEqual(
            shadowed, [],
            f"StallSampler shadows Thread callables with non-callables: {shadowed}. "
            "On an interpreter where Thread defines that name, join() raises "
            "TypeError. Rename the attribute (see `_halt`).",
        )

    def test_the_sampler_can_be_started_and_joined(self):
        """The behaviour the shadowing broke, end to end: start, stop, join."""
        sampler = StallSampler(
            base_url="http://127.0.0.1:1", interval_seconds=0.01,
            probe=lambda base: 0.01,
        )
        sampler.start()
        sampler.stop(timeout=5)
        self.assertFalse(sampler.is_alive())


class TestStallSampler(unittest.TestCase):
    def test_takes_samples_until_stopped_and_never_raises(self):
        probes = iter([0.1, None, 0.1])
        sampler = StallSampler(
            "http://127.0.0.1:1", interval_seconds=0.01,
            probe=lambda _base: next(probes, 0.1),
        )
        sampler.start()
        import time as _t
        deadline = _t.time() + 5
        while len(sampler.samples) < 3 and _t.time() < deadline:
            _t.sleep(0.01)
        sampler.stop()
        assert len(sampler.samples) >= 3
        kinds = [s.kind for s in sampler.samples[:3]]
        assert kinds[1] == SAMPLE_UNREACHABLE
        assert all(s.window_start_epoch <= s.at_epoch for s in sampler.samples)
        assert sampler.error is None

    def test_a_probe_that_raises_is_recorded_not_propagated(self):
        def _boom(_base):
            raise RuntimeError("probe broke")
        sampler = StallSampler("http://127.0.0.1:1", interval_seconds=0.01, probe=_boom)
        sampler.start()
        import time as _t
        deadline = _t.time() + 5
        while sampler.error is None and _t.time() < deadline:
            _t.sleep(0.01)
        sampler.stop()
        assert "probe broke" in (sampler.error or "")
        census = sampler.census()
        assert census["measured"] is False
        assert census["reason"].startswith("sampler_error")

    def test_census_counts_each_kind_separately(self):
        sampler = StallSampler("http://x", interval_seconds=5.0, probe=lambda _b: 0.1)
        sampler.samples.extend([
            _sample(SAMPLE_OK, 0, 5),
            _sample(SAMPLE_HOST_STALLED, 5, 17, overshoot=7.0),
            _sample(SAMPLE_HOST_STALLED, 17, 31, overshoot=9.0),
            _sample(SAMPLE_HEALTH_SLOW, 31, 40, health_seconds=4.0),
            _sample(SAMPLE_UNREACHABLE, 40, 45, health_seconds=None),
        ])
        c = sampler.census()
        assert c["measured"] is True
        assert c["samples"] == 5
        assert c["host_stalls"] == 2
        assert c["health_slow"] == 1
        assert c["unreachable"] == 1
        assert c["reachable_samples"] == 4
        assert c["health_max_seconds"] == 4.0
        assert c["max_sleep_overshoot_seconds"] == 9.0
        assert c["stalls"][0]["kind"] == SAMPLE_HOST_STALLED
        assert c["interval_seconds"] == 5.0

    def test_census_with_no_samples_is_unmeasured(self):
        sampler = StallSampler("http://x", interval_seconds=5.0, probe=lambda _b: 0.1)
        c = sampler.census()
        assert c["measured"] is False
        assert c["host_stalls"] is None and c["health_slow"] is None
        assert c["health_max_seconds"] is None


class TestRunE2ESuiteStallContext(unittest.TestCase):
    """The sweep-level contract: every batch record carries its census, every
    failure carries its verdict, and the verdict never moves the status."""

    START = "2026-09-11T21:28:14.504Z"
    START_EPOCH = 1789162094.504

    def _fake_sampler_factory(self, samples):
        module = sys.modules["icdev.tools.testing.qa_agent_runner"]

        class _Fake(module.StallSampler):
            def start(self_inner):  # noqa: N805
                self_inner.samples.extend(samples)

            def stop(self_inner, timeout=None):  # noqa: N805
                return None

        return _Fake

    def test_failure_inside_a_measured_stall_is_named_and_status_stays_failed(self):
        specs = ["tests/e2e/s0.spec.ts"]
        proc = MagicMock(returncode=1, stdout=_stall_report(self.START, 39732), stderr="")
        stall = _sample(SAMPLE_HOST_STALLED, self.START_EPOCH + 10, self.START_EPOCH + 22, overshoot=7.0)
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", return_value=proc),
            patch("icdev.tools.testing.qa_agent_runner.StallSampler", self._fake_sampler_factory([stall])),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=1)
        assert result.status == STATUS_FAILED, "a stall explains a failure; it never excuses it"
        assert result.failed == 1
        assert result.failures[0].during_stall is True
        assert result.failures_during_stall == 1
        assert result.failures_stall_unmeasured == 0
        census = result.batches[0]["stall_census"]
        assert census["measured"] is True and census["host_stalls"] == 1
        assert result.stall_summary["batches_sampled"] == 1
        assert result.stall_summary["host_stalls"] == 1
        assert result.stall_summary["base_url"]

    def test_failure_with_no_stall_in_window_is_false(self):
        specs = ["tests/e2e/s0.spec.ts"]
        proc = MagicMock(returncode=1, stdout=_stall_report(self.START, 39732), stderr="")
        ok = _sample(SAMPLE_OK, self.START_EPOCH + 5, self.START_EPOCH + 10)
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", return_value=proc),
            patch("icdev.tools.testing.qa_agent_runner.StallSampler", self._fake_sampler_factory([ok])),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=1)
        assert result.failures[0].during_stall is False
        assert result.failures_during_stall == 0
        assert result.stall_summary["host_stalls"] == 0

    def test_sampler_disabled_by_env_leaves_every_verdict_none(self):
        """Off is REPORTED: `failures_during_stall` is None, never 0, and the
        batch census says why."""
        specs = ["tests/e2e/s0.spec.ts"]
        proc = MagicMock(returncode=1, stdout=_stall_report(self.START, 39732), stderr="")
        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", return_value=proc),
            patch.dict(os.environ, {"ICDEV_QA_STALL_SAMPLER": "0"}),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=1)
        assert result.status == STATUS_FAILED
        assert result.failures[0].during_stall is None
        assert result.failures_during_stall is None
        assert result.failures_stall_unmeasured == 1
        assert result.batches[0]["stall_census"] == {"measured": False, "reason": "disabled_by_env"}
        assert result.stall_summary["measured"] is False
        assert result.stall_summary["batches_sampled"] == 0

    def test_sampler_is_started_and_stopped_around_every_batch(self):
        specs = ["tests/e2e/s0.spec.ts", "tests/e2e/s1.spec.ts"]
        report = json.dumps({"stats": {"expected": 1, "unexpected": 0, "skipped": 0}, "suites": []})
        proc = MagicMock(returncode=0, stdout=report, stderr="")
        events: list = []
        module = sys.modules["icdev.tools.testing.qa_agent_runner"]

        class _Spy(module.StallSampler):
            def start(self_inner):  # noqa: N805
                events.append("start")

            def stop(self_inner, timeout=None):  # noqa: N805
                events.append("stop")

        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch("icdev.tools.testing.qa_agent_runner.subprocess.run", return_value=proc),
            patch("icdev.tools.testing.qa_agent_runner.StallSampler", _Spy),
        ):
            result = run_e2e_suite(deadline_seconds=600, batch_size=1)
        assert events == ["start", "stop", "start", "stop"]
        assert result.status == STATUS_PASSED
        # A green batch still carries its census: "no stall" is a measurement
        # only when something measured it.
        assert result.batches[0]["stall_census"]["measured"] is False
        assert result.failures_during_stall is None

    def test_sampler_is_stopped_when_the_batch_is_deadline_killed(self):
        specs = ["tests/e2e/s0.spec.ts"]
        events: list = []
        module = sys.modules["icdev.tools.testing.qa_agent_runner"]

        class _Spy(module.StallSampler):
            def start(self_inner):  # noqa: N805
                events.append("start")

            def stop(self_inner, timeout=None):  # noqa: N805
                events.append("stop")

        with (
            patch("icdev.tools.testing.qa_agent_runner.resolve_spec_files", return_value=specs),
            patch(
                "icdev.tools.testing.qa_agent_runner.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="npx", timeout=1),
            ),
            patch("icdev.tools.testing.qa_agent_runner.StallSampler", _Spy),
        ):
            run_e2e_suite(deadline_seconds=600, batch_size=1)
        assert events == ["start", "stop"]


class TestSummarizeStalls(unittest.TestCase):
    def test_rolls_batches_up_without_folding_unmeasured(self):
        result = QARunResult(batches=[
            {"batch": 0, "stall_census": {"measured": True, "samples": 10, "host_stalls": 2,
                                          "health_slow": 0, "unreachable": 1, "reachable_samples": 9}},
            {"batch": 1, "stall_census": {"measured": False, "reason": "no_samples"}},
            {"batch": 2, "status": "deadline_skipped"},
        ])
        s = summarize_stalls(result, base_url="http://x", interval_seconds=5.0)
        assert s["measured"] is True
        assert s["batches_sampled"] == 1
        assert s["batches_unsampled"] == 2
        assert s["host_stalls"] == 2 and s["health_slow"] == 0 and s["unreachable"] == 1
        assert s["samples"] == 10

    def test_nothing_sampled_is_unmeasured_with_none_counts(self):
        result = QARunResult(batches=[{"batch": 0, "stall_census": {"measured": False, "reason": "x"}}])
        s = summarize_stalls(result, base_url="http://x", interval_seconds=5.0)
        assert s["measured"] is False
        assert s["host_stalls"] is None and s["health_slow"] is None and s["samples"] is None


class TestCLIRecordsFailures(unittest.TestCase):
    """`record_failure` had a definition and NO call site in the runner, so
    `ace_qa_failures` held 0 rows for a 4-failure sweep and 0 for a clean one
    (the 2026-09-11 sweep report, s8). `--record` now writes one row per
    failure, linked to the card when one was filed."""

    def _result(self):
        return QARunResult(
            run_id="qa-1", status=STATUS_FAILED, total=2, passed=0, failed=2,
            failures=[
                TestFailure(test_name="a > one", spec_file="a.spec.ts", error_message="x"),
                TestFailure(test_name="b > two", spec_file="b.spec.ts", error_message="y"),
            ],
        )

    def test_record_writes_one_failure_row_per_failure(self):
        from icdev.tools.testing.qa_agent_runner import main

        recorded: list = []
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["qa_agent_runner.py", "--run", "--json"]),
            patch("icdev.tools.testing.qa_agent_runner.run_e2e_suite", return_value=self._result()),
            patch("icdev.tools.testing.qa_agent_runner.record_run", return_value="qa-1"),
            patch(
                "icdev.tools.testing.qa_agent_runner.record_failure",
                side_effect=lambda f, run_id, kanban_task_id="": recorded.append((f.test_name, run_id, kanban_task_id)) or "fid",
            ),
            contextlib.redirect_stdout(out),
        ):
            rc = main()
        assert rc == 1
        assert recorded == [("a > one", "qa-1", ""), ("b > two", "qa-1", "")]
        payload = json.loads(out.getvalue())
        assert payload["persistence"]["recorded_failures"] == 2
        assert payload["persistence"]["record_failures_error"] is None

    def test_filed_card_id_rides_on_its_failure_row(self):
        from icdev.tools.testing.qa_agent_runner import main
        import hashlib as _h

        recorded: list = []
        card_a = "qa-fail-" + _h.sha256(b"qa-1:a > one").hexdigest()[:16]
        with (
            patch.object(sys, "argv", ["qa_agent_runner.py", "--run", "--file-failures"]),
            patch("icdev.tools.testing.qa_agent_runner.run_e2e_suite", return_value=self._result()),
            patch("icdev.tools.testing.qa_agent_runner.record_run", return_value="qa-1"),
            patch("icdev.tools.testing.qa_agent_runner.file_failure_tasks", return_value=[card_a]),
            patch(
                "icdev.tools.testing.qa_agent_runner.record_failure",
                side_effect=lambda f, run_id, kanban_task_id="": recorded.append(kanban_task_id) or "fid",
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main()
        # Only the failure whose card was actually created carries the id; the
        # other was deduped away (or refused) and must not claim a card.
        assert recorded == [card_a, ""]

    def test_no_record_writes_no_failure_rows_and_says_so(self):
        from icdev.tools.testing.qa_agent_runner import main

        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["qa_agent_runner.py", "--run", "--no-record", "--json"]),
            patch("icdev.tools.testing.qa_agent_runner.run_e2e_suite", return_value=self._result()),
            patch("icdev.tools.testing.qa_agent_runner.record_failure") as rf,
            contextlib.redirect_stdout(out),
        ):
            main()
        rf.assert_not_called()
        assert json.loads(out.getvalue())["persistence"]["recorded_failures"] is None

    def test_a_failing_row_write_is_reported_not_raised(self):
        from icdev.tools.testing.qa_agent_runner import main

        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["qa_agent_runner.py", "--run", "--json"]),
            patch("icdev.tools.testing.qa_agent_runner.run_e2e_suite", return_value=self._result()),
            patch("icdev.tools.testing.qa_agent_runner.record_run", return_value="qa-1"),
            patch("icdev.tools.testing.qa_agent_runner.record_failure", side_effect=RuntimeError("db down")),
            contextlib.redirect_stdout(out),
        ):
            rc = main()
        assert rc == 1
        p = json.loads(out.getvalue())["persistence"]
        assert p["recorded_failures"] == 0
        assert "db down" in p["record_failures_error"]

    def test_text_output_names_the_stall_verdict_per_failure(self):
        from icdev.tools.testing.qa_agent_runner import main

        result = self._result()
        result.failures[0].during_stall = True
        result.failures[0].stall_detail = "host_stalled overshoot 7.0s at 21:28:26Z"
        result.failures[1].during_stall = None
        result.failures[1].stall_detail = "unmeasured: sampler took no sample during this batch"
        result.failures_during_stall = 1
        result.failures_stall_unmeasured = 1
        result.stall_summary = {
            "measured": True, "batches_sampled": 1, "batches_unsampled": 0,
            "samples": 40, "host_stalls": 3, "health_slow": 0, "unreachable": 0,
            "reachable_samples": 40, "interval_seconds": 5.0, "base_url": "http://127.0.0.1:5095",
        }
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["qa_agent_runner.py", "--run", "--no-record"]),
            patch("icdev.tools.testing.qa_agent_runner.run_e2e_suite", return_value=result),
            contextlib.redirect_stdout(out),
        ):
            main()
        text = out.getvalue()
        assert "DURING MEASURED STALL" in text
        assert "STALL UNMEASURED" in text
        assert "STALLS:" in text and "host stalls 3" in text
        assert "1 of 2 failures inside a measured stall" in text


if __name__ == "__main__":
    unittest.main()
