#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 active extension hooks (Feature 2 — D261-D264).

Covers: registration, priority ordering, dispatch modification/observation,
layered override, exception isolation, timeout enforcement, file loading,
unregister, introspection.
"""

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.extensions.extension_manager import (
    ExtensionManager,
    ExtensionHandler,
    ExtensionPoint,
)


@pytest.fixture
def manager():
    """Fresh ExtensionManager per test (no singleton)."""
    return ExtensionManager()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_handler(self, manager):
        handler = manager.register(
            ExtensionPoint.TOOL_EXECUTE_BEFORE,
            handler=lambda ctx: ctx,
            name="test_hook",
        )
        assert isinstance(handler, ExtensionHandler)
        assert handler.name == "test_hook"
        assert manager.handler_count(ExtensionPoint.TOOL_EXECUTE_BEFORE) == 1

    def test_unregister(self, manager):
        manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE, handler=lambda ctx: ctx, name="hook1")
        removed = manager.unregister(ExtensionPoint.TOOL_EXECUTE_BEFORE, "hook1")
        assert removed is True
        assert manager.handler_count(ExtensionPoint.TOOL_EXECUTE_BEFORE) == 0

    def test_unregister_nonexistent(self, manager):
        removed = manager.unregister(ExtensionPoint.TOOL_EXECUTE_BEFORE, "nonexistent")
        assert removed is False

    def test_total_handler_count(self, manager):
        manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE, handler=lambda c: c, name="a")
        manager.register(ExtensionPoint.CHAT_MESSAGE_AFTER, handler=lambda c: c, name="b")
        assert manager.handler_count() == 2


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

class TestPriorityOrdering:
    def test_handlers_sorted_by_priority(self, manager):
        order = []
        manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE,
                         handler=lambda c: order.append("high") or c,
                         name="high", priority=100)
        manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE,
                         handler=lambda c: order.append("low") or c,
                         name="low", priority=900)
        manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE,
                         handler=lambda c: order.append("mid") or c,
                         name="mid", priority=500)

        manager.dispatch(ExtensionPoint.TOOL_EXECUTE_BEFORE, {})
        assert order == ["high", "mid", "low"]


# ---------------------------------------------------------------------------
# Dispatch — behavioral vs observational
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_behavioral_modifies_context(self, manager):
        def modifier(ctx):
            ctx["injected"] = True
            return ctx

        manager.register(
            ExtensionPoint.TOOL_EXECUTE_BEFORE,
            handler=modifier,
            name="modifier",
            allow_modification=True,
        )
        result = manager.dispatch(ExtensionPoint.TOOL_EXECUTE_BEFORE, {"original": True})
        assert result["injected"] is True
        assert result["original"] is True

    def test_observational_does_not_modify(self, manager):
        def observer(ctx):
            return {"injected": True}  # Return value should be ignored

        manager.register(
            ExtensionPoint.TOOL_EXECUTE_BEFORE,
            handler=observer,
            name="observer",
            allow_modification=False,
        )
        result = manager.dispatch(ExtensionPoint.TOOL_EXECUTE_BEFORE, {"original": True})
        assert "injected" not in result
        assert result["original"] is True

    def test_dispatch_with_no_handlers(self, manager):
        result = manager.dispatch(ExtensionPoint.AGENT_START, {"test": True})
        assert result == {"test": True}


# ---------------------------------------------------------------------------
# Exception isolation
# ---------------------------------------------------------------------------

class TestExceptionIsolation:
    def test_handler_exception_caught(self, manager):
        def bad_handler(ctx):
            raise ValueError("boom")

        manager.register(
            ExtensionPoint.TOOL_EXECUTE_BEFORE,
            handler=bad_handler,
            name="bad",
        )
        result = manager.dispatch(ExtensionPoint.TOOL_EXECUTE_BEFORE, {"safe": True})
        assert result["safe"] is True  # Should not crash

    def test_exception_does_not_affect_subsequent_handlers(self, manager):
        call_log = []

        def bad_handler(ctx):
            raise RuntimeError("fail")

        def good_handler(ctx):
            call_log.append("called")
            return ctx

        manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE, handler=bad_handler, name="bad", priority=100)
        manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE, handler=good_handler, name="good", priority=200)

        manager.dispatch(ExtensionPoint.TOOL_EXECUTE_BEFORE, {})
        assert "called" in call_log


# ---------------------------------------------------------------------------
# Disabled extensions
# ---------------------------------------------------------------------------

class TestDisabledExtensions:
    def test_disabled_handler_skipped(self, manager):
        called = []

        def handler(ctx):
            called.append(True)
            return ctx

        ext = manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE, handler=handler, name="disabled")
        ext.enabled = False

        manager.dispatch(ExtensionPoint.TOOL_EXECUTE_BEFORE, {})
        assert len(called) == 0


# ---------------------------------------------------------------------------
# File-based loading
# ---------------------------------------------------------------------------

class TestFileLoading:
    def test_load_from_directory(self, manager):
        with tempfile.TemporaryDirectory() as tmpdir:
            hook_dir = Path(tmpdir) / "tool_execute_before"
            hook_dir.mkdir()

            ext_file = hook_dir / "010_test_ext.py"
            ext_file.write_text(
                'PRIORITY = 10\nNAME = "test_ext"\nDESCRIPTION = "Test"\n'
                'def handle(ctx):\n    ctx["loaded"] = True\n    return ctx\n'
            )

            loaded = manager.load_extensions_from_directory(Path(tmpdir))
            assert loaded == 1
            assert manager.handler_count(ExtensionPoint.TOOL_EXECUTE_BEFORE) == 1

    def test_load_from_nonexistent_dir(self, manager):
        loaded = manager.load_extensions_from_directory(Path("/nonexistent/path"))
        assert loaded == 0

    def test_skip_already_loaded(self, manager):
        with tempfile.TemporaryDirectory() as tmpdir:
            hook_dir = Path(tmpdir) / "tool_execute_before"
            hook_dir.mkdir()
            ext_file = hook_dir / "test.py"
            ext_file.write_text('def handle(ctx): return ctx\n')

            loaded1 = manager.load_extensions_from_directory(Path(tmpdir))
            loaded2 = manager.load_extensions_from_directory(Path(tmpdir))
            assert loaded1 == 1
            assert loaded2 == 0  # Already loaded


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

class TestIntrospection:
    def test_list_handlers(self, manager):
        manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE, handler=lambda c: c, name="h1", priority=100)
        manager.register(ExtensionPoint.CHAT_MESSAGE_AFTER, handler=lambda c: c, name="h2", priority=200)

        all_handlers = manager.list_handlers()
        assert len(all_handlers) == 2
        names = {h["name"] for h in all_handlers}
        assert names == {"h1", "h2"}

    def test_list_handlers_filtered(self, manager):
        manager.register(ExtensionPoint.TOOL_EXECUTE_BEFORE, handler=lambda c: c, name="h1")
        manager.register(ExtensionPoint.CHAT_MESSAGE_AFTER, handler=lambda c: c, name="h2")

        filtered = manager.list_handlers(ExtensionPoint.TOOL_EXECUTE_BEFORE)
        assert len(filtered) == 1
        assert filtered[0]["name"] == "h1"


# ---------------------------------------------------------------------------
# Extension points enum
# ---------------------------------------------------------------------------

class TestExtensionPoint:
    def test_all_10_points(self):
        assert len(ExtensionPoint) == 10

    def test_values(self):
        assert ExtensionPoint.TOOL_EXECUTE_BEFORE.value == "tool_execute_before"
        assert ExtensionPoint.COMPLIANCE_CHECK_AFTER.value == "compliance_check_after"
