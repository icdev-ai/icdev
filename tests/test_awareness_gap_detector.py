# CUI // SP-CTI
"""Phase 3 unit tests for tools/awareness/gap_detector.py.

Covers each rule in isolation, the registry/orchestrator, and the
default-off behavior of stale_code.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.awareness import gap_detector as gd  # noqa: E402


# ---------------------------------------------------------------------------
# Rule registry + defaults
# ---------------------------------------------------------------------------


class TestGapRulesConfig:
    def test_seven_rules_enabled_by_default(self):
        enabled = [rid for rid, cfg in gd.GAP_RULES.items() if cfg["enabled"]]
        assert len(enabled) == 7
        assert "route_not_listed" in enabled
        assert "tool_not_in_manifest" in enabled
        assert "skill_references_missing_goal" in enabled
        assert "orphan_db_table" in enabled
        assert "broken_test_reference" in enabled
        assert "route_no_e2e" in enabled
        assert "empty_mcp_server" in enabled

    def test_stale_code_is_default_off(self):
        assert gd.GAP_RULES["stale_code"]["enabled"] is False

    def test_every_rule_has_func_in_registry(self):
        for rid in gd.GAP_RULES:
            assert rid in gd._RULE_FUNCS, f"{rid} missing from _RULE_FUNCS"

    def test_finding_shape(self):
        f = gd._finding("test_rule", "subject-1", {"k": "v"})
        assert f["rule_id"] == "test_rule"
        assert f["subject"] == "subject-1"
        assert f["evidence"] == {"k": "v"}


# ---------------------------------------------------------------------------
# Rule: route_not_listed
# ---------------------------------------------------------------------------


class TestRouteNotListed:
    # ------------------------------------------------------------------
    # Helpers used across tests
    # ------------------------------------------------------------------

    @staticmethod
    def _write_fixtures(tmp_path, app_src: str, pages_line: str) -> None:
        (tmp_path / "tools" / "dashboard").mkdir(parents=True)
        (tmp_path / "tools" / "dashboard" / "app.py").write_text(
            app_src, encoding="utf-8"
        )
        (tmp_path / ".claude" / "commands").mkdir(parents=True)
        (tmp_path / ".claude" / "commands" / "start.md").write_text(
            f"Pages: {pages_line}\n\n", encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Core page / API distinction
    # ------------------------------------------------------------------

    def test_render_template_handler_flagged_when_missing(self, tmp_path, monkeypatch):
        """Handler with render_template return → PAGE route → flagged if absent."""
        self._write_fixtures(
            tmp_path,
            app_src=(
                '@app.route("/new-page")\n'
                "def new_page():\n"
                '    return render_template("new_page.html")\n'
                '\n'
                '@app.route("/already-listed")\n'
                "def already_listed():\n"
                '    return render_template("already_listed.html")\n'
            ),
            pages_line='`/already-listed`, `/other`',
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        subjects = {f["subject"] for f in findings}
        assert "/new-page" in subjects
        assert "/already-listed" not in subjects

    def test_jsonify_handler_never_flagged(self, tmp_path, monkeypatch):
        """Handler that returns jsonify() → API endpoint → never flagged."""
        self._write_fixtures(
            tmp_path,
            app_src=(
                '@app.route("/data")\n'
                "def data():\n"
                '    return jsonify({"ok": True})\n'
            ),
            pages_line='`/home`',
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        assert not findings, f"API-only handler should never be flagged; got {findings}"

    def test_api_prefix_routes_always_skipped(self, tmp_path, monkeypatch):
        """Routes starting with /api/ are skipped regardless of handler body."""
        self._write_fixtures(
            tmp_path,
            app_src=(
                '@app.route("/api/unlisted-api")\n'
                "def unlisted_api():\n"
                '    return render_template("unlikely.html")\n'
            ),
            pages_line='`/home`',
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        assert all(not f["subject"].startswith("/api/") for f in findings)

    def test_ambiguous_handler_not_flagged(self, tmp_path, monkeypatch):
        """Handler with no render_template / jsonify return (ambiguous) is skipped."""
        self._write_fixtures(
            tmp_path,
            app_src=(
                '@app.route("/mystery")\n'
                "def mystery():\n"
                "    pass\n"
            ),
            pages_line='`/home`',
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        subjects = {f["subject"] for f in findings}
        assert "/mystery" not in subjects

    # ------------------------------------------------------------------
    # Mixed handler (both render_template and jsonify branches)
    # ------------------------------------------------------------------

    def test_mixed_handler_flagged_as_page(self, tmp_path, monkeypatch):
        """Handler with both render_template and jsonify branches → page wins."""
        self._write_fixtures(
            tmp_path,
            app_src=(
                '@app.route("/mixed")\n'
                "def mixed():\n"
                "    if some_condition:\n"
                '        return jsonify({"error": "unauthorized"}), 401\n'
                '    return render_template("mixed.html")\n'
            ),
            pages_line='`/home`',
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        subjects = {f["subject"] for f in findings}
        assert "/mixed" in subjects, (
            "Mixed handler (render_template + jsonify) should be flagged as page"
        )

    def test_mixed_handler_listed_not_flagged(self, tmp_path, monkeypatch):
        """Mixed handler already listed in Pages: line → not flagged."""
        self._write_fixtures(
            tmp_path,
            app_src=(
                '@app.route("/mixed")\n'
                "def mixed():\n"
                "    if err:\n"
                '        return jsonify({"error": "x"}), 500\n'
                '    return render_template("mixed.html")\n'
            ),
            pages_line='`/mixed`',
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        assert not findings

    # ------------------------------------------------------------------
    # Blueprint-registered routes
    # ------------------------------------------------------------------

    def test_blueprint_routes_are_inspected(self, tmp_path, monkeypatch):
        """Routes defined in blueprint files are classified and checked."""
        (tmp_path / "tools" / "dashboard").mkdir(parents=True)
        # app.py has no routes of its own
        (tmp_path / "tools" / "dashboard" / "app.py").write_text(
            "# no routes\n", encoding="utf-8"
        )
        # blueprint file registers a page route
        (tmp_path / "tools" / "dashboard" / "bp_reports.py").write_text(
            '@reports_bp.route("/reports")\n'
            "def reports():\n"
            '    return render_template("reports.html")\n',
            encoding="utf-8",
        )
        (tmp_path / ".claude" / "commands").mkdir(parents=True)
        (tmp_path / ".claude" / "commands" / "start.md").write_text(
            "Pages: `/home`\n\n", encoding="utf-8"
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        subjects = {f["subject"] for f in findings}
        assert "/reports" in subjects

    def test_blueprint_api_route_skipped(self, tmp_path, monkeypatch):
        """API-only blueprint routes are not flagged."""
        (tmp_path / "tools" / "dashboard").mkdir(parents=True)
        (tmp_path / "tools" / "dashboard" / "app.py").write_text(
            "# no routes\n", encoding="utf-8"
        )
        (tmp_path / "tools" / "dashboard" / "bp_api.py").write_text(
            '@api_bp.route("/api/v2/data")\n'
            "def api_data():\n"
            '    return jsonify({"data": []})\n',
            encoding="utf-8",
        )
        (tmp_path / ".claude" / "commands").mkdir(parents=True)
        (tmp_path / ".claude" / "commands" / "start.md").write_text(
            "Pages: `/home`\n\n", encoding="utf-8"
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        assert not findings

    # ------------------------------------------------------------------
    # Tuple-wrapped returns: return render_template(...), 200
    # ------------------------------------------------------------------

    def test_tuple_return_render_template_classified_as_page(
        self, tmp_path, monkeypatch
    ):
        """``return render_template(...), 200`` must still classify as page."""
        self._write_fixtures(
            tmp_path,
            app_src=(
                '@app.route("/status")\n'
                "def status():\n"
                '    return render_template("status.html"), 200\n'
            ),
            pages_line='`/home`',
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        subjects = {f["subject"] for f in findings}
        assert "/status" in subjects

    # ------------------------------------------------------------------
    # Nested inner function does not pollute classification
    # ------------------------------------------------------------------

    def test_inner_function_jsonify_does_not_override_page(
        self, tmp_path, monkeypatch
    ):
        """A jsonify() inside an inner helper closure must not flip the
        outer handler's classification from 'page' to 'api'."""
        self._write_fixtures(
            tmp_path,
            app_src=(
                '@app.route("/outer")\n'
                "def outer():\n"
                "    def _error_json(msg):\n"
                '        return jsonify({"error": msg})\n'
                '    return render_template("outer.html")\n'
            ),
            pages_line='`/home`',
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_not_listed()
        subjects = {f["subject"] for f in findings}
        assert "/outer" in subjects, (
            "render_template in outer handler must win over jsonify in inner closure"
        )


# ---------------------------------------------------------------------------
# Rule: tool_not_in_manifest
# ---------------------------------------------------------------------------


class TestToolNotInManifest:
    def test_flags_undocumented_tool(self, tmp_path, monkeypatch):
        # manifest mentions only foo.py
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "manifest.md").write_text(
            "| Foo | tools/foo.py | does foo | - | - |\n",
            encoding="utf-8",
        )
        (tmp_path / "tools" / "foo.py").write_text("pass\n", encoding="utf-8")
        (tmp_path / "tools" / "bar.py").write_text("pass\n", encoding="utf-8")
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_tool_not_in_manifest()
        subjects = {f["subject"] for f in findings}
        assert "tools/bar.py" in subjects
        assert "tools/foo.py" not in subjects

    def test_private_helpers_skipped(self, tmp_path, monkeypatch):
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "manifest.md").write_text("", encoding="utf-8")
        (tmp_path / "tools" / "_helper.py").write_text("pass\n", encoding="utf-8")
        (tmp_path / "tools" / "__init__.py").write_text("", encoding="utf-8")
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_tool_not_in_manifest()
        assert not findings


