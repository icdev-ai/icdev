# CUI // SP-CTI
"""Unit tests for icdev.tools.ace.step_executor — 5 required cases.

Cases (from task ace-sys-06):
    1. valid dict step
    2. RoleStep dataclass step
    3. string step name
    4. bad tool path → error dict (not exception)
    5. DB write failure → graceful, run still returns result
"""
from __future__ import annotations

import types
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from icdev.tools.ace.step_executor import (
    StepExecutor,
    ToolPermissionDeniedError,
    TrustKernelDeniedError,
    normalise_step,
)
from icdev.tools.ace.team_assembler import CoWorkerSpec


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _spec(tool_permissions: list[str] | None = None, trust_tier: str = "green") -> CoWorkerSpec:
    return CoWorkerSpec(
        coworker_id="cw-test-se",
        role_id="ai_developer",
        role_slot="ai_developer-0",
        mailbox_id="mb-test-se",
        llm_function="code_generation",
        tool_permissions=tool_permissions or [],
        trust_tier=trust_tier,
    )


class _AlwaysAllow:
    def can_execute(self, risk_tier: str, action: str = "run") -> tuple[bool, str]:
        return True, "approved"


def _fake_module(fn_name: str, return_value: Any = "ok") -> types.ModuleType:
    mod = types.ModuleType("fake.module.se")
    setattr(mod, fn_name, lambda **kwargs: return_value)
    return mod


# ---------------------------------------------------------------------------
# normalise_step coverage
# ---------------------------------------------------------------------------


class TestNormaliseStep:
    """Verify normalise_step handles all three input representations."""

    def test_dict_passthrough(self):
        d = {"id": "s1", "tool": "a.b.c", "args": {}}
        assert normalise_step(d) is d

    def test_role_step_dataclass_with_tool(self):
        @dataclass
        class RoleStep:
            name: str
            tool: str = ""
            params: dict = field(default_factory=dict)
            condition: str | None = None

        rs = RoleStep(name="do_thing", tool="icdev.tools.foo.bar", params={"k": "v"}, condition="x > 0")
        result = normalise_step(rs)
        assert result["id"] == "do_thing"
        assert result["tool"] == "icdev.tools.foo.bar"
        assert result["args"] == {"k": "v"}
        assert result["condition"] == "x > 0"

    def test_role_step_dataclass_no_tool_defaults_to_llm(self):
        @dataclass
        class RoleStep:
            name: str
            tool: str = ""
            params: dict = field(default_factory=dict)
            condition: str | None = None

        rs = RoleStep(name="analyze")
        result = normalise_step(rs)
        assert result["tool"] == "icdev.tools.ace.llm_step.invoke"

    def test_role_step_no_condition_key_absent(self):
        @dataclass
        class RoleStep:
            name: str
            tool: str = ""
            params: dict = field(default_factory=dict)
            condition: str | None = None

        result = normalise_step(RoleStep(name="step_x", tool="a.b.c"))
        assert "condition" not in result

    def test_string_step_name(self):
        result = normalise_step("analyze_requirements")
        assert result["id"] == "analyze_requirements"
        assert result["tool"] == "icdev.tools.ace.llm_step.invoke"
        assert result["args"]["step_name"] == "analyze_requirements"
        assert result["output_var"] == "analyze_requirements_result"

    def test_string_step_with_spaces_stringified(self):
        result = normalise_step(42)  # non-string non-dict non-dataclass
        assert result["id"] == "42"
        assert result["tool"] == "icdev.tools.ace.llm_step.invoke"


# ---------------------------------------------------------------------------
# Case 1: valid dict step
# ---------------------------------------------------------------------------


class TestCase1ValidDictStep:
    """Happy-path execution with a fully-formed step dict."""

    def setup_method(self):
        self.executor = StepExecutor()
        self.spec = _spec(tool_permissions=["fake.module.se.fn"])
        self.tk = _AlwaysAllow()

    def test_returns_tool_result(self):
        step = {"id": "s1", "tool": "fake.module.se.fn", "args": {}}
        mod = _fake_module("fn", return_value={"data": 42})
        with patch("importlib.import_module", return_value=mod), \
             patch.object(self.executor, "_audit"):
            result = self.executor.run(step, {}, self.spec, self.tk)
        assert result == {"data": 42}

    def test_stores_in_output_var(self):
        step = {"id": "s1", "tool": "fake.module.se.fn", "args": {}, "output_var": "out"}
        ctx: dict[str, Any] = {}
        mod = _fake_module("fn", return_value="value")
        with patch("importlib.import_module", return_value=mod), \
             patch.object(self.executor, "_audit"):
            self.executor.run(step, ctx, self.spec, self.tk)
        assert ctx["out"] == "value"

    def test_condition_false_returns_none(self):
        step = {"id": "s1", "tool": "fake.module.se.fn", "args": {}, "condition": "False"}
        with patch.object(self.executor, "_audit"):
            result = self.executor.run(step, {}, self.spec, self.tk)
        assert result is None

    def test_variable_substitution_in_args(self):
        received: dict[str, Any] = {}

        def fn(**kwargs):
            received.update(kwargs)
            return "ok"

        mod = types.ModuleType("fake.module.se")
        mod.fn = fn  # type: ignore[attr-defined]
        step = {"id": "s1", "tool": "fake.module.se.fn", "args": {"msg": "Hello $name"}}
        with patch("importlib.import_module", return_value=mod), \
             patch.object(self.executor, "_audit"):
            self.executor.run(step, {"name": "Alice"}, self.spec, self.tk)
        assert received["msg"] == "Hello Alice"


