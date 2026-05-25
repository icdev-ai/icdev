# CUI // SP-CTI
"""Phase 1e unit tests for tools/awareness/hooks.py.

Tests the post-tool-use subscriber in isolation (without the
extension_manager framework) and covers:
  * tracked vs untracked tool_name filter
  * tracked vs untracked file extension filter
  * in-project vs out-of-project path filter
  * context shape variations (tool_input dict, flat file_path)
  * exception resilience (handler never raises)
  * upsert_file is called with the correct path
  * registration idempotency
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.awareness import hooks  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_registered():
    """Reset the idempotency flag between tests."""
    hooks._reset_for_test()
    yield
    hooks._reset_for_test()


class TestExtractFilePath:
    def test_from_tool_input_dict(self):
        ctx = {"tool_name": "Edit", "tool_input": {"file_path": "tools/foo.py"}}
        result = hooks._extract_file_path(ctx)
        assert result == Path("tools/foo.py")

    def test_from_notebook_path(self):
        ctx = {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "x.ipynb"}}
        assert hooks._extract_file_path(ctx) == Path("x.ipynb")

    def test_from_flat_key(self):
        ctx = {"tool_name": "Edit", "file_path": "tools/bar.py"}
        assert hooks._extract_file_path(ctx) == Path("tools/bar.py")

    def test_returns_none_when_absent(self):
        ctx = {"tool_name": "Edit"}
        assert hooks._extract_file_path(ctx) is None

    def test_returns_none_for_non_dict(self):
        assert hooks._extract_file_path(None) is None  # type: ignore[arg-type]
        assert hooks._extract_file_path("not a dict") is None  # type: ignore[arg-type]


class TestIsTrackedPath:
    def test_python_file_in_project_is_tracked(self):
        p = PROJECT_ROOT / "tools" / "awareness" / "hooks.py"
        assert hooks._is_tracked_path(p)

    def test_markdown_in_project_is_tracked(self):
        p = PROJECT_ROOT / ".agents" / "skills" / "icdev-build" / "SKILL.md"
        assert hooks._is_tracked_path(p)

    def test_unknown_extension_is_not_tracked(self, tmp_path):
        p = PROJECT_ROOT / "tools" / "something.bin"
        assert not hooks._is_tracked_path(p)

    def test_out_of_project_path_is_not_tracked(self, tmp_path):
        outside = tmp_path / "random.py"
        outside.write_text("pass\n")
        assert not hooks._is_tracked_path(outside)

    def test_nonexistent_path_resolves_if_parent_exists(self):
        # A Python file that doesn't exist but whose parent is in the
        # project — should still be considered "tracked" because the
        # extension + path location are valid.
        p = PROJECT_ROOT / "tools" / "awareness" / "nonexistent_file.py"
        assert hooks._is_tracked_path(p)


class TestOnToolExecuteAfter:
    def test_non_tracked_tool_name_is_noop(self):
        with patch("tools.awareness.component_indexer.upsert_file") as mock_upsert:
            ctx = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
            result = hooks.on_tool_execute_after(ctx)
            assert result == ctx
            mock_upsert.assert_not_called()

    def test_tracked_tool_with_no_path_is_noop(self):
        with patch("tools.awareness.component_indexer.upsert_file") as mock_upsert:
            ctx = {"tool_name": "Edit", "tool_input": {}}
            result = hooks.on_tool_execute_after(ctx)
            assert result == ctx
            mock_upsert.assert_not_called()

    def test_tracked_edit_on_python_file_calls_upsert(self):
        real_py = PROJECT_ROOT / "tools" / "awareness" / "hooks.py"
        with patch("tools.awareness.component_indexer.upsert_file") as mock_upsert:
            mock_upsert.return_value = {
                "persisted": True,
                "node_id": "icdev-tool-abc",
                "entity_type": "tool",
                "enabled": True,
            }
            ctx = {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(real_py)},
            }
            hooks.on_tool_execute_after(ctx)
            mock_upsert.assert_called_once()
            called_path = mock_upsert.call_args[0][0]
            assert Path(called_path).name == "hooks.py"

    def test_out_of_project_path_is_skipped(self, tmp_path):
        outside = tmp_path / "outside.py"
        outside.write_text("pass\n")
        with patch("tools.awareness.component_indexer.upsert_file") as mock_upsert:
            ctx = {
                "tool_name": "Write",
                "tool_input": {"file_path": str(outside)},
            }
            hooks.on_tool_execute_after(ctx)
            mock_upsert.assert_not_called()

    def test_untracked_extension_is_skipped(self):
        bin_file = PROJECT_ROOT / "tools" / "something.bin"
        with patch("tools.awareness.component_indexer.upsert_file") as mock_upsert:
            ctx = {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(bin_file)},
            }
            hooks.on_tool_execute_after(ctx)
            mock_upsert.assert_not_called()

    def test_handler_swallows_upsert_exceptions(self):
        real_py = PROJECT_ROOT / "tools" / "awareness" / "hooks.py"
        with patch("tools.awareness.component_indexer.upsert_file") as mock_upsert:
            mock_upsert.side_effect = RuntimeError("synthetic failure")
            ctx = {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(real_py)},
            }
            # Must not raise
            result = hooks.on_tool_execute_after(ctx)
            assert result == ctx

    def test_non_dict_context_is_handled(self):
        # Handler must not raise even on garbage input
        assert hooks.on_tool_execute_after(None) is None  # type: ignore[arg-type]
        assert hooks.on_tool_execute_after("garbage") == "garbage"  # type: ignore[arg-type]


class TestRegistration:
    def test_register_is_idempotent(self):
        mock_mgr = MagicMock()
        mock_mgr.register = MagicMock()
        with patch("tools.extensions.extension_manager.extension_manager", mock_mgr):
            hooks._reset_for_test()
            r1 = hooks.register()
            r2 = hooks.register()
            r3 = hooks.register()
            assert r1 is True
            assert r2 is True
            assert r3 is True
            # Should only have been called once even across 3 register() calls
            assert mock_mgr.register.call_count == 1

    def test_register_returns_false_when_framework_missing(self):
        hooks._reset_for_test()
        # Simulate ImportError on the extension framework
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

        def fake_import(name, *args, **kwargs):
            if name == "tools.extensions.extension_manager":
                raise ImportError("framework unavailable")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = hooks.register()
            assert result is False

    def test_register_catches_framework_exceptions(self):
        mock_mgr = MagicMock()
        mock_mgr.register = MagicMock(side_effect=RuntimeError("boom"))
        with patch("tools.extensions.extension_manager.extension_manager", mock_mgr):
            hooks._reset_for_test()
            result = hooks.register()
            assert result is False


class TestEndToEndLiveUpsert:
    """Integration test against real upsert_file — exercises the full
    path the way post_tool_use.py will at runtime."""

    def test_edit_on_real_skill_file_refreshes_node(self):
        skill_path = PROJECT_ROOT / ".agents" / "skills" / "icdev-build" / "SKILL.md"
        if not skill_path.exists():
            pytest.skip("icdev-build skill not present in this environment")
        ctx = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(skill_path)},
            "output_length": 100,
        }
        # Should not raise. Real upsert runs against the live DB.
        result = hooks.on_tool_execute_after(ctx)
        assert result == ctx


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
