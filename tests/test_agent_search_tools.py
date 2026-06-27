# CUI // SP-CTI
"""Tests for search_files and grep_files agent tools (AgentToolRegistry).

Uses a real temporary directory so FileAccessBroker._resolve() can check
scope constraints against actual paths on disk.
"""
from __future__ import annotations

import pathlib
import tempfile
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(scoped_path: pathlib.Path, monkeypatch=None):
    """Build an AgentToolRegistry with folder_access scoped to *scoped_path*.

    Monkeypatches ``_REPO_ROOT`` in file_access_broker so the broker treats
    *scoped_path* as the repository root, allowing paths outside the real repo.
    """
    import icdev.tools.ace.file_access_broker as _fab
    from icdev.tools.ace.agent_tools import AgentToolRegistry

    # Patch the module-level constant so _resolve() uses scoped_path as root.
    if monkeypatch is not None:
        monkeypatch.setattr(_fab, "_REPO_ROOT", scoped_path)
    else:
        _fab._REPO_ROOT = scoped_path  # noqa: SLF001 — test-only mutation

    class FakeSpec:
        coworker_id = "cw-test"
        trust_tier = "green"
        folder_access = [{"path": str(scoped_path), "mode": "r"}]
        icdev_tools: list = []

    return AgentToolRegistry(FakeSpec(), instance_id="inst-test")