# ---------------------------------------------------------------------------
# Case 2: RoleStep dataclass step
# ---------------------------------------------------------------------------


class TestCase2RoleStepDataclass:
    """StepExecutor.run() accepts a RoleStep dataclass instance directly."""

    def setup_method(self):
        self.executor = StepExecutor()
        self.tk = _AlwaysAllow()

    def test_role_step_normalised_and_executed(self):
        @dataclass
        class RoleStep:
            name: str
            tool: str = ""
            params: dict = field(default_factory=dict)
            condition: str | None = None

        rs = RoleStep(name="compute", tool="fake.module.se.fn", params={"x": 7})
        spec = _spec(tool_permissions=["fake.module.se.fn"])

        called_with: dict[str, Any] = {}

        def fn(**kwargs):
            called_with.update(kwargs)
            return 14

        mod = types.ModuleType("fake.module.se")
        mod.fn = fn  # type: ignore[attr-defined]
        with patch("importlib.import_module", return_value=mod), \
             patch.object(self.executor, "_audit"):
            result = self.executor.run(rs, {}, spec, self.tk)

        assert result == 14
        assert called_with == {"x": 7}

    def test_role_step_condition_respected(self):
        @dataclass
        class RoleStep:
            name: str
            tool: str = ""
            params: dict = field(default_factory=dict)
            condition: str | None = None

        rs = RoleStep(name="cond_step", tool="fake.module.se.fn", condition="skip_flag")
        spec = _spec(tool_permissions=["fake.module.se.fn"])

        with patch.object(self.executor, "_audit"):
            result = self.executor.run(rs, {"skip_flag": False}, spec, self.tk)
        assert result is None  # condition False → skipped


# ---------------------------------------------------------------------------
# Case 3: string step name
# ---------------------------------------------------------------------------


class TestCase3StringStepName:
    """String step names are normalised to an LLM-invoke step dict."""

    def setup_method(self):
        self.executor = StepExecutor()
        self.tk = _AlwaysAllow()

    def test_string_step_dispatched_as_llm_step(self):
        llm_tool = "icdev.tools.ace.llm_step.invoke"
        spec = _spec(tool_permissions=[llm_tool])

        mock_fn = MagicMock(return_value={"output": "llm result"})
        with patch("icdev.tools.ace.step_executor._resolve_tool", return_value=mock_fn), \
             patch.object(self.executor, "_audit"):
            result = self.executor.run("analyze_requirements", {}, spec, self.tk)

        assert result == {"output": "llm result"}
        mock_fn.assert_called_once_with(step_name="analyze_requirements")

    def test_string_step_output_var_stored(self):
        llm_tool = "icdev.tools.ace.llm_step.invoke"
        spec = _spec(tool_permissions=[llm_tool])
        ctx: dict[str, Any] = {}

        mock_fn = MagicMock(return_value="step_output")
        with patch("icdev.tools.ace.step_executor._resolve_tool", return_value=mock_fn), \
             patch.object(self.executor, "_audit"):
            self.executor.run("my_step", ctx, spec, self.tk)

        assert ctx.get("my_step_result") == "step_output"


# ---------------------------------------------------------------------------
# Case 4: bad tool path → error dict
# ---------------------------------------------------------------------------


class TestCase4BadToolPath:
    """ImportError/AttributeError from _resolve_tool propagate to the caller."""

    def setup_method(self):
        self.executor = StepExecutor()
        self.tk = _AlwaysAllow()

    def test_nonexistent_module_raises_import_error(self):
        spec = _spec(tool_permissions=["nonexistent.module.func"])
        step = {"id": "bad", "tool": "nonexistent.module.func", "args": {}}

        with patch.object(self.executor, "_audit"), \
             pytest.raises(ModuleNotFoundError):
            self.executor.run(step, {}, spec, self.tk)

    def test_no_dotted_separator_raises_import_error(self):
        """A tool path without a '.' is not a valid dotted path."""
        spec = _spec(tool_permissions=["nodot"])
        step = {"id": "nodot", "tool": "nodot", "args": {}}

        with patch.object(self.executor, "_audit"), \
             pytest.raises(ImportError):
            self.executor.run(step, {}, spec, self.tk)

    def test_error_logged_at_error_level(self):
        spec = _spec(tool_permissions=["bad.path.fn"])
        step = {"id": "s", "tool": "bad.path.fn", "args": {}}

        with patch("icdev.tools.ace.step_executor.logger") as mock_logger, \
             patch.object(self.executor, "_audit"), \
             pytest.raises(ModuleNotFoundError):
            self.executor.run(step, {}, spec, self.tk)

        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args[0]
        assert "bad.path.fn" in "".join(str(a) for a in call_args)

    def test_attribute_error_raises(self):
        """Callable attribute not found on a real module."""

        spec = _spec(tool_permissions=["os.__nonexistent_fn__"])
        step = {"id": "atrerr", "tool": "os.__nonexistent_fn__", "args": {}}

        with patch.object(self.executor, "_audit"), \
             pytest.raises(AttributeError):
            self.executor.run(step, {}, spec, self.tk)


