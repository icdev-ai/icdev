# CUI // SP-CTI
"""ACE must actually produce output — regression tests for the step-mode deadlock.

Background
----------
``CoWorkerThread._normalise_step`` rewrites every bare-string role step into a
``icdev.tools.ace.llm_step.invoke`` call. ``StepExecutor.run`` then rejected that
call unless the dotted path appeared in ``spec.tool_permissions`` — and only 2 of
the 90 shipped role YAMLs listed it. Because ``_normalise_step`` also marks those
steps ``required: False``, every resulting ``ToolPermissionDeniedError`` was
swallowed as ``step_failed_optional`` and the co-worker still reported
``state='done'`` having produced nothing.

Roughly 35 ACE test files existed and none of them asserted that a run produces
output, which is exactly why this survived. These tests pin the behaviour end of
that gap:

1. the intrinsic LLM step runs without an explicit grant,
2. genuinely privileged tools are still refused,
3. a run where every step fails does NOT report success,
4. ``RoleStep`` dataclasses survive normalisation with their identity intact.
"""
from __future__ import annotations

import pytest

from icdev.tools.ace.step_executor import (
    CoWorkerSpec,
    StepExecutor,
    ToolPermissionDeniedError,
)


class _AllowAllKernel:
    """Trust kernel that permits everything — isolates the permission gate."""

    def can_execute(self, trust_tier, step_id):  # noqa: D102, ANN001
        return True, ""


@pytest.fixture
def spec():
    """A role with the conservative permissions 88 of 90 shipped YAMLs carry."""
    return CoWorkerSpec(
        tool_permissions=["Read", "Grep", "Glob"],
        trust_tier="green",
        name="sme-test",
    )


# ---------------------------------------------------------------------------
# 1. The intrinsic LLM step is callable without an explicit grant
# ---------------------------------------------------------------------------


def test_llm_step_runs_without_explicit_permission(spec, monkeypatch):
    """The engine's own LLM step must not require a per-role grant.

    This is the regression: with ``tool_permissions=["Read","Grep","Glob"]`` the
    step used to raise ToolPermissionDeniedError and the co-worker produced
    nothing while reporting success.
    """
    called = {}

    def _fake_invoke(**kwargs):
        called.update(kwargs)
        return "analysis complete"

    import icdev.tools.ace.llm_step as llm_step
    monkeypatch.setattr(llm_step, "invoke", _fake_invoke, raising=False)

    context: dict = {}
    result = StepExecutor().run(
        {
            "id": "analyze_requirements",
            "tool": "icdev.tools.ace.llm_step.invoke",
            "args": {"step_name": "analyze_requirements"},
            "output_var": "analyze_requirements_result",
            "required": False,
        },
        context,
        spec,
        _AllowAllKernel(),
    )

    assert result == "analysis complete"
    assert context["analyze_requirements_result"] == "analysis complete"
    assert called["step_name"] == "analyze_requirements"


def test_intrinsic_exemption_does_not_widen_other_tools(spec):
    """A tool that is not intrinsic and not granted is still refused.

    Guards against 'fixing' the deadlock by disabling the permission gate.
    """
    with pytest.raises(ToolPermissionDeniedError):
        StepExecutor().run(
            {"id": "danger", "tool": "subprocess.run", "args": {}},
            {},
            spec,
            _AllowAllKernel(),
        )


def test_both_import_namespaces_are_intrinsic():
    """The repo carries a tools/ shim and an icdev/tools/ canonical tree.

    A step normalised under one namespace must not be refused under the other.
    """
    from icdev.tools.ace.step_executor import _INTRINSIC_TOOLS

    assert "icdev.tools.ace.llm_step.invoke" in _INTRINSIC_TOOLS
    assert "tools.ace.llm_step.invoke" in _INTRINSIC_TOOLS


# ---------------------------------------------------------------------------
# 2. RoleStep dataclasses normalise correctly
# ---------------------------------------------------------------------------


def _thread_stub():
    """Minimal object exposing what _normalise_step reads, without a DB."""
    from icdev.tools.ace.coworker_thread import CoWorkerThread

    stub = object.__new__(CoWorkerThread)
    stub.instance_id = "ace-test"
    stub._ace_context = {"problem_text": "build a thing"}
    stub.spec = CoWorkerSpec(tool_permissions=[], trust_tier="green", name="x")
    stub.spec.coworker_id = "cw-1"
    stub.spec.llm_function = "code_generation"
    stub.spec.description = "test role"
    # Required: _run_step_mode passes self.trust_kernel into executor.run(), and
    # AttributeError is inside the caught tuple. Without this, every step fails
    # while *evaluating the arguments* and a patched run() is never reached — the
    # failure tests would then pass without exercising anything.
    stub.trust_kernel = _AllowAllKernel()
    return stub