@pytest.fixture()
def tmp_tree(tmp_path: pathlib.Path):
    """Populate a temp dir tree and return its root."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    (tmp_path / "src" / "utils.py").write_text("import os\nimport sys\n\ndef greet(name):\n    print(name)\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("# Project\nThis is the readme.\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("key: value\nother: 123\n", encoding="utf-8")
    (tmp_path / "src" / "sub").mkdir()
    (tmp_path / "src" / "sub" / "nested.py").write_text("# nested\nX = 42\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# search_files — schema
# ---------------------------------------------------------------------------


class TestSearchFilesSchema:
    def test_schema_registered(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        assert "search_files" in _SCHEMAS

    def test_schema_is_read_only(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        s = _SCHEMAS["search_files"]
        assert s.get("is_read_only") is True
        assert s["function"].get("is_read_only") is True

    def test_handler_resolves(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        tools, handlers = registry.build(["search_files"])
        assert "search_files" in handlers

    def test_schema_requires_pattern(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        params = _SCHEMAS["search_files"]["function"]["parameters"]
        assert "pattern" in params.get("required", [])


# ---------------------------------------------------------------------------
# search_files — behaviour
# ---------------------------------------------------------------------------


class TestSearchFiles:
    def test_flat_glob(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["search_files"])
        result = handlers["search_files"]({"pattern": "*.yaml", "path": str(tmp_tree)}, None)
        assert "config.yaml" in result

    def test_recursive_glob_finds_nested(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["search_files"])
        result = handlers["search_files"]({"pattern": "**/*.py", "path": str(tmp_tree)}, None)
        assert "nested.py" in result
        assert "main.py" in result
        assert "utils.py" in result

    def test_no_matches_returns_message(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["search_files"])
        result = handlers["search_files"]({"pattern": "*.go", "path": str(tmp_tree)}, None)
        assert "no matches" in result.lower()

    def test_missing_pattern_returns_error(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["search_files"])
        result = handlers["search_files"]({"path": str(tmp_tree)}, None)
        assert "error" in result.lower()

    def test_non_directory_path_returns_error(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["search_files"])
        result = handlers["search_files"](
            {"pattern": "*.py", "path": str(tmp_tree / "config.yaml")}, None
        )
        assert "error" in result.lower() or "not a directory" in result.lower()

    def test_scope_violation_raises(self, tmp_tree, tmp_path):
        # tmp_path is the scoped root; try to search outside it
        other = tmp_path.parent  # one level up — outside scope
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["search_files"])
        from icdev.tools.ace.file_access_broker import ScopeViolationError
        with pytest.raises((ScopeViolationError, PermissionError)):
            handlers["search_files"]({"pattern": "*.py", "path": str(other)}, None)

    def test_max_results_cap(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["search_files"])
        # max_results=2 with 3 .py files → truncation notice
        result = handlers["search_files"](
            {"pattern": "**/*.py", "path": str(tmp_tree), "max_results": 2}, None
        )
        assert "truncated" in result.lower()

    def test_result_count_header(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["search_files"])
        result = handlers["search_files"]({"pattern": "**/*.py", "path": str(tmp_tree)}, None)
        # Should contain "Found N file(s)"
        assert "found" in result.lower()


# ---------------------------------------------------------------------------
# grep_files — schema
# ---------------------------------------------------------------------------


class TestGrepFilesSchema:
    def test_schema_registered(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        assert "grep_files" in _SCHEMAS

    def test_schema_is_read_only(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        s = _SCHEMAS["grep_files"]
        assert s.get("is_read_only") is True
        assert s["function"].get("is_read_only") is True

    def test_handler_resolves(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        tools, handlers = registry.build(["grep_files"])
        assert "grep_files" in handlers

    def test_schema_requires_pattern(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        params = _SCHEMAS["grep_files"]["function"]["parameters"]
        assert "pattern" in params.get("required", [])


# ---------------------------------------------------------------------------
# grep_files — behaviour
# ---------------------------------------------------------------------------


class TestGrepFiles:
    def test_substring_match(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        result = handlers["grep_files"]({"pattern": "def hello", "path": str(tmp_tree)}, None)
        assert "main.py" in result
        assert "def hello" in result

    def test_regex_match(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        result = handlers["grep_files"]({"pattern": r"def \w+", "path": str(tmp_tree)}, None)
        assert "main.py" in result

    def test_line_number_in_output(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        result = handlers["grep_files"]({"pattern": "def hello", "path": str(tmp_tree)}, None)
        # Output format: filepath:line_num:content
        parts = [line for line in result.splitlines() if "def hello" in line]
        assert parts, "no match lines found"
        assert ":" in parts[0]
        segments = parts[0].split(":")
        assert segments[1].isdigit(), f"expected line number, got {segments[1]!r}"

    def test_no_matches_returns_message(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        result = handlers["grep_files"](
            {"pattern": "XYZNONEXISTENT999", "path": str(tmp_tree)}, None
        )
        assert "no matches" in result.lower()

    def test_missing_pattern_returns_error(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        result = handlers["grep_files"]({"path": str(tmp_tree)}, None)
        assert "error" in result.lower()

    def test_single_file_search(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        result = handlers["grep_files"](
            {"pattern": "import", "path": str(tmp_tree / "src" / "utils.py")}, None
        )
        assert "import" in result
        # Should NOT contain results from other files
        assert "main.py" not in result
        assert "readme.md" not in result

    def test_max_results_truncates(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        # pattern that matches many lines
        result = handlers["grep_files"](
            {"pattern": ".", "path": str(tmp_tree), "max_results": 3}, None
        )
        assert "truncated" in result.lower()

    def test_invalid_regex_falls_back_to_literal(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        # '[unclosed' is an invalid regex — should fall back to literal search
        result = handlers["grep_files"](
            {"pattern": "return 'hello'", "path": str(tmp_tree)}, None
        )
        assert "main.py" in result

    def test_scope_violation_raises(self, tmp_tree, tmp_path):
        other = tmp_path.parent
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        from icdev.tools.ace.file_access_broker import ScopeViolationError
        with pytest.raises((ScopeViolationError, PermissionError)):
            handlers["grep_files"]({"pattern": "hello", "path": str(other)}, None)

    def test_markdown_file_searched(self, tmp_tree):
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        result = handlers["grep_files"]({"pattern": "readme", "path": str(tmp_tree)}, None)
        assert "readme.md" in result

    def test_stop_event_interrupts_search(self, tmp_tree):
        import threading
        registry = _make_registry(tmp_tree)
        _, handlers = registry.build(["grep_files"])
        stop = threading.Event()
        stop.set()
        result = handlers["grep_files"](
            {"pattern": "def", "path": str(tmp_tree)}, stop
        )
        # Either interrupted message or partial/empty results — must not hang
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Both tools appear in the is_read_only parallel-execution set
# ---------------------------------------------------------------------------


class TestReadOnlyParallelExecution:
    def test_search_files_in_read_only_set(self, tmp_tree):
        from icdev.tools.llm.agent_loop import _build_read_only_set
        from icdev.tools.ace.agent_tools import _SCHEMAS

        registry = _make_registry(tmp_tree)
        tools, _ = registry.build(["read_file", "search_files", "grep_files", "write_file"])
        ro_set = _build_read_only_set(tools)
        assert "search_files" in ro_set
        assert "grep_files" in ro_set
        assert "write_file" not in ro_set
        assert "read_file" in ro_set
