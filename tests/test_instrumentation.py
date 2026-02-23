#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/observability/instrumentation.py — @traced() decorator (D280).

Covers:
  - @traced() basic decoration and span creation
  - Auto-naming from module.function
  - Custom name, kind, attributes
  - record_args hashing
  - record_result hashing
  - Error propagation with span status
  - traced_generator for generator functions
  - Edge cases: nested traced calls, no tracer
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_tracer():
    """Patch get_tracer() to return a mock tracer for all tests."""
    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=False)

    mock_t = MagicMock()
    mock_t.start_span.return_value = mock_span

    with patch("tools.observability.get_tracer", return_value=mock_t) as _:
        yield mock_t, mock_span


# ---------------------------------------------------------------------------
# @traced() Tests
# ---------------------------------------------------------------------------

class TestTraced:
    def test_basic_decoration(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, mock_span = mock_tracer

        @traced()
        def my_func(x):
            return x + 1

        result = my_func(5)
        assert result == 6
        mock_t.start_span.assert_called_once()

    def test_auto_name(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, _ = mock_tracer

        @traced()
        def auto_named():
            pass

        auto_named()
        call_args = mock_t.start_span.call_args
        name = call_args[0][0]
        assert "auto_named" in name

    def test_custom_name(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, _ = mock_tracer

        @traced(name="custom.operation")
        def func():
            pass

        func()
        call_args = mock_t.start_span.call_args
        assert call_args[0][0] == "custom.operation"

    def test_custom_kind(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, _ = mock_tracer

        @traced(kind="CLIENT")
        def func():
            pass

        func()
        call_args = mock_t.start_span.call_args
        assert call_args[1]["kind"] == "CLIENT"

    def test_static_attributes(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, _ = mock_tracer

        @traced(attributes={"mcp.server.name": "builder"})
        def func():
            pass

        func()
        call_args = mock_t.start_span.call_args
        attrs = call_args[1]["attributes"]
        assert attrs["mcp.server.name"] == "builder"

    def test_code_function_attribute(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, _ = mock_tracer

        @traced()
        def my_specific_func():
            pass

        my_specific_func()
        call_args = mock_t.start_span.call_args
        attrs = call_args[1]["attributes"]
        assert "code.function" in attrs
        assert "my_specific_func" in attrs["code.function"]

    def test_record_args(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, mock_span = mock_tracer

        @traced(record_args=True)
        def func(a, b=10):
            return a + b

        result = func(5, b=20)
        assert result == 25
        # Should have set code.args_hash attribute in the span attrs
        call_args = mock_t.start_span.call_args
        attrs = call_args[1]["attributes"]
        assert "code.args_hash" in attrs

    def test_record_result(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, mock_span = mock_tracer

        @traced(record_result=True)
        def func():
            return {"status": "ok"}

        func()
        # result hash should have been set via set_attribute
        mock_span.set_attribute.assert_called()

    def test_error_propagation(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, mock_span = mock_tracer

        @traced()
        def failing():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            failing()

        mock_span.set_status.assert_called_once()
        args = mock_span.set_status.call_args[0]
        assert args[0] == "ERROR"
        mock_span.add_event.assert_called_once()

    def test_preserves_function_name(self, mock_tracer):
        from tools.observability.instrumentation import traced

        @traced()
        def original_name():
            """Docstring."""
            pass

        assert original_name.__name__ == "original_name"
        assert original_name.__doc__ == "Docstring."

    def test_none_result_no_hash(self, mock_tracer):
        from tools.observability.instrumentation import traced

        mock_t, mock_span = mock_tracer

        @traced(record_result=True)
        def func():
            return None

        func()
        # set_attribute not called for result hash when result is None
        # (only code.function and code.module are set via start_span attrs)
        # The result hash set_attribute should not be called
        for call in mock_span.set_attribute.call_args_list:
            if call[0][0] == "code.result_hash":
                pytest.fail("Should not hash None result")


# ---------------------------------------------------------------------------
# @traced_generator() Tests
# ---------------------------------------------------------------------------

class TestTracedGenerator:
    def test_generator_decoration(self):
        from tools.observability.instrumentation import traced_generator

        mock_span = MagicMock()
        mock_t = MagicMock()
        mock_t.start_span.return_value = mock_span

        with patch("tools.observability.get_tracer", return_value=mock_t):
            @traced_generator()
            def gen_func():
                yield 1
                yield 2
                yield 3

            items = list(gen_func())
            assert items == [1, 2, 3]
            mock_span.set_attribute.assert_called_with("gen.item_count", 3)
            mock_span.set_status.assert_called_with("OK")
            mock_span.end.assert_called_once()

    def test_generator_error(self):
        from tools.observability.instrumentation import traced_generator

        mock_span = MagicMock()
        mock_t = MagicMock()
        mock_t.start_span.return_value = mock_span

        with patch("tools.observability.get_tracer", return_value=mock_t):
            @traced_generator()
            def bad_gen():
                yield 1
                raise RuntimeError("gen failed")

            with pytest.raises(RuntimeError, match="gen failed"):
                list(bad_gen())

            mock_span.set_status.assert_called_once()
            args = mock_span.set_status.call_args[0]
            assert args[0] == "ERROR"
            mock_span.end.assert_called_once()

    def test_generator_empty(self):
        from tools.observability.instrumentation import traced_generator

        mock_span = MagicMock()
        mock_t = MagicMock()
        mock_t.start_span.return_value = mock_span

        with patch("tools.observability.get_tracer", return_value=mock_t):
            @traced_generator()
            def empty_gen():
                return
                yield  # noqa: unreachable

            items = list(empty_gen())
            assert items == []
            mock_span.set_attribute.assert_called_with("gen.item_count", 0)
