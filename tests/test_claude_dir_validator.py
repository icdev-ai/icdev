#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for .claude directory governance validator.

Covers: dataclass behavior, discovery functions, all 6 checks,
run_all_checks orchestrator, format_human output, CHECK_REGISTRY.
"""

import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.testing.claude_dir_validator import (
    CHECK_REGISTRY,
    ClaudeConfigCheck,
    ClaudeConfigReport,
    check_append_only_table_coverage,
    check_dashboard_route_documentation,
    check_e2e_test_coverage,
    check_hook_syntax,
    check_settings_deny_rules,
    check_settings_hook_references,
    discover_append_only_tables,
    discover_dashboard_page_routes,
    discover_documented_routes,
    discover_protected_tables,
    format_human,
    run_all_checks,
)


# ---------------------------------------------------------------------------
# ClaudeConfigCheck dataclass
# ---------------------------------------------------------------------------

class TestClaudeConfigCheck:
    def test_to_dict(self):
        check = ClaudeConfigCheck(
            check_id="test", check_name="Test", status="pass",
            expected=["a"], actual=["a"], missing=[], extra=[], message="ok",
        )
        d = check.to_dict()
        assert d["check_id"] == "test"
        assert d["status"] == "pass"
        assert isinstance(d["expected"], list)

    def test_passed_property_pass(self):
        check = ClaudeConfigCheck(
            check_id="t", check_name="T", status="pass",
            expected=[], actual=[], missing=[], extra=[], message="",
        )
        assert check.passed is True

    def test_passed_property_fail(self):
        check = ClaudeConfigCheck(
            check_id="t", check_name="T", status="fail",
            expected=[], actual=[], missing=[], extra=[], message="",
        )
        assert check.passed is False

    def test_passed_property_warn(self):
        check = ClaudeConfigCheck(
            check_id="t", check_name="T", status="warn",
            expected=[], actual=[], missing=[], extra=[], message="",
        )
        assert check.passed is False


# ---------------------------------------------------------------------------
# ClaudeConfigReport dataclass
# ---------------------------------------------------------------------------

class TestClaudeConfigReport:
    def test_to_dict_structure(self):
        report = ClaudeConfigReport(
            overall_pass=True, timestamp="2026-01-01T00:00:00",
            checks=[], total_checks=0, passed_checks=0,
            failed_checks=0, warned_checks=0,
        )
        d = report.to_dict()
        assert "overall_pass" in d
        assert "checks" in d
        assert isinstance(d["checks"], list)

    def test_overall_pass_true(self):
        report = ClaudeConfigReport(
            overall_pass=True, timestamp="", checks=[],
            total_checks=1, passed_checks=1, failed_checks=0, warned_checks=0,
        )
        assert report.overall_pass is True

    def test_overall_pass_false(self):
        report = ClaudeConfigReport(
            overall_pass=False, timestamp="", checks=[],
            total_checks=1, passed_checks=0, failed_checks=1, warned_checks=0,
        )
        assert report.overall_pass is False


# ---------------------------------------------------------------------------
# discover_append_only_tables
# ---------------------------------------------------------------------------

class TestDiscoverAppendOnlyTables:
    def test_discovers_table_with_append_only_comment(self, tmp_path):
        db_file = tmp_path / "init_db.py"
        db_file.write_text(textwrap.dedent("""\
            SCHEMA_SQL = \"\"\"
            -- Core audit log
            -- append-only, immutable -- NIST AU controls
            CREATE TABLE IF NOT EXISTS audit_trail (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT
            );

            -- Normal mutable table
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT
            );

            -- append-only pipeline audit
            CREATE TABLE IF NOT EXISTS pipeline_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT
            );
            \"\"\"
        """))
        tables = discover_append_only_tables(db_file)
        assert "audit_trail" in tables
        assert "pipeline_audit" in tables
        assert "projects" not in tables

    def test_discovers_immutable_comment(self, tmp_path):
        db_file = tmp_path / "init_db.py"
        db_file.write_text(textwrap.dedent("""\
            SCHEMA_SQL = \"\"\"
            -- immutable log
            CREATE TABLE IF NOT EXISTS immutable_log (
                id INTEGER PRIMARY KEY
            );
            \"\"\"
        """))
        tables = discover_append_only_tables(db_file)
        assert "immutable_log" in tables

    def test_ignores_non_append_only(self, tmp_path):
        db_file = tmp_path / "init_db.py"
        db_file.write_text(textwrap.dedent("""\
            SCHEMA_SQL = \"\"\"
            -- Regular table for projects
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY
            );
            \"\"\"
        """))
        tables = discover_append_only_tables(db_file)
        assert len(tables) == 0

    def test_handles_missing_file(self, tmp_path):
        tables = discover_append_only_tables(tmp_path / "nonexistent.py")
        assert tables == set()

    def test_handles_no_schema_sql(self, tmp_path):
        db_file = tmp_path / "init_db.py"
        db_file.write_text("# empty file\n")
        tables = discover_append_only_tables(db_file)
        assert isinstance(tables, set)

    def test_discovers_real_init_db(self):
        """Integration: verify known tables are found in actual init_icdev_db.py."""
        init_db = Path(__file__).resolve().parent.parent / "tools" / "db" / "init_icdev_db.py"
        if not init_db.exists():
            pytest.skip("init_icdev_db.py not found")
        tables = discover_append_only_tables(init_db)
        assert "audit_trail" in tables
        assert "hook_events" in tables
        assert len(tables) >= 8


# ---------------------------------------------------------------------------
# discover_protected_tables
# ---------------------------------------------------------------------------

class TestDiscoverProtectedTables:
    def test_discovers_from_list_literal(self, tmp_path):
        hook_file = tmp_path / "pre_tool_use.py"
        hook_file.write_text(textwrap.dedent("""\
            APPEND_ONLY_TABLES = [
                "audit_trail",
                "hook_events",
                "custom_log",
            ]
        """))
        tables = discover_protected_tables(hook_file)
        assert tables == {"audit_trail", "hook_events", "custom_log"}

    def test_handles_missing_file(self, tmp_path):
        tables = discover_protected_tables(tmp_path / "missing.py")
        assert tables == set()

    def test_handles_no_list(self, tmp_path):
        hook_file = tmp_path / "pre_tool_use.py"
        hook_file.write_text("# No APPEND_ONLY_TABLES here\nx = 1\n")
        tables = discover_protected_tables(hook_file)
        assert tables == set()


# ---------------------------------------------------------------------------
# check_append_only_table_coverage
# ---------------------------------------------------------------------------

class TestCheckAppendOnlyCoverage:
    def test_full_coverage_passes(self, tmp_path):
        db_file = tmp_path / "init_db.py"
        db_file.write_text(textwrap.dedent("""\
            SCHEMA_SQL = \"\"\"
            -- append-only
            CREATE TABLE IF NOT EXISTS log_a (id INTEGER);
            -- append-only
            CREATE TABLE IF NOT EXISTS log_b (id INTEGER);
            \"\"\"
        """))
        hook_file = tmp_path / "pre_tool_use.py"
        hook_file.write_text('APPEND_ONLY_TABLES = ["log_a", "log_b"]\n')

        result = check_append_only_table_coverage(db_file, hook_file)
        assert result.status == "pass"
        assert result.missing == []

    def test_missing_table_fails(self, tmp_path):
        db_file = tmp_path / "init_db.py"
        db_file.write_text(textwrap.dedent("""\
            SCHEMA_SQL = \"\"\"
            -- append-only
            CREATE TABLE IF NOT EXISTS log_a (id INTEGER);
            -- append-only
            CREATE TABLE IF NOT EXISTS log_b (id INTEGER);
            \"\"\"
        """))
        hook_file = tmp_path / "pre_tool_use.py"
        hook_file.write_text('APPEND_ONLY_TABLES = ["log_a"]\n')

        result = check_append_only_table_coverage(db_file, hook_file)
        assert result.status == "fail"
        assert "log_b" in result.missing

    def test_extra_table_warns(self, tmp_path):
        db_file = tmp_path / "init_db.py"
        db_file.write_text(textwrap.dedent("""\
            SCHEMA_SQL = \"\"\"
            -- append-only
            CREATE TABLE IF NOT EXISTS log_a (id INTEGER);
            \"\"\"
        """))
        hook_file = tmp_path / "pre_tool_use.py"
        hook_file.write_text('APPEND_ONLY_TABLES = ["log_a", "log_extra"]\n')

        result = check_append_only_table_coverage(db_file, hook_file)
        assert result.status == "warn"
        assert "log_extra" in result.extra

    def test_missing_files_handled(self, tmp_path):
        result = check_append_only_table_coverage(
            tmp_path / "missing_db.py", tmp_path / "missing_hook.py"
        )
        assert result.status == "pass"  # empty sets match


# ---------------------------------------------------------------------------
# discover_dashboard_page_routes
# ---------------------------------------------------------------------------

class TestDiscoverDashboardRoutes:
    def test_discovers_routes(self, tmp_path):
        app_file = tmp_path / "app.py"
        app_file.write_text(textwrap.dedent("""\
            @app.route("/")
            def index(): pass

            @app.route("/projects")
            def projects(): pass

            @app.route("/projects/<project_id>")
            def project_detail(): pass

            @app.route("/api/data")
            def api_data(): pass
        """))
        routes = discover_dashboard_page_routes(app_file)
        assert "/" in routes
        assert "/projects" in routes
        assert "/projects/<id>" in routes
        assert "/api/data" not in routes

    def test_excludes_api_routes(self, tmp_path):
        app_file = tmp_path / "app.py"
        app_file.write_text('@app.route("/api/v1/status")\ndef status(): pass\n')
        routes = discover_dashboard_page_routes(app_file)
        assert len(routes) == 0

    def test_normalizes_params(self, tmp_path):
        app_file = tmp_path / "app.py"
        app_file.write_text('@app.route("/items/<item_id>")\ndef item(): pass\n')
        routes = discover_dashboard_page_routes(app_file)
        assert "/items/<id>" in routes

    def test_handles_missing_file(self, tmp_path):
        routes = discover_dashboard_page_routes(tmp_path / "nope.py")
        assert routes == set()


# ---------------------------------------------------------------------------
# discover_documented_routes
# ---------------------------------------------------------------------------

class TestDiscoverDocumentedRoutes:
    def test_extracts_paths(self, tmp_path):
        md_file = tmp_path / "start.md"
        md_file.write_text("Pages: `/`, `/projects`, `/agents`\n")
        routes = discover_documented_routes(md_file)
        assert routes == {"/", "/projects", "/agents"}

    def test_handles_missing_file(self, tmp_path):
        routes = discover_documented_routes(tmp_path / "missing.md")
        assert routes == set()

    def test_handles_empty_file(self, tmp_path):
        md_file = tmp_path / "start.md"
        md_file.write_text("# Start\n\nNo pages listed.\n")
        routes = discover_documented_routes(md_file)
        assert len(routes) == 0


# ---------------------------------------------------------------------------
# check_dashboard_route_documentation
# ---------------------------------------------------------------------------

class TestCheckRouteDocumentation:
    def test_all_documented_passes(self, tmp_path):
        app_file = tmp_path / "app.py"
        app_file.write_text('@app.route("/")\ndef index(): pass\n@app.route("/projects")\ndef projects(): pass\n')
        md_file = tmp_path / "start.md"
        md_file.write_text("Pages: `/`, `/projects`\n")

        result = check_dashboard_route_documentation(app_file, md_file)
        assert result.status == "pass"

    def test_undocumented_warns(self, tmp_path):
        app_file = tmp_path / "app.py"
        app_file.write_text('@app.route("/")\ndef index(): pass\n@app.route("/secret")\ndef secret(): pass\n')
        md_file = tmp_path / "start.md"
        md_file.write_text("Pages: `/`\n")

        result = check_dashboard_route_documentation(app_file, md_file)
        assert result.status == "warn"
        assert "/secret" in result.missing

    def test_extra_documented_passes(self, tmp_path):
        app_file = tmp_path / "app.py"
        app_file.write_text('@app.route("/")\ndef index(): pass\n')
        md_file = tmp_path / "start.md"
        md_file.write_text("Pages: `/`, `/bonus`\n")

        result = check_dashboard_route_documentation(app_file, md_file)
        assert result.status == "pass"


# ---------------------------------------------------------------------------
# check_settings_deny_rules
# ---------------------------------------------------------------------------

class TestCheckSettingsDenyRules:
    def test_all_present_passes(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {
                "deny": [
                    "Bash(git push --force:*)",
                    "Bash(git push -f:*)",
                    "Bash(rm -rf:*)",
                    "Bash(git reset --hard:*)",
                    "Bash(DROP TABLE:*)",
                    "Bash(TRUNCATE:*)",
                ]
            }
        }))
        result = check_settings_deny_rules(settings_file)
        assert result.status == "pass"

    def test_missing_rule_warns(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {"deny": ["Bash(rm -rf:*)"]}
        }))
        result = check_settings_deny_rules(settings_file)
        assert result.status == "warn"
        assert len(result.missing) > 0

    def test_handles_missing_file(self, tmp_path):
        result = check_settings_deny_rules(tmp_path / "missing.json")
        assert result.status == "fail"


# ---------------------------------------------------------------------------
# check_e2e_test_coverage
# ---------------------------------------------------------------------------

class TestCheckE2eCoverage:
    def test_all_covered_passes(self, tmp_path):
        e2e_dir = tmp_path / "e2e"
        e2e_dir.mkdir()
        for name in ["dashboard_health", "agents_monitoring", "activity_usage",
                      "compliance_artifacts", "security_scan", "chat_streams", "saas_portal"]:
            (e2e_dir / f"{name}.md").write_text(f"# {name}")

        result = check_e2e_test_coverage(tmp_path / "app.py", e2e_dir)
        assert result.status == "pass"

    def test_missing_group_warns(self, tmp_path):
        e2e_dir = tmp_path / "e2e"
        e2e_dir.mkdir()
        (e2e_dir / "dashboard_health.md").write_text("# test")

        result = check_e2e_test_coverage(tmp_path / "app.py", e2e_dir)
        assert result.status == "warn"
        assert len(result.missing) > 0

    def test_handles_missing_dir(self, tmp_path):
        result = check_e2e_test_coverage(tmp_path / "app.py", tmp_path / "nope")
        assert result.status == "warn"


# ---------------------------------------------------------------------------
# check_hook_syntax
# ---------------------------------------------------------------------------

class TestCheckHookSyntax:
    def test_valid_hooks_pass(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "good.py").write_text("import json\ndef main(): pass\n")
        (hooks_dir / "also_good.py").write_text("x = 1\n")

        result = check_hook_syntax(hooks_dir)
        assert result.status == "pass"

    def test_syntax_error_fails(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "bad.py").write_text("def broken(\n")

        result = check_hook_syntax(hooks_dir)
        assert result.status == "fail"
        assert "bad.py" in result.message

    def test_handles_missing_dir(self, tmp_path):
        result = check_hook_syntax(tmp_path / "nope")
        assert result.status == "fail"


# ---------------------------------------------------------------------------
# check_settings_hook_references
# ---------------------------------------------------------------------------

class TestCheckHookReferences:
    def test_all_exist_passes(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "pre_tool_use.py").write_text("pass")

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "",
                    "hooks": [{"type": "command", "command": "python .claude/hooks/pre_tool_use.py || true"}]
                }]
            }
        }))
        result = check_settings_hook_references(settings_file, hooks_dir)
        assert result.status == "pass"

    def test_missing_file_fails(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "",
                    "hooks": [{"type": "command", "command": "python .claude/hooks/nonexistent.py || true"}]
                }]
            }
        }))
        result = check_settings_hook_references(settings_file, hooks_dir)
        assert result.status == "fail"
        assert "nonexistent.py" in result.missing

    def test_handles_missing_settings(self, tmp_path):
        result = check_settings_hook_references(tmp_path / "missing.json", tmp_path / "hooks")
        assert result.status == "fail"


# ---------------------------------------------------------------------------
# run_all_checks orchestrator
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    def test_run_all_returns_report(self, tmp_path, monkeypatch):
        # Patch PROJECT_ROOT to use temp dir to avoid depending on real files
        import tools.testing.claude_dir_validator as mod
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)

        # Create minimal structure
        (tmp_path / ".claude" / "hooks").mkdir(parents=True)
        (tmp_path / ".claude" / "commands" / "e2e").mkdir(parents=True)
        (tmp_path / "tools" / "db").mkdir(parents=True)
        (tmp_path / "tools" / "dashboard").mkdir(parents=True)
        (tmp_path / ".claude" / "hooks" / "test.py").write_text("pass")
        (tmp_path / ".claude" / "settings.json").write_text('{"permissions":{"deny":[]},"hooks":{}}')
        (tmp_path / ".claude" / "commands" / "start.md").write_text("# Start\n")
        (tmp_path / "tools" / "db" / "init_icdev_db.py").write_text('SCHEMA_SQL = """"""\n')
        (tmp_path / "tools" / "dashboard" / "app.py").write_text("")

        report = run_all_checks()
        assert isinstance(report, ClaudeConfigReport)
        assert report.total_checks == len(CHECK_REGISTRY)

    def test_run_selected_check(self, tmp_path, monkeypatch):
        import tools.testing.claude_dir_validator as mod
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        (tmp_path / ".claude" / "hooks").mkdir(parents=True)
        (tmp_path / ".claude" / "hooks" / "test.py").write_text("pass")

        report = run_all_checks(selected=["hooks-syntax"])
        assert report.total_checks == 1

    def test_overall_pass_requires_no_fail(self):
        check_pass = ClaudeConfigCheck("a", "A", "pass", [], [], [], [], "ok")
        check_warn = ClaudeConfigCheck("b", "B", "warn", [], [], [], [], "meh")
        report = ClaudeConfigReport(
            overall_pass=True, timestamp="", checks=[check_pass, check_warn],
            total_checks=2, passed_checks=1, failed_checks=0, warned_checks=1,
        )
        assert report.overall_pass is True

    def test_fail_makes_overall_fail(self):
        check_fail = ClaudeConfigCheck("a", "A", "fail", [], [], ["x"], [], "bad")
        report = ClaudeConfigReport(
            overall_pass=False, timestamp="", checks=[check_fail],
            total_checks=1, passed_checks=0, failed_checks=1, warned_checks=0,
        )
        assert report.overall_pass is False


# ---------------------------------------------------------------------------
# format_human
# ---------------------------------------------------------------------------

class TestFormatHuman:
    def test_includes_banner(self):
        report = ClaudeConfigReport(
            overall_pass=True, timestamp="2026-01-01T00:00:00",
            checks=[], total_checks=0, passed_checks=0,
            failed_checks=0, warned_checks=0,
        )
        output = format_human(report)
        assert "Governance Report" in output

    def test_includes_pass_fail(self):
        check = ClaudeConfigCheck("t", "Test", "fail", [], [], ["x"], [], "failed")
        report = ClaudeConfigReport(
            overall_pass=False, timestamp="",
            checks=[check], total_checks=1, passed_checks=0,
            failed_checks=1, warned_checks=0,
        )
        output = format_human(report)
        assert "FAIL" in output
        assert "[FAIL]" in output


# ---------------------------------------------------------------------------
# CHECK_REGISTRY
# ---------------------------------------------------------------------------

class TestCheckRegistry:
    def test_has_all_checks(self):
        expected = {"append-only", "routes", "settings", "e2e", "hooks-syntax", "hooks-refs", "cli-json", "cli-naming", "db-path"}
        assert set(CHECK_REGISTRY.keys()) == expected

    def test_all_callable(self):
        for name, fn in CHECK_REGISTRY.items():
            assert callable(fn), f"{name} is not callable"