# ---------------------------------------------------------------------------
# Rule: skill_references_missing_goal
# ---------------------------------------------------------------------------


class TestSkillReferencesMissingGoal:
    def test_flags_missing_goal_reference(self, tmp_path, monkeypatch):
        (tmp_path / ".agents" / "skills" / "demo").mkdir(parents=True)
        (tmp_path / ".agents" / "skills" / "demo" / "SKILL.md").write_text(
            "Run per goals/demo_goal.md and goals/missing_goal.md\n",
            encoding="utf-8",
        )
        (tmp_path / "goals").mkdir()
        (tmp_path / "goals" / "demo_goal.md").write_text("# demo\n", encoding="utf-8")
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_skill_references_missing_goal()
        assert len(findings) == 1
        assert findings[0]["evidence"]["missing_goal"] == "goals/missing_goal.md"


# ---------------------------------------------------------------------------
# Rule: orphan_db_table
# ---------------------------------------------------------------------------


class TestOrphanDbTable:
    def test_flags_table_referenced_not_created(self, tmp_path, monkeypatch):
        (tmp_path / "tools" / "foo").mkdir(parents=True)
        (tmp_path / "tools" / "foo" / "writer.py").write_text(
            'conn.execute("INSERT INTO orphan_table (id) VALUES (1)")\n'
            'conn.execute("CREATE TABLE existing_table (id INT)")\n'
            'conn.execute("INSERT INTO existing_table (id) VALUES (2)")\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_orphan_db_table()
        tables = {f["subject"] for f in findings}
        assert "orphan_table" in tables
        assert "existing_table" not in tables

    def test_ignores_python_from_import_statements(self, tmp_path, monkeypatch):
        """Regression for 2026-04-11: the pre-fix detector ran the FROM
        regex over raw file text and matched Python ``from tools.rag
        import X`` as if ``tools`` were a SQL table. The fix scans
        string literals only."""
        (tmp_path / "tools" / "foo").mkdir(parents=True)
        (tmp_path / "tools" / "foo" / "user.py").write_text(
            "from tools.rag import codebase_indexer\n"
            "from __future__ import annotations\n"
            "import typing\n"
            "# no SQL in this file at all\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_orphan_db_table()
        # None of these Python import words should appear as tables.
        tables = {f["subject"] for f in findings}
        for bad in ("tools", "__future__", "typing", "annotations"):
            assert bad not in tables, f"{bad} leaked as orphan table"

    def test_ignores_docstrings_starting_with_create_word(
        self, tmp_path, monkeypatch
    ):
        """Docstrings like ``Create a vector store instance based on
        config...`` used to pass the substring-based SQL heuristic and
        produce garbage orphan entries. The strict start-anchored
        _is_likely_sql must reject them."""
        (tmp_path / "tools" / "foo").mkdir(parents=True)
        (tmp_path / "tools" / "foo" / "helper.py").write_text(
            '''
def build():
    """Create a vector store instance based on config.

    Args:
        backend: Override backend selection.
        config: Optional config dict (loads from args/rag_config.yaml if None).
    """
    pass
''',
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_orphan_db_table()
        tables = {f["subject"] for f in findings}
        for bad in ("args", "config", "store", "instance", "based"):
            assert bad not in tables

    def test_ignores_insert_into_english_prose(self, tmp_path, monkeypatch):
        """The docstring ``"Insert into append-only proposal_status_history."``
        must not produce ``append`` as an orphan table. The INSERT regex
        requires a SQL-valid continuation (``(``, ``VALUES``, ``SELECT``,
        ``DEFAULT VALUES``) after the table name."""
        (tmp_path / "tools" / "foo").mkdir(parents=True)
        (tmp_path / "tools" / "foo" / "proposals.py").write_text(
            '''
def record():
    """Insert into append-only proposal_status_history."""
    pass
''',
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_orphan_db_table()
        tables = {f["subject"] for f in findings}
        assert "append" not in tables
        assert "proposal_status_history" not in tables

    def test_ignores_sql_line_comments(self, tmp_path, monkeypatch):
        """A SQL CREATE TABLE string containing a -- line comment that
        happens to include the word FROM must not produce an orphan
        for the comment text. Real SQL only."""
        (tmp_path / "tools" / "foo").mkdir(parents=True)
        (tmp_path / "tools" / "foo" / "schema.py").write_text(
            '''
SCHEMA = """
CREATE TABLE research_signals (  -- discovered from all 8 data streams
    id TEXT PRIMARY KEY,
    name TEXT
);
"""
''',
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_orphan_db_table()
        tables = {f["subject"] for f in findings}
        assert "all" not in tables
        assert "research_signals" not in tables  # it has a CREATE TABLE
        assert "discovered" not in tables

    def test_ignores_information_schema(self, tmp_path, monkeypatch):
        """Postgres system namespaces are never defined in tool code
        and must not appear in the orphan list."""
        (tmp_path / "tools" / "foo").mkdir(parents=True)
        (tmp_path / "tools" / "foo" / "pg.py").write_text(
            'conn.execute("SELECT COUNT(*) FROM information_schema.tables")\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_orphan_db_table()
        tables = {f["subject"] for f in findings}
        assert "information_schema" not in tables
        assert "pg_catalog" not in tables


class TestIsLikelySql:
    """Unit tests for the start-anchored SQL-shape detector. Guards
    against docstring / template-string false positives."""

    def test_accepts_real_sql(self):
        from tools.awareness.gap_detector import _is_likely_sql
        assert _is_likely_sql("CREATE TABLE foo (id TEXT)")
        assert _is_likely_sql("SELECT * FROM bar WHERE x = 1")
        assert _is_likely_sql("SELECT col1, col2 FROM bar")
        assert _is_likely_sql("SELECT COUNT(*) FROM bar")
        assert _is_likely_sql("INSERT INTO bar VALUES (?)")
        assert _is_likely_sql("UPDATE foo SET x = 1 WHERE id = ?")
        assert _is_likely_sql("DELETE FROM foo WHERE id = ?")
        assert _is_likely_sql("ALTER TABLE foo ADD COLUMN x TEXT")
        assert _is_likely_sql("DROP TABLE IF EXISTS foo")
        assert _is_likely_sql("WITH recent AS (SELECT * FROM foo) SELECT * FROM recent")

    def test_accepts_sql_with_leading_comments(self):
        from tools.awareness.gap_detector import _is_likely_sql
        assert _is_likely_sql("-- comment line\nCREATE TABLE foo (id TEXT)")
        assert _is_likely_sql("-- header\n-- more\nSELECT * FROM bar")

    def test_rejects_english_prose(self):
        from tools.awareness.gap_detector import _is_likely_sql
        assert not _is_likely_sql("Create a vector store instance based on config.")
        assert not _is_likely_sql("Select an option from the list to continue.")
        assert not _is_likely_sql("loads from args/rag_config.yaml if None")
        assert not _is_likely_sql("The function runs SELECT queries against the DB")
        assert not _is_likely_sql("from __future__ import annotations")

    def test_rejects_short_strings(self):
        from tools.awareness.gap_detector import _is_likely_sql
        assert not _is_likely_sql("")
        assert not _is_likely_sql("SELECT 1")  # too short
        assert not _is_likely_sql(None or "")


# ---------------------------------------------------------------------------
# Rule: broken_test_reference
# ---------------------------------------------------------------------------


class TestBrokenTestReference:
    def test_flags_missing_symbol(self, tmp_path, monkeypatch):
        # Create a real tools module + a test that imports a nonexistent name
        (tmp_path / "tools" / "pkg").mkdir(parents=True)
        (tmp_path / "tools" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "tools" / "pkg" / "real.py").write_text(
            "def real_func():\n    return 1\n",
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            "from tools.pkg.real import real_func, missing_func\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_broken_test_reference()
        missing = [f for f in findings if f["evidence"]["symbol"] == "missing_func"]
        assert missing, f"expected missing_func to be flagged, got {findings}"

    def test_does_not_flag_valid_imports(self, tmp_path, monkeypatch):
        (tmp_path / "tools" / "pkg").mkdir(parents=True)
        (tmp_path / "tools" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "tools" / "pkg" / "real.py").write_text(
            "def real_func():\n    return 1\n",
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            "from tools.pkg.real import real_func\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_broken_test_reference()
        # No broken reference
        assert all(f["evidence"]["symbol"] != "real_func" for f in findings)

    def test_does_not_flag_submodule_import(self, tmp_path, monkeypatch):
        """Regression for 2026-04-11: Python lets you write
        ``from tools.pkg import submod`` even when ``submod`` is not
        listed in ``tools/pkg/__init__.py``, because Python resolves
        submodules by filesystem lookup. The pre-fix detector flagged
        every such import as ``symbol not exported from module``."""
        (tmp_path / "tools" / "pkg").mkdir(parents=True)
        (tmp_path / "tools" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "tools" / "pkg" / "submod.py").write_text(
            "def work():\n    return 1\n",
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_sub.py").write_text(
            "from tools.pkg import submod\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_broken_test_reference()
        assert all(
            f["evidence"]["symbol"] != "submod" for f in findings
        ), f"submodule import flagged as broken: {findings}"

    def test_does_not_flag_nested_subpackage(self, tmp_path, monkeypatch):
        """``from tools.pkg import subpkg`` where ``subpkg`` is itself a
        package with its own ``__init__.py`` must also resolve."""
        (tmp_path / "tools" / "pkg" / "subpkg").mkdir(parents=True)
        (tmp_path / "tools" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "tools" / "pkg" / "subpkg" / "__init__.py").write_text(
            "def sub_work():\n    return 1\n",
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_sub.py").write_text(
            "from tools.pkg import subpkg\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_broken_test_reference()
        assert all(
            f["evidence"]["symbol"] != "subpkg" for f in findings
        )

    def test_does_not_flag_reexport_from_init(self, tmp_path, monkeypatch):
        """``__init__.py`` re-exports via ``from .sub import Name``
        must count as "defined in module" for tests that import the
        name from the package level."""
        (tmp_path / "tools" / "pkg").mkdir(parents=True)
        (tmp_path / "tools" / "pkg" / "__init__.py").write_text(
            "from .inner import exported_name\n",
            encoding="utf-8",
        )
        (tmp_path / "tools" / "pkg" / "inner.py").write_text(
            "exported_name = 'hello'\n",
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_ex.py").write_text(
            "from tools.pkg import exported_name\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_broken_test_reference()
        assert all(
            f["evidence"]["symbol"] != "exported_name" for f in findings
        )

    def test_does_not_flag_conditional_top_level_binding(
        self, tmp_path, monkeypatch
    ):
        """``try/except ImportError`` with a fallback stub at module
        top level must be recognized. This is the pattern that
        conceals ``audit_log_event`` in ``tools/builder/app_blueprint.py``
        and produced 12 stale predictions before the fix landed."""
        (tmp_path / "tools" / "pkg").mkdir(parents=True)
        (tmp_path / "tools" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "tools" / "pkg" / "guarded.py").write_text(
            '''
try:
    from tools.other.audit import log_event as audit_log_event
except ImportError:

    def audit_log_event(**kwargs):
        pass
''',
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_guarded.py").write_text(
            "from tools.pkg.guarded import audit_log_event\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_broken_test_reference()
        assert all(
            f["evidence"]["symbol"] != "audit_log_event" for f in findings
        )


# ---------------------------------------------------------------------------
# Rule: route_no_e2e
# ---------------------------------------------------------------------------


class TestRouteNoE2e:
    def test_flags_route_with_no_e2e_reference(self, tmp_path, monkeypatch):
        (tmp_path / "tools" / "dashboard").mkdir(parents=True)
        (tmp_path / "tools" / "dashboard" / "app.py").write_text(
            '@app.route("/covered-page")\ndef a(): pass\n'
            '@app.route("/uncovered-page")\ndef b(): pass\n',
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "e2e_demo.py").write_text(
            'driver.get("http://localhost/covered-page")\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_route_no_e2e()
        subjects = {f["subject"] for f in findings}
        assert "/uncovered-page" in subjects
        assert "/covered-page" not in subjects


# ---------------------------------------------------------------------------
# Rule: empty_mcp_server
# ---------------------------------------------------------------------------


class TestEmptyMcpServer:
    def test_flags_empty_server_only(self, tmp_path, monkeypatch):
        (tmp_path / "tools" / "mcp").mkdir(parents=True)
        (tmp_path / "tools" / "mcp" / "full_server.py").write_text(
            '"""Full server."""\n'
            'class FullServer:\n'
            '    @mcp_tool\n'
            '    def t1(self): pass\n'
            '    @mcp_tool\n'
            '    def t2(self): pass\n',
            encoding="utf-8",
        )
        (tmp_path / "tools" / "mcp" / "empty_server.py").write_text(
            '"""Empty server with no tools."""\n'
            'class EmptyServer:\n'
            '    def helper(self): pass\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(gd, "BASE_DIR", tmp_path)
        findings = gd._rule_empty_mcp_server()
        subjects = {f["subject"] for f in findings}
        assert "tools/mcp/empty_server.py" in subjects
        assert "tools/mcp/full_server.py" not in subjects


# ---------------------------------------------------------------------------
# Detect orchestrator
# ---------------------------------------------------------------------------


class TestDetectOrchestrator:
    """Patches target the _RULE_FUNCS registry dict rather than
    module-level attribute names, because the orchestrator looks
    rules up via the dict captured at import time."""

    def test_dry_run_writes_nothing(self):
        stub_registry = {
            "route_not_listed": lambda: [gd._finding("route_not_listed", "/x", {})]
        }
        with patch.dict(gd._RULE_FUNCS, stub_registry, clear=True):
            result = gd.detect(rules=["route_not_listed"], dry_run=True)
        assert result["dry_run"] is True
        assert result["total_findings"] == 1
        assert result["total_written"] == 0

    def test_default_rules_exclude_stale_code(self):
        called: list = []

        def stub(rid):
            def _inner():
                called.append(rid)
                return []
            return _inner

        stub_registry = {rid: stub(rid) for rid in gd._RULE_FUNCS}
        with patch.dict(gd._RULE_FUNCS, stub_registry, clear=True):
            gd.detect(dry_run=True)

        # stale_code is default-off
        assert "stale_code" not in called
        assert "route_not_listed" in called
        assert "empty_mcp_server" in called

    def test_include_disabled_runs_stale_code(self):
        called: list = []

        def stub(rid):
            def _inner():
                called.append(rid)
                return []
            return _inner

        stub_registry = {rid: stub(rid) for rid in gd._RULE_FUNCS}
        with patch.dict(gd._RULE_FUNCS, stub_registry, clear=True):
            gd.detect(dry_run=True, include_disabled=True)
        assert "stale_code" in called

    def test_rule_filter_runs_only_one(self):
        calls = {"route_not_listed": 0, "tool_not_in_manifest": 0}

        def s_route():
            calls["route_not_listed"] += 1
            return []

        def s_tool():
            calls["tool_not_in_manifest"] += 1
            return []

        stub_registry = {
            "route_not_listed": s_route,
            "tool_not_in_manifest": s_tool,
        }
        with patch.dict(gd._RULE_FUNCS, stub_registry, clear=True):
            gd.detect(rules=["route_not_listed"], dry_run=True)
        assert calls["route_not_listed"] == 1
        assert calls["tool_not_in_manifest"] == 0

    def test_rule_exception_does_not_crash_loop(self):
        def _boom():
            raise RuntimeError("kaboom")

        stub_registry = {
            "route_not_listed": _boom,
            "empty_mcp_server": lambda: [],
        }
        with patch.dict(gd._RULE_FUNCS, stub_registry, clear=True):
            result = gd.detect(
                rules=["route_not_listed", "empty_mcp_server"],
                dry_run=True,
            )
        assert "error" in result["by_rule"]["route_not_listed"]
        assert "findings" in result["by_rule"]["empty_mcp_server"]


# ---------------------------------------------------------------------------
# Prediction ID determinism
# ---------------------------------------------------------------------------


class TestPredictionId:
    def test_deterministic(self):
        """Stable prediction ID: same (rule, subject) → same ID, no timestamp."""
        a = gd._prediction_id("r1", "subject-a")
        b = gd._prediction_id("r1", "subject-a")
        c = gd._prediction_id("r1", "subject-b")
        assert a == b
        assert a != c
        assert a.startswith("op-gap-")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ---------------------------------------------------------------------------
# Prediction write / refresh — the tombstone fix
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Records execute() calls and simulates one existing prediction row.

    Confidence/severity are per-rule constants; the row keyed by (rule, subject)
    must track config changes while it is still open, and must never be rewritten
    once promoted or dismissed.
    """

    def __init__(self, existing=None):
        self.existing = existing  # dict with outcome/confidence/severity, or None
        self.selects = 0
        self.updates = []
        self.inserts = []
        self.commits = 0

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT outcome, confidence, severity"):
            self.selects += 1
            return _FakeCursor(dict(self.existing) if self.existing else None)
        if s.startswith("UPDATE oracle_predictions"):
            self.updates.append({"sql": s, "params": params})
            return _FakeCursor(None)
        if s.startswith("INSERT INTO oracle_predictions"):
            self.inserts.append({"sql": s, "params": params})
            return _FakeCursor(None)
        return _FakeCursor(None)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


_RULE_CFG = {"confidence": 0.90, "severity": "medium", "description": "desc"}


def _finding_for():
    return gd._finding("broken_test_reference", "some_subject", {"k": "v"})


class TestPredictionRefresh:
    def test_absent_row_inserts(self):
        conn = _FakeConn(existing=None)
        pid = gd._write_gap_prediction(conn, _finding_for(), _RULE_CFG)
        assert pid is not None
        assert len(conn.inserts) == 1 and len(conn.updates) == 0
        # confidence written is the rule constant
        assert 0.90 in conn.inserts[0]["params"]

    def test_open_row_same_confidence_is_noop(self):
        conn = _FakeConn(existing={"outcome": "pending", "confidence": 0.90, "severity": "medium"})
        gd._write_gap_prediction(conn, _finding_for(), _RULE_CFG)
        assert conn.inserts == [] and conn.updates == []

    def test_open_row_stale_confidence_is_refreshed(self):
        """The tombstone: a subject first seen at 0.50 must not stay pinned there
        after the rule's constant is raised to 0.90."""
        conn = _FakeConn(existing={"outcome": "pending", "confidence": 0.50, "severity": "low"})
        gd._write_gap_prediction(conn, _finding_for(), _RULE_CFG)
        assert conn.inserts == []
        assert len(conn.updates) == 1
        assert 0.90 in conn.updates[0]["params"]
        assert "medium" in conn.updates[0]["params"]

    def test_refresh_does_not_reset_created_at(self):
        conn = _FakeConn(existing={"outcome": "pending", "confidence": 0.50, "severity": "medium"})
        gd._write_gap_prediction(conn, _finding_for(), _RULE_CFG)
        assert "created_at" not in conn.updates[0]["sql"]

    def test_promoted_row_is_never_rewritten(self):
        """A kanban card already exists — rewriting is the churn the guard stops."""
        conn = _FakeConn(existing={"outcome": "promoted:task-abc", "confidence": 0.50, "severity": "low"})
        gd._write_gap_prediction(conn, _finding_for(), _RULE_CFG)
        assert conn.inserts == [] and conn.updates == []

    def test_dismissed_row_is_never_rewritten(self):
        """The operator ruled; respect the signal even though the constant differs."""
        conn = _FakeConn(existing={"outcome": "dismissed", "confidence": 0.50, "severity": "low"})
        gd._write_gap_prediction(conn, _finding_for(), _RULE_CFG)
        assert conn.inserts == [] and conn.updates == []

    def test_empty_outcome_counts_as_open(self):
        conn = _FakeConn(existing={"outcome": "", "confidence": 0.50, "severity": "medium"})
        gd._write_gap_prediction(conn, _finding_for(), _RULE_CFG)
        assert len(conn.updates) == 1
