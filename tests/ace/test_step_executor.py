"""Tests for icdev.tools.ace.step_executor."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from icdev.tools.ace.step_executor import (
    CoWorkerSpec,
    StepExecutor,
    ToolPermissionDeniedError,
    TrustKernelDeniedError,
    _eval_condition,
    _substitute_vars,
)


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


def _make_trust_kernel(allowed: bool = True, reason: str = "approved"):
    tk = MagicMock()
    tk.can_execute.return_value = (allowed, reason)
    return tk


def _make_spec(tool_permissions=None, trust_tier="green", name="test-worker"):
    return CoWorkerSpec(
        tool_permissions=tool_permissions or [],
        trust_tier=trust_tier,
        name=name,
    )


# Patch out the DB audit so tests don't need a live connection.
@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(
        "icdev.tools.ace.step_executor._emit_audit",
        lambda **_kw: None,
    )


# ---------------------------------------------------------------------------
# _substitute_vars
# ---------------------------------------------------------------------------


def test_substitute_vars_string():
    ctx = {"foo": "bar", "n": 42}
    assert _substitute_vars("value is $foo and $n", ctx) == "value is bar and 42"


def test_substitute_vars_unknown_key_kept():
    assert _substitute_vars("$unknown", {}) == "$unknown"


def test_substitute_vars_dict():
    result = _substitute_vars({"k": "$x"}, {"x": "hello"})
    assert result == {"k": "hello"}


def test_substitute_vars_list():
    result = _substitute_vars(["$a", "$b"], {"a": 1, "b": 2})
    assert result == ["1", "2"]


def test_substitute_vars_non_string_passthrough():
    assert _substitute_vars(123, {}) == 123
    assert _substitute_vars(None, {}) is None


# ---------------------------------------------------------------------------
# _eval_condition
# ---------------------------------------------------------------------------


def test_eval_condition_true():
    assert _eval_condition("True", {}) is True


def test_eval_condition_false():
    assert _eval_condition("False", {}) is False


def test_eval_condition_empty_string_returns_true():
    assert _eval_condition("", {}) is True


def test_eval_condition_var_substitution():
    assert _eval_condition("$ready == True", {"ready": True}) is True


def test_eval_condition_bad_expr_returns_false():
    assert _eval_condition("this is not python", {}) is False


# ---------------------------------------------------------------------------
# StepExecutor — permission checks
# ---------------------------------------------------------------------------


def test_tool_not_in_permissions_raises():
    executor = StepExecutor()
    spec = _make_spec(tool_permissions=["other.tool"])
    step = {"id": "s1", "tool": "my.tool.func", "args": {}}
    tk = _make_trust_kernel()

    with pytest.raises(ToolPermissionDeniedError, match="not in spec.tool_permissions"):
        executor.run(step, {}, spec, tk)


def test_trust_kernel_denied_raises():
    executor = StepExecutor()
    tool_path = "icdev.tools.ace._test_helper.noop"
    spec = _make_spec(tool_permissions=[tool_path])
    step = {"id": "s1", "tool": tool_path, "args": {}}
    tk = _make_trust_kernel(allowed=False, reason="tier not allowed")

    with pytest.raises(TrustKernelDeniedError, match="Trust kernel denied"):
        executor.run(step, {}, spec, tk)


# ---------------------------------------------------------------------------
# StepExecutor — condition skip
# ---------------------------------------------------------------------------


def test_step_skipped_when_condition_false():
    executor = StepExecutor()
    spec = _make_spec(tool_permissions=["some.tool"])
    step = {"id": "s1", "tool": "some.tool", "args": {}, "condition": "False"}
    tk = _make_trust_kernel()

    result = executor.run(step, {}, spec, tk)
    assert result is None
    # trust_kernel.can_execute should NOT be called for a skipped step
    tk.can_execute.assert_not_called()


def test_step_not_skipped_when_condition_true(monkeypatch):
    executor = StepExecutor()
    tool_path = "icdev.tools.ace._dummy.run"
    spec = _make_spec(tool_permissions=[tool_path])
    step = {"id": "s1", "tool": tool_path, "args": {}, "condition": "True"}
    tk = _make_trust_kernel()

    dummy_fn = MagicMock(return_value="ok")
    monkeypatch.setattr(
        "icdev.tools.ace.step_executor._resolve_tool",
        lambda path: dummy_fn,
    )
    result = executor.run(step, {}, spec, tk)
    assert result == "ok"
    dummy_fn.assert_called_once()


# ---------------------------------------------------------------------------
# StepExecutor — successful execution + output_var
# ---------------------------------------------------------------------------


def test_result_stored_in_output_var(monkeypatch):
    executor = StepExecutor()
    tool_path = "icdev.tools.ace._dummy.compute"
    spec = _make_spec(tool_permissions=[tool_path])
    context = {"x": 10}
    step = {"id": "s2", "tool": tool_path, "args": {"val": "$x"}, "output_var": "result"}
    tk = _make_trust_kernel()

    captured_kwargs = {}

    def fake_tool(**kwargs):
        captured_kwargs.update(kwargs)
        return "computed"

    monkeypatch.setattr(
        "icdev.tools.ace.step_executor._resolve_tool",
        lambda path: fake_tool,
    )
    ret = executor.run(step, context, spec, tk)
    assert ret == "computed"
    assert context["result"] == "computed"
    assert captured_kwargs["val"] == "10"  # $x substituted to str(10)


def test_no_output_var_does_not_mutate_context(monkeypatch):
    executor = StepExecutor()
    tool_path = "icdev.tools.ace._dummy.compute"
    spec = _make_spec(tool_permissions=[tool_path])
    context = {}
    step = {"id": "s3", "tool": tool_path, "args": {}}
    tk = _make_trust_kernel()

    monkeypatch.setattr(
        "icdev.tools.ace.step_executor._resolve_tool",
        lambda path: lambda: "x",
    )
    executor.run(step, context, spec, tk)
    assert "result" not in context


# ---------------------------------------------------------------------------
# StepExecutor — tool execution error propagates
# ---------------------------------------------------------------------------


def test_tool_exception_propagates(monkeypatch):
    executor = StepExecutor()
    tool_path = "icdev.tools.ace._dummy.bad"
    spec = _make_spec(tool_permissions=[tool_path])
    step = {"id": "s4", "tool": tool_path, "args": {}}
    tk = _make_trust_kernel()

    def boom():
        raise ValueError("something broke")

    monkeypatch.setattr(
        "icdev.tools.ace.step_executor._resolve_tool",
        lambda path: boom,
    )
    with pytest.raises(ValueError, match="something broke"):
        executor.run(step, {}, spec, tk)


# ---------------------------------------------------------------------------
# CoWorkerSpec defaults
# ---------------------------------------------------------------------------


def test_coworker_spec_defaults():
    spec = CoWorkerSpec()
    assert spec.tool_permissions == []
    assert spec.trust_tier == "green"
    assert spec.name == "ace-coworker"
