# CUI // SP-CTI
"""Tests for icdev.tools.ace.step_executor.StepExecutor."""
from __future__ import annotations

import sqlite3
import types
from typing import Any
from unittest.mock import patch

import pytest

import icdev.tools.ace.step_executor as step_executor
from icdev.tools.ace.db.init_db import (
    COWORKER_STATES,
    INSTANCE_STATES,
    SCHEMA,
    repair_state_constraints,
)
from icdev.tools.ace.step_executor import (
    StepExecutor,
    ToolPermissionDeniedError,
    TrustKernelDeniedError,
    _emit_audit,
    _eval_condition,
    _substitute,
)
from icdev.tools.ace.team_assembler import CoWorkerSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _spec(tool_permissions: list[str] | None = None, trust_tier: str = "yellow") -> CoWorkerSpec:
    return CoWorkerSpec(
        coworker_id="cw-test-001",
        role_id="ai_developer",
        role_slot="ai_developer-0",
        mailbox_id="mailbox:cw-test-001",
        llm_function="code_generation",
        tool_permissions=tool_permissions or [],
        trust_tier=trust_tier,
    )


class _AlwaysAllow:
    def can_execute(self, risk_tier: str, action: str = "run") -> tuple[bool, str]:
        return True, "approved"


class _AlwaysDeny:
    def can_execute(self, risk_tier: str, action: str = "run") -> tuple[bool, str]:
        return False, "tier not allowed"


def _make_step(
    tool: str = "fake.module.fn",
    args: dict | None = None,
    output_var: str | None = "result",
    condition: str | None = None,
    instance_id: str = "inst-001",
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "step-01",
        "tool": tool,
        "args": args or {},
        "instance_id": instance_id,
    }
    if output_var is not None:
        step["output_var"] = output_var
    if condition is not None:
        step["condition"] = condition
    return step


def _fake_module(fn_name: str, return_value: Any = "ok"):
    """Return a synthetic module with one callable."""
    mod = types.ModuleType("fake.module")
    setattr(mod, fn_name, lambda **kwargs: return_value)
    return mod


# ---------------------------------------------------------------------------
# _substitute
# ---------------------------------------------------------------------------


class TestSubstitute:
    def test_whole_var_is_stringified_like_inline(self):
        """Both substitution forms stringify — see _substitute's docstring.

        Previously asserted `== 42`, i.e. that a whole-var reference preserves
        the context value's type. _substitute has stringified both forms since
        its first commit and documents why: step arguments originate in
        JSON/YAML and are text by the time a tool receives them. The assertion
        was added long afterwards and never matched the code.

        Renamed rather than deleted: type-preserving whole-var substitution is
        a defensible design (Ansible and GitHub Actions both do it), so if it
        is wanted it should be a deliberate change to _substitute and its
        docstring, not a lone test asserting a behaviour nothing implements.
        """
        assert _substitute("$count", {"count": 42}) == "42"

    def test_inline_var_stringified(self):
        assert _substitute("prefix_$name", {"name": "foo"}) == "prefix_foo"

    def test_missing_var_kept_as_is(self):
        assert _substitute("$missing", {}) == "$missing"

    def test_non_string_passthrough(self):
        assert _substitute(123, {}) == 123
        assert _substitute({"a": 1}, {}) == {"a": 1}

    def test_multiple_vars(self):
        assert _substitute("$a-$b", {"a": "x", "b": "y"}) == "x-y"


# ---------------------------------------------------------------------------
# _eval_condition
# ---------------------------------------------------------------------------


class TestEvalCondition:
    def test_true_expression(self):
        assert _eval_condition("x > 0", {"x": 5}) is True

    def test_false_expression(self):
        assert _eval_condition("x > 10", {"x": 5}) is False

    def test_context_variable(self):
        assert _eval_condition("found", {"found": True}) is True

    def test_syntax_error_returns_false(self):
        assert _eval_condition("!!!invalid!!!", {}) is False

    def test_undefined_var_returns_false(self):
        assert _eval_condition("missing_var", {}) is False

    def test_len_builtin_available(self):
        assert _eval_condition("len(items) > 1", {"items": [1, 2, 3]}) is True


# ---------------------------------------------------------------------------
# StepExecutor.run
# ---------------------------------------------------------------------------


