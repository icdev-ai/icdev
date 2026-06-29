# CUI // SP-CTI
"""Tests for the patch_file agent tool (AgentToolRegistry).

Uses a real temporary directory so FileAccessBroker._resolve() enforces
scope constraints against actual paths on disk.
"""
from __future__ import annotations

import pathlib

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rw_registry(scoped_path: pathlib.Path, monkeypatch):
    """Build an AgentToolRegistry with RW folder_access scoped to *scoped_path*."""
    import icdev.tools.ace.file_access_broker as _fab
    from icdev.tools.ace.agent_tools import AgentToolRegistry

    monkeypatch.setattr(_fab, "_REPO_ROOT", scoped_path)

    class FakeSpec:
        coworker_id = "cw-patch-test"
        trust_tier = "green"
        folder_access = [{"path": str(scoped_path), "mode": "rw"}]
        icdev_tools: list = []

    return AgentToolRegistry(FakeSpec(), instance_id="inst-patch")


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestPatchFileSchema:
    def test_patch_file_schema_registered(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        assert "patch_file" in _SCHEMAS

    def test_patch_file_schema_not_read_only(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        schema = _SCHEMAS["patch_file"]
        fn = schema.get("function", {})
        assert fn.get("is_read_only") is False

    def test_patch_file_schema_required_params(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        params = _SCHEMAS["patch_file"]["function"]["parameters"]
        required = set(params.get("required", []))
        assert {"path", "old_string", "new_string"}.issubset(required)


# ---------------------------------------------------------------------------
# Behaviour tests
# ---------------------------------------------------------------------------


class TestPatchFileBehaviour:
    def test_patch_replaces_unique_substring(self, tmp_path, monkeypatch):
        reg = _make_rw_registry(tmp_path, monkeypatch)
        f = tmp_path / "hello.py"
        f.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
        _, handlers = reg.build(["patch_file"])
        result = handlers["patch_file"](
            {"path": str(f), "old_string": "return 'hello'", "new_string": "return 'world'"},
            None,
        )
        assert "Patched" in result
        assert f.read_text(encoding="utf-8") == "def greet():\n    return 'world'\n"

    def test_patch_reports_net_lines(self, tmp_path, monkeypatch):
        reg = _make_rw_registry(tmp_path, monkeypatch)
        f = tmp_path / "multi.py"
        f.write_text("a\nb\nc\n", encoding="utf-8")
        _, handlers = reg.build(["patch_file"])
        result = handlers["patch_file"](
            {"path": str(f), "old_string": "a\nb", "new_string": "x\ny\nz"},
            None,
        )
        assert "net +1" in result

    def test_patch_error_old_string_not_found(self, tmp_path, monkeypatch):
        reg = _make_rw_registry(tmp_path, monkeypatch)
        f = tmp_path / "nope.py"
        f.write_text("nothing here\n", encoding="utf-8")
        _, handlers = reg.build(["patch_file"])
        result = handlers["patch_file"](
            {"path": str(f), "old_string": "MISSING", "new_string": "x"},
            None,
        )
        assert result.startswith("error:") and "not found" in result

    def test_patch_error_old_string_ambiguous(self, tmp_path, monkeypatch):
        reg = _make_rw_registry(tmp_path, monkeypatch)
        f = tmp_path / "dup.py"
        f.write_text("foo\nfoo\n", encoding="utf-8")
        _, handlers = reg.build(["patch_file"])
        result = handlers["patch_file"](
            {"path": str(f), "old_string": "foo", "new_string": "bar"},
            None,
        )
        assert result.startswith("error:") and "2 times" in result

    def test_patch_error_missing_path(self, tmp_path, monkeypatch):
        reg = _make_rw_registry(tmp_path, monkeypatch)
        _, handlers = reg.build(["patch_file"])
        result = handlers["patch_file"]({"old_string": "x", "new_string": "y"}, None)
        assert "path" in result and result.startswith("error:")

    def test_patch_error_missing_old_string(self, tmp_path, monkeypatch):
        reg = _make_rw_registry(tmp_path, monkeypatch)
        f = tmp_path / "any.py"
        f.write_text("x\n", encoding="utf-8")
        _, handlers = reg.build(["patch_file"])
        result = handlers["patch_file"]({"path": str(f), "new_string": "y"}, None)
        assert "old_string" in result and result.startswith("error:")

    def test_patch_error_file_not_found(self, tmp_path, monkeypatch):
        reg = _make_rw_registry(tmp_path, monkeypatch)
        _, handlers = reg.build(["patch_file"])
        result = handlers["patch_file"](
            {"path": str(tmp_path / "ghost.py"), "old_string": "x", "new_string": "y"},
            None,
        )
        assert result.startswith("error:") and "not found" in result

    def test_patch_scope_violation_raises(self, tmp_path, monkeypatch):
        import icdev.tools.ace.file_access_broker as _fab
        from icdev.tools.ace.file_access_broker import ScopeViolationError
        from icdev.tools.ace.agent_tools import AgentToolRegistry

        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.setattr(_fab, "_REPO_ROOT", tmp_path)

        class FakeSpec:
            coworker_id = "cw-scope"
            trust_tier = "green"
            folder_access = [{"path": str(sub), "mode": "rw"}]
            icdev_tools: list = []

        reg = AgentToolRegistry(FakeSpec(), instance_id="inst-scope")
        _, handlers = reg.build(["patch_file"])
        outside = tmp_path / "outside.py"
        outside.write_text("secret\n", encoding="utf-8")
        with pytest.raises(ScopeViolationError):
            handlers["patch_file"](
                {"path": str(outside), "old_string": "secret", "new_string": "x"},
                None,
            )

    def test_patch_available_via_build(self, tmp_path, monkeypatch):
        reg = _make_rw_registry(tmp_path, monkeypatch)
        tools, handlers = reg.build(["patch_file"])
        assert len(tools) == 1
        assert "patch_file" in handlers

    def test_patch_idempotent_on_second_distinct_call(self, tmp_path, monkeypatch):
        reg = _make_rw_registry(tmp_path, monkeypatch)
        f = tmp_path / "code.py"
        f.write_text("alpha\nbeta\n", encoding="utf-8")
        _, handlers = reg.build(["patch_file"])
        handlers["patch_file"]({"path": str(f), "old_string": "alpha", "new_string": "ALPHA"}, None)
        handlers["patch_file"]({"path": str(f), "old_string": "beta", "new_string": "BETA"}, None)
        assert f.read_text(encoding="utf-8") == "ALPHA\nBETA\n"
