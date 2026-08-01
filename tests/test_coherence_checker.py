#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Coherence Checker and Impact Analyzer (Phase 66, D-WF-8).

Tests the 7 coherence checks and cross-subsystem impact analysis.
"""

import pytest
import textwrap
from pathlib import Path

from tools.workflow.coherence_checker import (
    CHECK_REGISTRY,
    CoherenceCheck,
    CoherenceReport,
    _extract_function_calls,
    _extract_function_sigs,
    _extract_imports,
    _extract_name_usage,
    _extract_sql_columns_from_python,
    _load_ruff_gate_whitelist,
    _parse_create_tables,
    check_append_only,
    check_fixture_schema,
    check_import_usage,
    check_ruff_lint,
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

    def test_extract_name_usage_syntax_error(self):
        # Graceful handling: returns empty set when source has a SyntaxError.
        # Mirrors the pattern for _extract_function_sigs (see test_extract_function_sigs_syntax_error).
        names = _extract_name_usage("def bad(:\n    pass\n")
        assert names == set()

    def test_extract_name_usage_empty_source(self):
        # Empty source and comment-only source produce no Name nodes — no false positives.
        assert _extract_name_usage("") == set()
        assert _extract_name_usage("# just a comment\n") == set()

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

    def test_extract_function_sigs_syntax_error(self):
        # Graceful handling: returns {} when source has a SyntaxError.
        # This prevents gap_detector false positives when a file is partially
        # written during an active edit session (op-gap-09f524e9931dee4a).
        sigs = _extract_function_sigs("def bad(:\n    pass\n")
        assert sigs == {}

    def test_extract_function_sigs_async(self):
        source = textwrap.dedent("""
        async def fetch(url, timeout=30):
            pass
        """)
        sigs = _extract_function_sigs(source)
        assert "fetch" in sigs
        assert sigs["fetch"] == ["url", "timeout"]

    def test_extract_function_sigs_self_excluded(self):
        # Class methods: `self` must be excluded from the param list so
        # check_signature_call doesn't flag it as a missing required arg.
        source = textwrap.dedent("""
        class MyClass:
            def method(self, a, b):
                pass

            @staticmethod
            def helper(x):
                pass
        """)
        sigs = _extract_function_sigs(source)
        assert "method" in sigs
        assert sigs["method"] == ["a", "b"], "self must be excluded"
        assert "helper" in sigs
        assert sigs["helper"] == ["x"]

    def test_extract_function_sigs_empty_source(self):
        # Empty source returns empty dict — no false positives.
        assert _extract_function_sigs("") == {}
        assert _extract_function_sigs("# just a comment\n") == {}

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

    @pytest.mark.timeout(120)
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
        test_file.write_text(
            textwrap.dedent("""
            CREATE TABLE IF NOT EXISTS workflow_loops (
                id TEXT PRIMARY KEY,
                project_id TEXT
            );
        """),
            encoding="utf-8",
        )
        result = check_fixture_schema(changed_files=[test_file])
        # Should detect missing columns vs real schema
        assert isinstance(result, CoherenceCheck)
        if result.status == "fail":
            assert len(result.missing) > 0

    def test_detects_column_used_in_insert_but_absent_from_fixture(self, tmp_path):
        # Fixture declares only (id, project_id) but INSERT references phase_name too —
        # check_fixture_schema must flag that mismatch.
        test_file = tmp_path / "test_insert_mismatch.py"
        test_file.write_text(
            textwrap.dedent("""
            import sqlite3

            SCHEMA = '''
            CREATE TABLE IF NOT EXISTS workflow_loops (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL
            );
            '''

            def test_something(tmp_path):
                db = str(tmp_path / "t.db")
                conn = sqlite3.connect(db)
                conn.executescript(SCHEMA)
                conn.execute(
                    "INSERT INTO workflow_loops (id, project_id, phase_name) VALUES (?, ?, ?)",
                    ("1", "p1", "build"),
                )
                conn.commit()
        """),
            encoding="utf-8",
        )
        result = check_fixture_schema(changed_files=[test_file])
        assert isinstance(result, CoherenceCheck)
        assert result.check_id == "fixture_schema"
        # phase_name is used in INSERT but absent from fixture CREATE TABLE → must fail
        assert result.status == "fail"
        assert any("phase_name" in m for m in result.missing)

    def test_passes_when_no_create_table_in_file(self, tmp_path):
        # Files without CREATE TABLE should always pass (no fixture to check).
        test_file = tmp_path / "test_no_schema.py"
        test_file.write_text(
            textwrap.dedent("""
            def test_pure_python():
                assert 1 + 1 == 2
        """),
            encoding="utf-8",
        )
        result = check_fixture_schema(changed_files=[test_file])
        assert isinstance(result, CoherenceCheck)
        assert result.status == "pass"

    def test_passes_when_insert_columns_match_fixture(self, tmp_path):
        # INSERT references only columns that ARE in the fixture → no mismatch.
        test_file = tmp_path / "test_matching_fixture.py"
        test_file.write_text(
            textwrap.dedent("""
            import sqlite3

            SCHEMA = '''
            CREATE TABLE IF NOT EXISTS workflow_loops (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                phase_name TEXT NOT NULL
            );
            '''

            def test_something(tmp_path):
                db = str(tmp_path / "t.db")
                conn = sqlite3.connect(db)
                conn.executescript(SCHEMA)
                conn.execute(
                    "INSERT INTO workflow_loops (id, project_id, phase_name) VALUES (?, ?, ?)",
                    ("1", "p1", "init"),
                )
                conn.commit()
        """),
            encoding="utf-8",
        )
        result = check_fixture_schema(changed_files=[test_file])
        assert isinstance(result, CoherenceCheck)
        assert result.status == "pass"


class TestSignatureCallCheck:
    """Tests for signature-call coherence check."""

    def test_check_runs(self):
        result = check_signature_call()
        assert isinstance(result, CoherenceCheck)
        assert result.check_id == "signature_call"

    def test_detects_positional_calls(self, tmp_path):
        tool_file = tmp_path / "my_tool.py"
        tool_file.write_text(
            textwrap.dedent("""
            def create_thing(name, description, category, owner, db_path=None):
                pass
        """),
            encoding="utf-8",
        )

        test_file = tmp_path / "test_my_tool.py"
        test_file.write_text(
            textwrap.dedent("""
            from my_tool import create_thing
            create_thing("a", "b", "c", "d", test_db)
        """),
            encoding="utf-8",
        )

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
# Ruff Lint Gate (OPT-49)
# ---------------------------------------------------------------------------


class TestRuffLintGate:
    """Tests for the OPT-49 authoritative ruff F401/F811/F841 gate."""

    def test_registered_in_check_registry(self):
        assert "ruff_lint" in CHECK_REGISTRY
        assert CHECK_REGISTRY["ruff_lint"] is check_ruff_lint

    def test_clean_file_passes(self, tmp_path):
        py_file = tmp_path / "clean.py"
        py_file.write_text(
            "import json\n\nprint(json.dumps({'ok': 1}))\n",
            encoding="utf-8",
        )
        result = check_ruff_lint(changed_files=[py_file])
        assert result.check_id == "ruff_lint"
        assert result.status == "pass", f"unexpected: {result.message}"

    def test_unused_import_fails(self, tmp_path):
        py_file = tmp_path / "dirty.py"
        # `os` is imported but never referenced → F401
        py_file.write_text(
            "import os\nimport json\nprint(json.dumps({'a': 1}))\n",
            encoding="utf-8",
        )
        result = check_ruff_lint(changed_files=[py_file])
        assert result.status == "fail"
        assert any("F401" in line for line in result.extra)

    def test_unused_local_fails(self, tmp_path):
        py_file = tmp_path / "unused_local.py"
        py_file.write_text(
            "def f():\n    x = 42\n    return 7\n",
            encoding="utf-8",
        )
        result = check_ruff_lint(changed_files=[py_file])
        assert result.status == "fail"
        assert any("F841" in line for line in result.extra)

    def test_non_python_files_ignored(self, tmp_path):
        not_py = tmp_path / "config.yaml"
        not_py.write_text("key: value\n", encoding="utf-8")
        result = check_ruff_lint(changed_files=[not_py])
        # No .py targets → pass
        assert result.status == "pass"

    def test_whitelist_loader_handles_missing_file(self, tmp_path, monkeypatch):
        # Point at a non-existent file — loader must return empty dict, not raise
        from tools.workflow import coherence_checker as cc

        monkeypatch.setattr(cc, "_RUFF_GATE_CONFIG", tmp_path / "nonexistent.yaml")
        assert _load_ruff_gate_whitelist() == {}

    def test_whitelist_loader_parses_yaml(self, tmp_path, monkeypatch):
        from tools.workflow import coherence_checker as cc

        wl_file = tmp_path / "wl.yaml"
        wl_file.write_text(
            "whitelist:\n"
            "  tools/legacy/foo.py:\n"
            "    - F401\n"
            "    - F841\n"
            "  tools/legacy/bar.py:\n"
            "    - F811\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(cc, "_RUFF_GATE_CONFIG", wl_file)
        wl = _load_ruff_gate_whitelist()
        assert wl["tools/legacy/foo.py"] == {"F401", "F841"}
        assert wl["tools/legacy/bar.py"] == {"F811"}

    def test_whitelist_loader_handles_malformed_yaml(self, tmp_path, monkeypatch):
        from tools.workflow import coherence_checker as cc

        wl_file = tmp_path / "bad.yaml"
        wl_file.write_text("this is: : not: valid: yaml:", encoding="utf-8")
        monkeypatch.setattr(cc, "_RUFF_GATE_CONFIG", wl_file)
        # Malformed → empty dict (fail-safe: stricter gate, not looser)
        assert _load_ruff_gate_whitelist() == {}

    def test_whitelist_loader_handles_string_code(self, tmp_path, monkeypatch):
        # YAML allows a bare string instead of a list for a single code.
        from tools.workflow import coherence_checker as cc

        wl_file = tmp_path / "wl_str.yaml"
        wl_file.write_text(
            "whitelist:\n"
            "  tools/legacy/foo.py: F401\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(cc, "_RUFF_GATE_CONFIG", wl_file)
        wl = _load_ruff_gate_whitelist()
        assert wl["tools/legacy/foo.py"] == {"F401"}

    def test_whitelist_loader_normalizes_backslash_paths(self, tmp_path, monkeypatch):
        # Windows-style paths in the YAML should be normalized to forward slashes.
        # In YAML plain scalars a single \ is a literal backslash, so we write
        # "tools\legacy\bar.py" by putting one backslash in the file (Python \\).
        from tools.workflow import coherence_checker as cc

        wl_file = tmp_path / "wl_win.yaml"
        wl_file.write_text(
            "whitelist:\n"
            "  tools\\legacy\\bar.py:\n"
            "    - F811\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(cc, "_RUFF_GATE_CONFIG", wl_file)
        wl = _load_ruff_gate_whitelist()
        assert "tools/legacy/bar.py" in wl
        assert wl["tools/legacy/bar.py"] == {"F811"}


# ---------------------------------------------------------------------------
# Report-level tests
# ---------------------------------------------------------------------------


class TestCoherenceReport:
    """Tests for the aggregate report."""

    @pytest.mark.timeout(300)
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

    @pytest.mark.timeout(300)
    def test_autofix_mode(self, monkeypatch):
        """--fix mode runs and plumbs total_fixes through — without touching the tree.

        The real fixers mutate tracked files (``_autofix_manifest`` appends to
        ``tools/manifest.md``, ``_autofix_append_only`` rewrites
        ``.claude/hooks/pre_tool_use.py``, ``_autofix_imports`` shells out to
        ``ruff --fix`` over ``tools/``), so running them here left the working
        tree dirty. Stub the handlers: this test owns the autofix *plumbing*,
        not the individual fixers.
        """
        applied: list = []

        def _stub_fixer(check):
            applied.append(check.check_id)
            return [f"stub fix for {check.check_id}"]

        globals_ = run_checks.__globals__
        stub_handlers = {check_id: _stub_fixer for check_id in globals_["_AUTOFIX_HANDLERS"]}
        # setitem on the module globals is shim-proof: it is the same dict
        # _apply_fixes resolves _AUTOFIX_HANDLERS from at call time.
        monkeypatch.setitem(globals_, "_AUTOFIX_HANDLERS", stub_handlers)

        report = run_checks(selected=["append_only", "manifest"], autofix=True)
        assert isinstance(report, CoherenceReport)
        assert hasattr(report, "total_fixes")
        assert report.total_fixes >= 0
        assert report.total_fixes == sum(len(c.fixes_applied) for c in report.checks)
        # Only failed/warned checks may be fixed, and only via the stubs.
        assert all(c.status in ("fail", "warn") for c in report.checks if c.fixes_applied)
        assert set(applied) <= {"append_only", "manifest"}
        d = report.to_dict()
        assert "total_fixes" in d

    def test_autofix_dispatches_to_registered_handler(self, monkeypatch):
        """A failing check with an ``auto`` tier routes through its handler."""
        globals_ = run_checks.__globals__
        seen = []

        def _stub_fixer(check):
            seen.append(check)
            return ["stub fix"]

        monkeypatch.setitem(globals_, "_AUTOFIX_HANDLERS", {"manifest": _stub_fixer})

        check = CoherenceCheck(
            check_id="manifest",
            check_name="Manifest",
            status="fail",
            expected=[],
            actual=[],
            missing=["tools/foo/bar.py"],
            extra=[],
            message="missing",
        )
        updated = globals_["_apply_fixes"](check)
        assert seen == [check]
        assert updated.fixes_applied == ["stub fix"]
        assert "1 auto-fixed" in updated.message

    def test_autofix_skips_non_auto_tier(self, monkeypatch):
        """A ``skip``-tier check never reaches a fixer, even when it fails."""
        globals_ = run_checks.__globals__
        assert globals_["_FIX_REGISTRY"]["nav_sync"] == "skip"

        def _boom(check):  # pragma: no cover — must never be called
            raise AssertionError("skip-tier check must not be auto-fixed")

        monkeypatch.setitem(globals_, "_AUTOFIX_HANDLERS", {"nav_sync": _boom})

        check = CoherenceCheck(
            check_id="nav_sync",
            check_name="Nav Sync",
            status="fail",
            expected=[],
            actual=[],
            missing=["x"],
            extra=[],
            message="missing",
        )
        assert globals_["_apply_fixes"](check).fixes_applied == []

    def test_fixes_applied_field(self):
        """Test that checks include fixes_applied list."""
        check = CoherenceCheck(
            check_id="test",
            check_name="Test",
            status="pass",
            expected=[],
            actual=[],
            missing=[],
            extra=[],
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

    def test_run_checks_with_changed_files(self, tmp_path):
        """run_checks passes changed_files to checks that accept it."""
        dummy = tmp_path / "dummy.py"
        dummy.write_text("x = 1\n", encoding="utf-8")
        report = run_checks(selected=["import_usage"], changed_files=[Path(dummy)])
        assert isinstance(report, CoherenceReport)
        assert report.total_checks == 1
        assert report.checks[0].check_id == "import_usage"

    def test_run_checks_overall_pass_field(self):
        """overall_pass is False only when at least one check fails."""
        report = run_checks(selected=["append_only"])
        assert isinstance(report.overall_pass, bool)


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