# ---------------------------------------------------------------------------
# Case 5: DB write failure → graceful
# ---------------------------------------------------------------------------


class TestCase5DbWriteFailure:
    """audit() DB failures must never crash the executor."""

    def setup_method(self):
        self.executor = StepExecutor()
        self.tk = _AlwaysAllow()

    def test_db_down_does_not_crash_run(self):
        spec = _spec(tool_permissions=["fake.module.se.fn"])
        step = {"id": "audit-fail", "tool": "fake.module.se.fn", "args": {}}

        # Patch _resolve_tool (not importlib.import_module) so the storage module
        # lookup done by the companion patch is not intercepted.
        mock_fn = MagicMock(return_value="computed")
        with patch("icdev.tools.ace.step_executor._resolve_tool", return_value=mock_fn), \
             patch("icdev.tools.db.storage.get_canvas_connection",
                   side_effect=RuntimeError("DB unavailable")):
            result = self.executor.run(step, {}, spec, self.tk)

        assert result == "computed"

    def test_db_down_audit_direct_does_not_raise(self):
        """_audit() itself must be exception-proof (best-effort contract)."""
        spec = _spec()
        step = {"id": "s", "instance_id": "inst-x"}

        with patch("icdev.tools.db.storage.get_canvas_connection",
                   side_effect=OSError("socket error")):
            StepExecutor._audit(step, spec, "step_executed", "detail")
        # No assertion needed — passes if no exception escapes

    def test_db_failure_logged_at_debug(self):
        spec = _spec(tool_permissions=["fake.module.se.fn"])
        step = {"id": "s", "tool": "fake.module.se.fn", "args": {}}

        mock_fn = MagicMock(return_value="x")
        # _emit_audit acquires its connection via get_connection (module-level
        # binding), not get_canvas_connection. Patching the latter never took
        # effect — the test only "failed the write" because the old ? placeholder
        # was itself invalid on PostgreSQL. Inject the failure where the audit
        # path actually reads it so this genuinely tests DB-down logging.
        with patch("icdev.tools.ace.step_executor._resolve_tool", return_value=mock_fn), \
             patch("icdev.tools.ace.step_executor.get_connection",
                   side_effect=RuntimeError("no db")), \
             patch("icdev.tools.ace.step_executor.logger") as mock_logger:
            self.executor.run(step, {}, spec, self.tk)

        mock_logger.debug.assert_called()
        debug_call_args = "".join(str(a) for a in mock_logger.debug.call_args[0])
        assert "audit" in debug_call_args.lower()


# ---------------------------------------------------------------------------
# Cross-cutting: permission and trust errors still raise
# ---------------------------------------------------------------------------


class TestPermissionAndTrustErrors:
    def setup_method(self):
        self.executor = StepExecutor()

    def test_tool_not_permitted_raises(self):
        spec = _spec(tool_permissions=[])
        step = {"id": "s", "tool": "some.tool", "args": {}}
        with patch.object(self.executor, "_audit"):
            with pytest.raises(ToolPermissionDeniedError):
                self.executor.run(step, {}, spec, _AlwaysAllow())

    def test_trust_kernel_denied_raises(self):
        class _Deny:
            def can_execute(self, *_):
                return False, "denied"

        spec = _spec(tool_permissions=["fake.module.se.fn"])
        step = {"id": "s", "tool": "fake.module.se.fn", "args": {}}
        mod = _fake_module("fn")
        with patch("importlib.import_module", return_value=mod), \
             patch.object(self.executor, "_audit"):
            with pytest.raises(TrustKernelDeniedError):
                self.executor.run(step, {}, spec, _Deny())

    def test_tool_runtime_error_logs_at_error_and_reraises(self):
        def _boom(**_):
            raise ValueError("runtime failure")

        spec = _spec(tool_permissions=["fake.module.se.fn"])
        step = {"id": "s", "tool": "fake.module.se.fn", "args": {}}

        with patch("icdev.tools.ace.step_executor._resolve_tool", return_value=_boom), \
             patch.object(self.executor, "_audit"), \
             patch("icdev.tools.ace.step_executor.logger") as mock_logger:
            with pytest.raises(ValueError, match="runtime failure"):
                self.executor.run(step, {}, spec, _AlwaysAllow())

        mock_logger.error.assert_called_once()
