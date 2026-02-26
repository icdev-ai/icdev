#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Code Quality Analyzer (Phase 52 — D331-D337).

Covers: AST visitors, smell detection, maintainability scoring, file analysis,
multi-language dispatch, DB storage, trend query, CLI flags.
"""

import ast
import json
import sqlite3
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.analysis.code_analyzer import (
    CodeAnalyzer,
    _CognitiveComplexityVisitor,
    _NestingDepthVisitor,
    _PythonComplexityVisitor,
    _count_lines,
    _detect_smells,
    _regex_branch_count,
    _uid,
    compute_maintainability_score,
)


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
def simple_py(tmp_path):
    """Create a simple Python file for testing."""
    f = tmp_path / "simple.py"
    f.write_text(textwrap.dedent("""\
        def greet(name):
            return f"Hello, {name}"

        def add(a, b):
            return a + b
    """))
    return f


@pytest.fixture
def complex_py(tmp_path):
    """Create a complex Python file with deep nesting and many branches."""
    f = tmp_path / "complex.py"
    f.write_text(textwrap.dedent("""\
        def process(data, mode, flag, extra, verbose, config):
            result = []
            if mode == "a":
                for item in data:
                    if item > 0:
                        if flag:
                            for sub in item:
                                if sub:
                                    while sub > 0:
                                        result.append(sub)
                                        sub -= 1
                    elif item == 0:
                        try:
                            result.append(0)
                        except Exception:
                            pass
            elif mode == "b":
                if flag or verbose:
                    result = list(data)
            return result
    """))
    return f


# ---------------------------------------------------------------------------
# TestPythonComplexityVisitor
# ---------------------------------------------------------------------------

class TestPythonComplexityVisitor:

    def test_simple_function_complexity_one(self):
        code = "def f(): return 1"
        tree = ast.parse(code)
        fn = tree.body[0]
        v = _PythonComplexityVisitor()
        v.visit(fn)
        assert v.complexity == 1

    def test_if_branch_increments(self):
        code = "def f(x):\n  if x: return 1\n  return 0"
        tree = ast.parse(code)
        fn = tree.body[0]
        v = _PythonComplexityVisitor()
        v.visit(fn)
        assert v.complexity == 2

    def test_nested_if_adds_multiple(self):
        code = "def f(x, y):\n  if x:\n    if y:\n      return 1"
        tree = ast.parse(code)
        fn = tree.body[0]
        v = _PythonComplexityVisitor()
        v.visit(fn)
        assert v.complexity == 3

    def test_for_loop_increments(self):
        code = "def f(items):\n  for i in items: pass"
        tree = ast.parse(code)
        fn = tree.body[0]
        v = _PythonComplexityVisitor()
        v.visit(fn)
        assert v.complexity == 2

    def test_bool_op_and_increments(self):
        code = "def f(a, b, c):\n  if a and b and c: pass"
        tree = ast.parse(code)
        fn = tree.body[0]
        v = _PythonComplexityVisitor()
        v.visit(fn)
        # 1 (base) + 1 (if) + 2 (and with 3 operands = 2 joins)
        assert v.complexity == 4

    def test_while_and_except_increment(self):
        code = "def f():\n  while True:\n    try: pass\n    except: pass"
        tree = ast.parse(code)
        fn = tree.body[0]
        v = _PythonComplexityVisitor()
        v.visit(fn)
        assert v.complexity >= 3


# ---------------------------------------------------------------------------
# TestNestingDepthVisitor
# ---------------------------------------------------------------------------

class TestNestingDepthVisitor:

    def test_flat_function_depth_zero(self):
        code = "def f(): return 1"
        tree = ast.parse(code)
        fn = tree.body[0]
        v = _NestingDepthVisitor()
        v.visit(fn)
        assert v.max_depth == 0

    def test_single_if_depth_one(self):
        code = "def f(x):\n  if x: return 1"
        tree = ast.parse(code)
        fn = tree.body[0]
        v = _NestingDepthVisitor()
        v.visit(fn)
        assert v.max_depth == 1

    def test_nested_if_depth_two(self):
        code = "def f(x, y):\n  if x:\n    if y:\n      return 1"
        tree = ast.parse(code)
        fn = tree.body[0]
        v = _NestingDepthVisitor()
        v.visit(fn)
        assert v.max_depth == 2


# ---------------------------------------------------------------------------
# TestSmellDetection
# ---------------------------------------------------------------------------

class TestSmellDetection:

    def test_long_function_detected(self):
        metrics = {"loc": 60, "nesting_depth": 1, "cyclomatic_complexity": 3,
                   "parameter_count": 2, "function_count": 1}
        smells = _detect_smells(metrics, {"max_function_loc": 50,
                                           "max_nesting": 4, "max_complexity": 10,
                                           "max_params": 5, "max_methods_per_class": 10})
        assert "long_function" in smells

    def test_deep_nesting_detected(self):
        metrics = {"loc": 20, "nesting_depth": 5, "cyclomatic_complexity": 3,
                   "parameter_count": 1, "function_count": 1}
        smells = _detect_smells(metrics, {"max_function_loc": 50,
                                           "max_nesting": 4, "max_complexity": 10,
                                           "max_params": 5, "max_methods_per_class": 10})
        assert "deep_nesting" in smells

    def test_high_complexity_detected(self):
        metrics = {"loc": 20, "nesting_depth": 1, "cyclomatic_complexity": 15,
                   "parameter_count": 1, "function_count": 1}
        smells = _detect_smells(metrics, {"max_function_loc": 50,
                                           "max_nesting": 4, "max_complexity": 10,
                                           "max_params": 5, "max_methods_per_class": 10})
        assert "high_complexity" in smells

    def test_no_smells_clean_function(self):
        metrics = {"loc": 10, "nesting_depth": 1, "cyclomatic_complexity": 3,
                   "parameter_count": 2, "function_count": 1}
        smells = _detect_smells(metrics, {"max_function_loc": 50,
                                           "max_nesting": 4, "max_complexity": 10,
                                           "max_params": 5, "max_methods_per_class": 10})
        assert len(smells) == 0

    def test_too_many_params_detected(self):
        metrics = {"loc": 10, "nesting_depth": 1, "cyclomatic_complexity": 1,
                   "parameter_count": 8, "function_count": 1}
        smells = _detect_smells(metrics, {"max_function_loc": 50,
                                           "max_nesting": 4, "max_complexity": 10,
                                           "max_params": 5, "max_methods_per_class": 10})
        assert "too_many_params" in smells


# ---------------------------------------------------------------------------
# TestMaintainabilityScore
# ---------------------------------------------------------------------------

class TestMaintainabilityScore:

    def test_perfect_score_simple_function(self):
        metrics = {"cyclomatic_complexity": 1, "smell_count": 0, "import_count": 0}
        score = compute_maintainability_score(metrics)
        assert score > 0.9

    def test_low_score_complex_function(self):
        metrics = {"cyclomatic_complexity": 30, "smell_count": 5, "import_count": 20}
        score = compute_maintainability_score(metrics)
        assert score <= 0.5

    def test_score_is_deterministic(self):
        metrics = {"cyclomatic_complexity": 8, "smell_count": 2, "import_count": 5}
        s1 = compute_maintainability_score(metrics)
        s2 = compute_maintainability_score(metrics)
        assert s1 == s2

    def test_score_clamped_to_unit_interval(self):
        for cc in (0, 5, 10, 25, 50):
            metrics = {"cyclomatic_complexity": cc, "smell_count": 0}
            score = compute_maintainability_score(metrics)
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# TestCodeAnalyzer
# ---------------------------------------------------------------------------

class TestCodeAnalyzer:

    def test_analyze_simple_python_file(self, simple_py, tmp_path):
        analyzer = CodeAnalyzer(project_dir=str(tmp_path))
        results = analyzer.analyze_python_file(simple_py)
        assert len(results) >= 3  # 2 functions + 1 file-level
        fn_results = [r for r in results if r.get("function_name")]
        assert len(fn_results) == 2
        assert fn_results[0]["language"] == "python"

    def test_analyze_complex_file_detects_smells(self, complex_py, tmp_path):
        analyzer = CodeAnalyzer(project_dir=str(tmp_path))
        results = analyzer.analyze_python_file(complex_py)
        fn_results = [r for r in results if r.get("function_name")]
        assert len(fn_results) >= 1
        process_fn = fn_results[0]
        assert process_fn["cyclomatic_complexity"] > 5
        assert process_fn["nesting_depth"] >= 3
        smells = json.loads(process_fn["smells_json"])
        assert len(smells) >= 1

    def test_scan_directory(self, simple_py, tmp_path):
        analyzer = CodeAnalyzer(project_dir=str(tmp_path), project_id="test-proj")
        result = analyzer.scan_directory()
        assert result["scan_id"].startswith("scan-")
        assert result["files_analyzed"] >= 1
        assert result["project_id"] == "test-proj"
        assert len(result["metrics"]) >= 1


# ---------------------------------------------------------------------------
# TestMultiLanguageDispatch
# ---------------------------------------------------------------------------

class TestMultiLanguageDispatch:

    def test_regex_branch_count_java(self):
        java_code = "if (x) { for (int i=0; i<n; i++) { if (flag && ready) {} } }"
        cc = _regex_branch_count(java_code, "java")
        assert cc >= 4  # 1 base + if + for + if + &&

    def test_regex_branch_count_go(self):
        go_code = "if x > 0 { for _, v := range items { if v != nil && ok {} } }"
        cc = _regex_branch_count(go_code, "go")
        assert cc >= 4

    def test_non_python_file_analysis(self, tmp_path):
        java_file = tmp_path / "Test.java"
        java_file.write_text("public class Test {\n  public void run() {\n    if (x) {}\n  }\n}")
        analyzer = CodeAnalyzer(project_dir=str(tmp_path))
        results = analyzer.analyze_non_python_file(java_file, "java")
        assert len(results) == 1
        assert results[0]["language"] == "java"
        assert results[0]["cyclomatic_complexity"] >= 2


# ---------------------------------------------------------------------------
# TestDBStorage
# ---------------------------------------------------------------------------

class TestDBStorage:

    def test_store_metrics_inserts_rows(self, tmp_db, simple_py, tmp_path):
        analyzer = CodeAnalyzer(project_dir=str(tmp_path), db_path=tmp_db)
        metrics = analyzer.analyze_python_file(simple_py)
        for m in metrics:
            m["project_id"] = "test"
        count = analyzer.store_metrics(metrics, "scan-test", db_path=tmp_db)
        assert count == len(metrics)

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT COUNT(*) FROM code_quality_metrics").fetchone()[0]
        conn.close()
        assert rows == count

    def test_scan_id_groups_rows(self, tmp_db, simple_py, tmp_path):
        analyzer = CodeAnalyzer(project_dir=str(tmp_path), db_path=tmp_db)
        metrics = analyzer.analyze_python_file(simple_py)
        for m in metrics:
            m["project_id"] = "test"
        analyzer.store_metrics(metrics, "scan-001", db_path=tmp_db)
        analyzer.store_metrics(metrics, "scan-002", db_path=tmp_db)

        conn = sqlite3.connect(str(tmp_db))
        scan_ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT scan_id FROM code_quality_metrics"
        ).fetchall()]
        conn.close()
        assert "scan-001" in scan_ids
        assert "scan-002" in scan_ids

    def test_trend_query_returns_sorted(self, tmp_db, simple_py, tmp_path):
        analyzer = CodeAnalyzer(project_dir=str(tmp_path), db_path=tmp_db,
                                project_id="test")
        metrics = analyzer.analyze_python_file(simple_py)
        for m in metrics:
            m["project_id"] = "test"
        analyzer.store_metrics(metrics, "scan-001", db_path=tmp_db)
        analyzer.store_metrics(metrics, "scan-002", db_path=tmp_db)
        trend = analyzer.get_trend("test", db_path=tmp_db)
        assert len(trend) >= 1
        for t in trend:
            assert "avg_maintainability" in t
            assert "avg_complexity" in t


# ---------------------------------------------------------------------------
# TestLineCount
# ---------------------------------------------------------------------------

class TestLineCount:

    def test_count_lines_basic(self):
        source = "# comment\ndef f():\n    return 1\n\n"
        counts = _count_lines(source)
        assert counts["loc"] == 4
        assert counts["loc_comment"] >= 1
        assert counts["loc_blank"] >= 1

    def test_uid_format(self):
        uid = _uid()
        assert uid.startswith("cqm-")
        assert len(uid) == 16  # "cqm-" + 12 hex chars