class TestStepExecutorRun:
    def setup_method(self):
        self.executor = StepExecutor()

    def _run(self, step, context=None, spec=None, trust_kernel=None, module=None):
        if context is None:
            context = {}
        if spec is None:
            spec = _spec(tool_permissions=["fake.module.fn"])
        if trust_kernel is None:
            trust_kernel = _AlwaysAllow()
        if module is None:
            module = _fake_module("fn", return_value="result-value")
        with patch("importlib.import_module", return_value=module), \
             patch.object(self.executor, "_audit"):
            return self.executor.run(step, context, spec, trust_kernel)

    # -- happy path --

    def test_returns_tool_result(self):
        step = _make_step(tool="fake.module.fn", output_var="out")
        result = self._run(step)
        assert result == "result-value"

    def test_stores_result_in_context(self):
        ctx = {}
        step = _make_step(tool="fake.module.fn", output_var="my_var")
        self._run(step, context=ctx)
        assert ctx["my_var"] == "result-value"

    def test_no_output_var_does_not_mutate_context(self):
        ctx = {}
        step = _make_step(tool="fake.module.fn", output_var=None)
        self._run(step, context=ctx)
        assert ctx == {}

    def test_args_substituted(self):
        received = {}

        def fn(**kwargs):
            received.update(kwargs)
            return "ok"

        mod = types.ModuleType("fake.module")
        mod.fn = fn
        ctx = {"name": "Alice"}
        step = _make_step(tool="fake.module.fn", args={"greeting": "Hello $name"})
        self._run(step, context=ctx, module=mod)
        assert received["greeting"] == "Hello Alice"

    # -- condition skipping --

    def test_condition_false_skips_step(self):
        ctx = {"flag": False}
        step = _make_step(tool="fake.module.fn", condition="flag")
        result = self._run(step, context=ctx)
        assert result is None

    def test_condition_true_executes_step(self):
        ctx = {"flag": True}
        step = _make_step(tool="fake.module.fn", condition="flag")
        result = self._run(step, context=ctx)
        assert result == "result-value"

    def test_condition_skipped_does_not_touch_context(self):
        ctx = {}
        step = _make_step(tool="fake.module.fn", condition="False", output_var="out")
        self._run(step, context=ctx)
        assert "out" not in ctx

    # -- permission errors --

    def test_tool_not_in_permissions_raises(self):
        spec = _spec(tool_permissions=["other.module.fn"])
        step = _make_step(tool="fake.module.fn")
        with pytest.raises(ToolPermissionDeniedError):
            self._run(step, spec=spec)

    def test_trust_kernel_denied_raises(self):
        spec = _spec(tool_permissions=["fake.module.fn"])
        step = _make_step(tool="fake.module.fn")
        with pytest.raises(TrustKernelDeniedError):
            self._run(step, spec=spec, trust_kernel=_AlwaysDeny())

    # -- import errors --

    def test_import_error_is_raised(self):
        """ImportError from _resolve_tool propagates to the caller."""
        spec = _spec(tool_permissions=["bad.module.fn"])
        step = _make_step(tool="bad.module.fn")
        with patch("importlib.import_module", side_effect=ImportError("no such module")), \
             patch.object(self.executor, "_audit"), \
             pytest.raises(ImportError, match="no such module"):
            self.executor.run(step, {}, spec, _AlwaysAllow())

    # -- audit is best-effort --

    def test_audit_db_failure_does_not_crash(self):
        """A DB error inside _audit must be swallowed — never propagate."""
        import icdev.tools.db.storage as storage_mod

        step = _make_step(tool="fake.module.fn")
        spec = _spec(tool_permissions=["fake.module.fn"])
        with patch.object(storage_mod, "get_canvas_connection", side_effect=RuntimeError("DB down")):
            # _audit uses get_canvas_connection internally; error must be caught
            StepExecutor._audit(step, spec, "step_executed", "test")
            # No assertion needed — the test passes if no exception is raised

    # -- trust_kernel.can_execute receives correct args --

    def test_trust_kernel_receives_tier_and_step_id(self):
        calls = []

        class CaptureTK:
            def can_execute(self, risk_tier, action="run"):
                calls.append((risk_tier, action))
                return True, "ok"

        spec = _spec(tool_permissions=["fake.module.fn"], trust_tier="red")
        step = _make_step(tool="fake.module.fn")
        step["id"] = "my-step-id"
        mod = _fake_module("fn")
        with patch("importlib.import_module", return_value=mod), \
             patch.object(self.executor, "_audit"):
            self.executor.run(step, {}, spec, CaptureTK())

        assert calls == [("red", "my-step-id")]


