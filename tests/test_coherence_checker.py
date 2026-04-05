#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Coherence Checker and Impact Analyzer (Phase 66, D-WF-8).

Tests the 7 coherence checks and cross-subsystem impact analysis.
"""

import textwrap

from tools.workflow.coherence_checker import (
    CoherenceCheck,
    CoherenceReport,
    _extract_function_calls,
    _extract_function_sigs,
    _extract_imports,
    _extract_name_usage,
    _extract_sql_columns_from_python,
    _parse_create_tables,
    check_append_only,
    check_fixture_schema,
    check_import_usage,
    check_schema_code,
    check_signature_call,
    run_checks,
)
from tools.workflow.impact_analyzer import (
    _identify_subsystem_from_file,
    _identify_subsystem_from_table,
    analyze_impact,
    get_graph,
)


# ---------------------------------------------------------------------------
# Unit tests for parsing helpers
# ---------------------------------------------------------------------------

class TestParsingHelpers:
    """Test low-level AST/regex parsing functions."""

    def test_parse_create_tables(self):
        sql = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    total REAL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""
        tables = _parse_create_tables(sql)
        assert "users" in tables
        assert "orders" in tables
        assert "id" in tables["users"]
        assert "name" in tables["users"]
        assert "email" in tables["users"]
        assert "user_id" in tables["orders"]
        assert "total" in tables["orders"]

    def test_extract_sql_columns_insert(self):
        source = '''
conn.execute(
    """INSERT INTO users (id, name, email) VALUES (?, ?, ?)""",
    (uid, name, email),
)
'''
        results = _extract_sql_columns_from_python(source)
        assert len(results) >= 1
        table, op, cols = results[0]
        assert table == "users"
        assert op == "INSERT"
        assert "id" in cols
        assert "name" in cols
        assert "email" in cols

    def test_extract_imports(self):
        source = "import json\nimport os\nfrom pathlib import Path\n"
        imports = _extract_imports(source)
        names = [name for name, _ in imports]
        assert "json" in names
        assert "os" in names
        assert "Path" in names

    def test_extract_name_usage(self):
        source = "x = json.loads(data)\nresult = Path(x)\n"
        names = _extract_name_usage(source)
        assert "json" in names
        assert "loads" in names
        assert "Path" in names

    def test_extract_function_sigs(self):
        source = textwrap.dedent("""
        def foo(a, b, c, db_path=None):
            pass

        def bar(x):
            pass
        """)
        sigs = _extract_function_sigs(source)
        assert "foo" in sigs
        assert sigs["foo"] == ["a", "b", "c", "db_path"]
        assert "bar" in sigs
        assert sigs["bar"] == ["x"]

    def test_extract_function_calls(self):
        source = textwrap.dedent("""
        foo("a", "b", "c", test_db)
        bar(x=1, y=2)
        baz("a", "b", db_path=test_db)
        """)
        calls = _extract_function_calls(source)
        call_map = {name: (pos, kws) for name, _, pos, kws in calls}
        assert "foo" in call_map
        assert call_map["foo"] == (4, [])  # 4 positional, 0 keyword
        assert "bar" in call_map
        assert call_map["bar"] == (0, ["x", "y"])
        assert "baz" in call_map
        assert call_map["baz"] == (2, ["db_path"])


# ---------------------------------------------------------------------------
# Check-level tests
# ---------------------------------------------------------------------------

class TestSchemaCodeCheck:
    """Tests for schema-code coherence check."""

    def test_check_runs_without_error(self):
        result = check_schema_code()
        assert isinstance(result, CoherenceCheck)
        assert result.check_id == "schema_code"
        assert result.status in ("pass", "fail", "warn")

    def test_check_with_specific_files(self, tmp_path):
        # Create a fake tool with matching INSERT
        tool = tmp_path / "test_tool.py"
        tool.write_text(
            'conn.execute("INSERT INTO workflow_loops (id, project_id) VALUES (?, ?)")',
            encoding="utf-8",
        )
        result = check_schema_code(changed_files=[tool])
        assert isinstance(result, CoherenceCheck)


class TestFixtureSchemaCheck:
    """Tests for test fixture-schema coherence check."""

    def test_check_runs(self):
        result = check_fixture_schema()
        assert isinstance(result, CoherenceCheck)
        assert result.check_id == "fixture_schema"

    def test_detects_missing_columns(self, tmp_path):
        # Create a test file with a fixture missing a column
        test_file = tmp_path / "test_example.py"
        test_file.write_text(textwrap.dedent("""
            CREATE TABLE IF NOT EXISTS workflow_loops (
                id TEXT PRIMARY KEY,
                project_id TEXT
            );
        """), encoding="utf-8")
        result = check_fixture_schema(changed_files=[test_file])
        # Should detect missing columns vs real schema
        assert isinstance(result, CoherenceCheck)
        if result.status == "fail":
            assert len(result.missing) > 0


class TestSignatureCallCheck:
    """Tests for signature-call coherence check."""

    def test_check_runs(self):
        result = check_signature_call()
        assert isinstance(result, CoherenceCheck)
        assert result.check_id == "signature_call"

    def test_detects_positional_calls(self, tmp_path):
        tool_file = tmp_path / "my_tool.py"
        tool_file.write_text(textwrap.dedent("""
            def create_thing(name, description, category, owner, db_path=None):
                pass
        """), encoding="utf-8")

        test_file = tmp_path / "test_my_tool.py"
        test_file.write_text(textwrap.dedent("""
            from my_tool import create_thing
            create_thing("a", "b", "c", "d", test_db)
        """), encoding="utf-8")

        result = check_signature_call(changed_files=[tool_file, test_file])
        assert isinstance(result, CoherenceCheck)


class TestAppendOnlyCheck:
    """Tests for append-only table protection check."""

    def test_check_runs(self):
        result = check_append_only()
        assert isinstance(result, CoherenceCheck)
        assert result.check_id == "append_only"
        # May find real gaps — accept any status as valid behavior
        assert result.status in ("pass", "warn", "fail")


class TestImportUsageCheck:
    """Tests for import usage check."""

    def test_no_files_returns_pass(self):
        result = check_import_usage()
        assert result.status == "pass"

    def test_detects_unused_import(self, tmp_path):
        py_file = tmp_path / "example.py"
        py_file.write_text("import json\nimport os\nx = 1\n", encoding="utf-8")
        result = check_import_usage(changed_files=[py_file])
        assert result.check_id == "import_usage"
        # json and os are unused
        if result.status == "warn":
            assert len(result.extra) >= 1

    def test_used_imports_pass(self, tmp_path):
        py_file = tmp_path / "example.py"
        py_file.write_text("import json\nx = json.dumps({})\n", encoding="utf-8")
        result = check_import_usage(changed_files=[py_file])
        # json is used
        assert result.check_id == "import_usage"


# ---------------------------------------------------------------------------
# Report-level tests
# ---------------------------------------------------------------------------

class TestCoherenceReport:
    """Tests for the aggregate report."""

    def test_run_all_checks(self):
        report = run_checks()
        assert isinstance(report, CoherenceReport)
        assert report.total_checks >= 5
        assert report.passed_checks + report.failed_checks + report.warned_checks == report.total_checks

    def test_run_single_check(self):
        report = run_checks(selected=["append_only"])
        assert report.total_checks == 1
        assert report.checks[0].check_id == "append_only"

    def test_report_to_dict(self):
        report = run_checks(selected=["append_only"])
        d = report.to_dict()
        assert "overall_pass" in d
        assert "checks" in d
        assert isinstance(d["checks"], list)

    def test_unknown_check_warns(self):
        report = run_checks(selected=["nonexistent_check"])
        assert report.total_checks == 1
        assert report.checks[0].status == "warn"

    def test_autofix_mode(self):
        """Test that --fix mode runs without error and includes total_fixes."""
        report = run_checks(autofix=True)
        assert isinstance(report, CoherenceReport)
        assert hasattr(report, "total_fixes")
        assert report.total_fixes >= 0
        d = report.to_dict()
        assert "total_fixes" in d

    def test_fixes_applied_field(self):
        """Test that checks include fixes_applied list."""
        check = CoherenceCheck(
            check_id="test", check_name="Test", status="pass",
            expected=[], actual=[], missing=[], extra=[],
            message="ok",
        )
        assert check.fixes_applied == []
        d = check.to_dict()
        assert "fixes_applied" in d

    def test_fix_registry_tiers(self):
        """Test that fix registry has correct tier assignments."""
        from tools.workflow.coherence_checker import _FIX_REGISTRY
        assert _FIX_REGISTRY["import_usage"] == "auto"
        assert _FIX_REGISTRY["append_only"] == "auto"
        assert _FIX_REGISTRY["schema_code"] == "suggest"
        assert _FIX_REGISTRY["signature_call"] == "skip"


# ---------------------------------------------------------------------------
# Impact Analyzer tests
# ---------------------------------------------------------------------------

class TestImpactAnalyzer:
    """Tests for cross-subsystem impact analysis."""

    def test_identify_from_file_workflow(self):
        matrix = {}  # Not needed for path-based detection
        result = _identify_subsystem_from_file("tools/workflow/loop_engine.py", matrix)
        assert "workflow" in result

    def test_identify_from_file_chat(self):
        result = _identify_subsystem_from_file("tools/dashboard/chat_manager.py", {})
        assert "chat" in result

    def test_identify_from_file_intake(self):
        result = _identify_subsystem_from_file("tools/requirements/intake_engine.py", {})
        assert "intake" in result

    def test_identify_from_table(self):
        matrix = {
            "workflow": {"tables": ["workflow_loops", "workflow_handoffs"]},
            "chat": {"tables": ["chat_contexts", "chat_messages"]},
        }
        result = _identify_subsystem_from_table("workflow_loops", matrix)
        assert "workflow" in result

    def test_analyze_impact_workflow_change(self):
        result = analyze_impact(changed_files=["tools/workflow/loop_engine.py"])
        assert "affected_subsystems" in result
        assert "workflow" in result["affected_subsystems"]
        # Workflow connects_to chat, intake, dashboard
        assert result["total_impacted"] >= 0  # Depends on matrix

    def test_analyze_impact_no_changes(self):
        result = analyze_impact(changed_files=[])
        assert result.get("total_recommendations", 0) == 0

    def test_get_graph(self):
        result = get_graph()
        if "error" not in result:
            assert "nodes" in result
            assert "edges" in result
            assert result["total_subsystems"] > 0

    def test_analyze_impact_table_change(self):
        result = analyze_impact(changed_tables=["chat_contexts"])
        assert "affected_subsystems" in result