def test_role_step_dataclass_keeps_its_name():
    """RoleStep must not degrade into its dataclass repr.

    It previously fell through to ``str(raw_step)``, so the step id became
    ``"RoleStep(name='analyze', tool='', params={}, condition=None)"`` — which was
    written to ace_coworkers.assigned_step and fed into the LLM prompt.
    """
    from icdev.tools.ace.role_loader import RoleStep

    step = _thread_stub()._normalise_step(RoleStep(name="analyze"))

    assert step["id"] == "analyze"
    assert step["tool"] == "icdev.tools.ace.llm_step.invoke"
    assert step["args"]["step_name"] == "analyze"
    assert "RoleStep(" not in step["id"]


def test_role_step_with_declared_tool_is_honoured():
    """A RoleStep declaring a tool/params must invoke that tool, not the LLM."""
    from icdev.tools.ace.role_loader import RoleStep

    step = _thread_stub()._normalise_step(
        RoleStep(name="scan", tool="icdev.tools.security.scanner.scan",
                 params={"target": "tools/"}, condition="$ready")
    )

    assert step["tool"] == "icdev.tools.security.scanner.scan"
    assert step["args"] == {"target": "tools/"}
    assert step["condition"] == "$ready"


def test_bare_string_step_still_works():
    """The plain-string path must be unchanged."""
    step = _thread_stub()._normalise_step("report")

    assert step["id"] == "report"
    assert step["tool"] == "icdev.tools.ace.llm_step.invoke"


def test_dict_step_passes_through_untouched():
    original = {"id": "custom", "tool": "x.y.z", "args": {"a": 1}}
    assert _thread_stub()._normalise_step(original) is original


# ---------------------------------------------------------------------------
# 3. Total failure must not report success
# ---------------------------------------------------------------------------


def test_all_steps_failing_does_not_report_done(monkeypatch):
    """A co-worker whose every step failed produced nothing — it is not 'done'.

    Individual steps stay optional; total failure is not tolerated.
    """
    from icdev.tools.ace.coworker_thread import CoWorkerThread

    stub = _thread_stub()
    states: list[str] = []
    audits: list[tuple[str, str]] = []

    stub._stop_event = type("E", (), {"is_set": lambda self: False})()
    stub._set_state = lambda s: states.append(s)
    stub._audit = lambda a, d="": audits.append((a, d))
    stub._set_assigned_step = lambda s: None
    stub._drain_inbox = lambda: None
    stub._finish_done = lambda role, detail="": states.append("done")
    stub._handle_hitl_required = lambda step, exc: True
    stub._step_count = 0
    stub._monitor_interval = 0

    def _always_fail(self, step, context, spec, trust_kernel):
        raise ToolPermissionDeniedError("denied")

    monkeypatch.setattr(StepExecutor, "run", _always_fail)

    role = type("R", (), {"steps": ["a", "b", "c"]})()
    CoWorkerThread._run_step_mode(stub, role)

    assert "failed" in states, "every step failed but the co-worker did not fail"
    assert "done" not in states, "reported success having produced nothing"
    assert any(a == "all_steps_failed" for a, _ in audits)


def test_partial_failure_still_reports_done(monkeypatch):
    """One surviving step is real output — that run is still a success."""
    from icdev.tools.ace.coworker_thread import CoWorkerThread

    stub = _thread_stub()
    states: list[str] = []

    stub._stop_event = type("E", (), {"is_set": lambda self: False})()
    stub._set_state = lambda s: states.append(s)
    stub._audit = lambda a, d="": None
    stub._set_assigned_step = lambda s: None
    stub._drain_inbox = lambda: None
    stub._finish_done = lambda role, detail="": states.append("done")
    stub._handle_hitl_required = lambda step, exc: True
    stub._step_count = 0
    stub._monitor_interval = 0

    calls = {"n": 0}

    def _fail_first(self, step, context, spec, trust_kernel):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ToolPermissionDeniedError("denied")
        return "ok"

    monkeypatch.setattr(StepExecutor, "run", _fail_first)

    role = type("R", (), {"steps": ["a", "b"]})()
    CoWorkerThread._run_step_mode(stub, role)

    assert "done" in states
    assert "failed" not in states