# ---------------------------------------------------------------------------
# ACE canvas schema — every declared state must satisfy its CHECK constraint
# (hcx-ace-08: live PG constraint had drifted away from these constants)
# ---------------------------------------------------------------------------


class TestAceStateSchema:
    def _fresh_schema_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.commit()
        return conn

    def test_all_instance_states_insertable(self):
        conn = self._fresh_schema_conn()
        try:
            for i, state in enumerate(INSTANCE_STATES):
                conn.execute(
                    "INSERT INTO ace_instances (id, state) VALUES (?, ?)",
                    (f"inst-{i}", state),
                )
            conn.commit()
            n = conn.execute("SELECT COUNT(*) FROM ace_instances").fetchone()[0]
            assert n == len(INSTANCE_STATES)
        finally:
            conn.close()

    def test_all_coworker_states_insertable(self):
        conn = self._fresh_schema_conn()
        try:
            conn.execute("INSERT INTO ace_instances (id, state) VALUES ('inst-x', 'active')")
            for i, state in enumerate(COWORKER_STATES):
                conn.execute(
                    "INSERT INTO ace_coworkers (id, instance_id, role_id, state) "
                    "VALUES (?, 'inst-x', 'r', ?)",
                    (f"cw-{i}", state),
                )
            conn.commit()
            n = conn.execute("SELECT COUNT(*) FROM ace_coworkers").fetchone()[0]
            assert n == len(COWORKER_STATES)
        finally:
            conn.close()

    def test_bogus_state_rejected(self):
        conn = self._fresh_schema_conn()
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO ace_instances (id, state) VALUES ('bad', 'nonexistent_state')"
                )
                conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# repair_state_constraints — idempotent, no-op on SQLite (hcx-ace-08)
# ---------------------------------------------------------------------------


class TestRepairStateConstraints:
    def test_sqlite_is_noop_and_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.commit()
        try:
            first = repair_state_constraints(conn)
            second = repair_state_constraints(conn)
            assert first == second
            assert all(v == "skipped:sqlite" for v in first.values())
            assert set(first) == {"ace_instances", "ace_coworkers"}
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# _emit_audit — writes to ace_step_audit_log (renamed off ace_audit_log) (hcx-ace-01)
# ---------------------------------------------------------------------------


class TestEmitAudit:
    def test_writes_row_to_step_audit_log(self, monkeypatch):
        conn = sqlite3.connect(":memory:")
        # Clear the module-level "table created" cache so _ensure_audit_table
        # actually builds the table in this fresh in-memory connection.
        step_executor._AUDIT_TABLE_CREATED.clear()
        monkeypatch.setattr(step_executor, "get_connection", lambda: conn)
        try:
            _emit_audit(
                step_id="s-1",
                tool="fake.tool.fn",
                trust_tier="green",
                success=True,
                skipped=False,
                error=None,
                duration_ms=12.5,
            )
            row = conn.execute(
                "SELECT step_id, tool, trust_tier, success, skipped, duration_ms "
                "FROM ace_step_audit_log"
            ).fetchone()
            assert row is not None
            assert row[0] == "s-1"
            assert row[1] == "fake.tool.fn"
            assert row[2] == "green"
            assert row[3] == 1
            assert row[4] == 0
            assert row[5] == 12.5
        finally:
            step_executor._AUDIT_TABLE_CREATED.clear()
            conn.close()

    def test_no_legacy_ace_audit_log_table_created(self, monkeypatch):
        """The step executor must NOT create/write the canvas ace_audit_log."""
        conn = sqlite3.connect(":memory:")
        step_executor._AUDIT_TABLE_CREATED.clear()
        monkeypatch.setattr(step_executor, "get_connection", lambda: conn)
        try:
            _emit_audit(
                step_id="s-2",
                tool="t",
                trust_tier="green",
                success=True,
                skipped=False,
                error=None,
                duration_ms=1.0,
            )
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "ace_step_audit_log" in names
            assert "ace_audit_log" not in names
        finally:
            step_executor._AUDIT_TABLE_CREATED.clear()
            conn.close()
