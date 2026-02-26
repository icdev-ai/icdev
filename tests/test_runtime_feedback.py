#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Runtime Feedback Collector (Phase 52 — D332, D334).

Covers: JUnit XML parsing, stdout parsing, test-to-source mapping,
DB storage, collect pipeline, health score computation.
"""

import json
import sqlite3
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.analysis.runtime_feedback import RuntimeFeedbackCollector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temp DB with code_quality_metrics and runtime_feedback tables."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS code_quality_metrics (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            file_path TEXT NOT NULL,
            function_name TEXT,
            class_name TEXT,
            language TEXT NOT NULL,
            cyclomatic_complexity INTEGER DEFAULT 0,
            cognitive_complexity INTEGER DEFAULT 0,
            loc INTEGER DEFAULT 0,
            loc_code INTEGER DEFAULT 0,
            loc_comment INTEGER DEFAULT 0,
            parameter_count INTEGER DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0,
            import_count INTEGER DEFAULT 0,
            class_count INTEGER DEFAULT 0,
            function_count INTEGER DEFAULT 0,
            smells_json TEXT DEFAULT '[]',
            smell_count INTEGER DEFAULT 0,
            maintainability_score REAL DEFAULT 0.0,
            content_hash TEXT,
            scan_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS runtime_feedback (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            source_file TEXT NOT NULL,
            source_function TEXT,
            test_file TEXT,
            test_function TEXT,
            test_passed INTEGER,
            test_duration_ms REAL,
            error_type TEXT,
            error_message TEXT,
            coverage_pct REAL,
            run_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def sample_junit_xml(tmp_path):
    """Create a sample JUnit XML file."""
    xml = tmp_path / "results.xml"
    xml.write_text(textwrap.dedent("""\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites>
          <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="3" time="1.234">
            <testcase classname="tests.test_foo" name="test_add" time="0.012" />
            <testcase classname="tests.test_foo" name="test_subtract" time="0.008">
              <failure type="AssertionError" message="assert 5 == 4">Traceback...</failure>
            </testcase>
            <testcase classname="tests.test_bar" name="test_multiply" time="0.003" />
          </testsuite>
        </testsuites>
    """))
    return xml


@pytest.fixture
def single_suite_xml(tmp_path):
    """Create a JUnit XML with <testsuite> as root (no <testsuites> wrapper)."""
    xml = tmp_path / "single.xml"
    xml.write_text(textwrap.dedent("""\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuite name="pytest" errors="1" failures="0" tests="2" time="0.5">
          <testcase classname="tests.test_utils" name="test_parse" time="0.100" />
          <testcase classname="tests.test_utils" name="test_format" time="0.050">
            <error type="RuntimeError" message="Connection refused" />
          </testcase>
        </testsuite>
    """))
    return xml


# ---------------------------------------------------------------------------
# TestJUnitXMLParsing
# ---------------------------------------------------------------------------

class TestJUnitXMLParsing:

    def test_parse_basic_xml(self, sample_junit_xml):
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_xml(sample_junit_xml)
        assert len(results) == 3
        passed = [r for r in results if r["test_passed"]]
        failed = [r for r in results if not r["test_passed"]]
        assert len(passed) == 2
        assert len(failed) == 1

    def test_parse_extracts_test_names(self, sample_junit_xml):
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_xml(sample_junit_xml)
        names = [r["test_function"] for r in results]
        assert "test_add" in names
        assert "test_subtract" in names
        assert "test_multiply" in names

    def test_parse_extracts_duration(self, sample_junit_xml):
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_xml(sample_junit_xml)
        add_result = [r for r in results if r["test_function"] == "test_add"][0]
        assert add_result["test_duration_ms"] == 12.0

    def test_parse_failure_details(self, sample_junit_xml):
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_xml(sample_junit_xml)
        failed = [r for r in results if not r["test_passed"]][0]
        assert failed["error_type"] == "AssertionError"
        assert "assert 5 == 4" in failed["error_message"]

    def test_parse_single_testsuite_root(self, single_suite_xml):
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_xml(single_suite_xml)
        assert len(results) == 2
        error_result = [r for r in results if not r["test_passed"]][0]
        assert error_result["error_type"] == "RuntimeError"

    def test_parse_nonexistent_xml_returns_empty(self, tmp_path):
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_xml(tmp_path / "nonexistent.xml")
        assert results == []

    def test_parse_invalid_xml_returns_empty(self, tmp_path):
        bad_xml = tmp_path / "bad.xml"
        bad_xml.write_text("this is not xml at all")
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_xml(bad_xml)
        assert results == []


# ---------------------------------------------------------------------------
# TestStdoutParsing
# ---------------------------------------------------------------------------

class TestStdoutParsing:

    def test_parse_passed_line(self):
        stdout = "PASSED tests/test_foo.py::TestClass::test_add\n"
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_stdout(stdout)
        assert len(results) == 1
        assert results[0]["test_passed"] is True
        assert results[0]["test_function"] == "test_add"

    def test_parse_failed_line(self):
        stdout = "FAILED tests/test_bar.py::test_subtract\n"
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_stdout(stdout)
        assert len(results) == 1
        assert results[0]["test_passed"] is False
        assert results[0]["error_type"] == "FAILED"

    def test_parse_multiple_lines(self):
        stdout = (
            "PASSED tests/test_foo.py::test_one\n"
            "FAILED tests/test_foo.py::test_two\n"
            "PASSED tests/test_bar.py::test_three\n"
            "ERROR tests/test_baz.py::TestClass::test_four\n"
        )
        collector = RuntimeFeedbackCollector()
        results = collector.parse_pytest_stdout(stdout)
        assert len(results) == 4
        passed = sum(1 for r in results if r["test_passed"])
        assert passed == 2


# ---------------------------------------------------------------------------
# TestTestToSourceMapping
# ---------------------------------------------------------------------------

class TestTestToSourceMapping:

    def test_strip_test_prefix_from_function(self):
        source_file, source_fn = RuntimeFeedbackCollector._map_test_to_source(
            "test_analyze_code", "tests/test_code_analyzer.py"
        )
        assert source_fn == "analyze_code"

    def test_strip_test_prefix_from_file(self):
        source_file, source_fn = RuntimeFeedbackCollector._map_test_to_source(
            "test_parse", "tests/test_utils.py"
        )
        assert source_file == "utils.py"

    def test_no_test_prefix_returns_none(self):
        source_file, source_fn = RuntimeFeedbackCollector._map_test_to_source(
            "helper_function", ""
        )
        assert source_fn is None
        assert source_file is None

    def test_maps_test_file_to_source_file(self):
        source_file, source_fn = RuntimeFeedbackCollector._map_test_to_source(
            "test_create_project", "tests/test_project_create.py"
        )
        assert source_file == "project_create.py"
        assert source_fn == "create_project"


# ---------------------------------------------------------------------------
# TestDBStorage
# ---------------------------------------------------------------------------

class TestDBStorage:

    def test_store_feedback_inserts_rows(self, tmp_db):
        collector = RuntimeFeedbackCollector(project_id="test", db_path=tmp_db)
        feedback = [
            {"test_file": "tests/test_foo.py", "test_function": "test_add",
             "test_passed": True, "test_duration_ms": 12.0,
             "source_file": "foo.py", "source_function": "add"},
            {"test_file": "tests/test_foo.py", "test_function": "test_sub",
             "test_passed": False, "test_duration_ms": 8.0,
             "error_type": "AssertionError", "error_message": "assert 5==4",
             "source_file": "foo.py", "source_function": "sub"},
        ]
        count = collector.store_feedback(feedback, run_id="run-001", db_path=tmp_db)
        assert count == 2

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT COUNT(*) FROM runtime_feedback").fetchone()[0]
        conn.close()
        assert rows == 2

    def test_store_feedback_uses_run_id(self, tmp_db):
        collector = RuntimeFeedbackCollector(project_id="test", db_path=tmp_db)
        feedback = [
            {"test_file": "t.py", "test_function": "test_a", "test_passed": True,
             "test_duration_ms": 1.0, "source_file": "", "source_function": "a"},
        ]
        collector.store_feedback(feedback, run_id="run-abc", db_path=tmp_db)

        conn = sqlite3.connect(str(tmp_db))
        run_id = conn.execute(
            "SELECT run_id FROM runtime_feedback LIMIT 1"
        ).fetchone()[0]
        conn.close()
        assert run_id == "run-abc"

    def test_store_feedback_missing_db_raises(self, tmp_path):
        collector = RuntimeFeedbackCollector(
            project_id="test", db_path=tmp_path / "missing.db"
        )
        with pytest.raises(FileNotFoundError):
            collector.store_feedback(
                [{"test_file": "t.py", "test_function": "test_a",
                  "test_passed": True, "test_duration_ms": 1.0,
                  "source_file": "", "source_function": "a"}],
                db_path=tmp_path / "missing.db",
            )


# ---------------------------------------------------------------------------
# TestCollectPipeline
# ---------------------------------------------------------------------------

class TestCollectPipeline:

    def test_collect_from_xml_returns_summary(self, sample_junit_xml, tmp_db):
        collector = RuntimeFeedbackCollector(project_id="test", db_path=tmp_db)
        summary = collector.collect_from_xml(sample_junit_xml, run_id="run-x", db_path=tmp_db)
        assert summary["total_tests"] == 3
        assert summary["passed"] == 2
        assert summary["failed"] == 1
        assert summary["pass_rate"] == round(2 / 3, 4)
        assert summary["stored_rows"] == 3
        assert summary["run_id"] == "run-x"

    def test_collect_from_xml_without_db(self, sample_junit_xml, tmp_path):
        collector = RuntimeFeedbackCollector(
            project_id="test", db_path=tmp_path / "nonexistent.db"
        )
        summary = collector.collect_from_xml(sample_junit_xml, run_id="run-y")
        assert summary["total_tests"] == 3
        assert summary["stored_rows"] == 0  # DB missing, graceful fallback


# ---------------------------------------------------------------------------
# TestHealthScore
# ---------------------------------------------------------------------------

class TestHealthScore:

    def test_health_score_with_both_tables(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        # Insert code quality metric
        conn.execute(
            """INSERT INTO code_quality_metrics
            (id, project_id, file_path, function_name, language,
             cyclomatic_complexity, cognitive_complexity, nesting_depth,
             smell_count, maintainability_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            ("cqm-test1", "proj", "foo.py", "process", "python",
             8, 5, 3, 1, 0.75),
        )
        # Insert runtime feedback
        for i, passed in enumerate([1, 1, 1, 0]):
            conn.execute(
                """INSERT INTO runtime_feedback
                (id, project_id, source_file, source_function,
                 test_file, test_function, test_passed, test_duration_ms,
                 run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (f"rf-test{i}", "proj", "foo.py", "process",
                 "tests/test_foo.py", f"test_process_{i}", passed,
                 10.0 + i, "run-health"),
            )
        conn.commit()
        conn.close()

        collector = RuntimeFeedbackCollector(project_id="proj", db_path=tmp_db)
        health = collector.compute_function_health("process", db_path=tmp_db)

        assert health is not None
        assert health["function_name"] == "process"
        assert health["cyclomatic_complexity"] == 8
        assert health["test_total"] == 4
        assert health["test_passed"] == 3
        assert health["test_pass_rate"] == 0.75
        assert 0.0 <= health["health_score"] <= 1.0

    def test_health_score_no_data_returns_none(self, tmp_db):
        collector = RuntimeFeedbackCollector(project_id="proj", db_path=tmp_db)
        health = collector.compute_function_health("nonexistent", db_path=tmp_db)
        assert health is None

    def test_health_score_missing_db_returns_none(self, tmp_path):
        collector = RuntimeFeedbackCollector(
            project_id="proj", db_path=tmp_path / "missing.db"
        )
        health = collector.compute_function_health("func")
        assert health is None
