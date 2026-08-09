"""Conditional edges and downstream cancellation in the Studio runner (hgx-cond-01).

Two routing primitives the DAG runtime did not have, both exercised here through
the REAL `_worker` with persistence stubbed:

  * `when:` on a step — the {field, operator, value} DSL `automation_builder`
    already owns, evaluated against the predecessor's recorded result. A step
    whose condition does not hold records the existing `skipped` status with the
    unmet condition as its reason.
  * a failed required step cancels its transitive descendants instead of letting
    them run against a precondition that is known to be broken — ported from
    `tools/agent/team_orchestrator.py::_block_downstream`.

The load-bearing guarantee is the ABSENCE case: a template with no `when` key
must execute exactly as it did before either primitive existed, which
`test_no_when_key_*` asserts step-for-step.
"""
# CUI // SP-CTI

from __future__ import annotations

import importlib
import json
import queue
from pathlib import Path

import pytest

# The root `tools.` namespace is a shim over `icdev.tools.`, so the module OBJECT
# an import binds is not necessarily the one a string-form patch would reach.
# Resolve it once and patch attributes on that object (see test_workflow_parallel).
runner = importlib.import_module("tools.studio.workflow_runner")
linter = importlib.import_module("tools.studio.template_linter")


# ── Harness ────────────────────────────────────────────────

class _Recorder:
    """Records which steps actually executed, and fakes each one's outcome.

    A step declares its outcome in the template itself: `_test_status` (default
    "success") and `_test_stdout`. Unknown keys are ignored by the runner, so a
    fixture stays a plain template.
    """

    def __init__(self) -> None:
        self.order: list[str] = []

    def exec_step(self, step: dict, project_id: str, run_id: str = "") -> dict:
        step_id = step["id"]
        self.order.append(step_id)
        status = str(step.get("_test_status", "success"))
        stdout = step.get("_test_stdout")
        return {
            "step_id": step_id,
            "step_name": step.get("name", step_id),
            "tool": step.get("tool", ""),
            "status": status,
            "stdout": stdout if stdout is None else str(stdout),
            "stderr": None,
            "exit_code": 0 if status == "success" else 1,
            "duration_ms": 1,
        }


class _Run:
    """The outcome of one `_worker` pass, in the shapes the assertions want."""

    def __init__(self, recorder: _Recorder, events: list[dict], statuses: list[tuple]) -> None:
        self.executed = recorder.order
        self.events = events
        # (status, summary_json) per _update_run_status call, in order.
        self.status_calls = statuses

    def step_done(self, step_id: str) -> dict:
        for event in self.events:
            if event.get("type") == "step_done" and event.get("step_id") == step_id:
                return event
        raise AssertionError(f"no step_done event for {step_id!r}: {self.events}")

    def status_of(self, step_id: str) -> str:
        return self.step_done(step_id)["status"]

    @property
    def overall(self) -> str:
        return self.status_calls[-1][0]

    @property
    def summary(self) -> dict:
        return json.loads(self.status_calls[-1][1])


def _run(monkeypatch: pytest.MonkeyPatch, template_yaml: str) -> _Run:
    """Execute a template through the real `_worker`, with persistence stubbed."""
    recorder = _Recorder()
    statuses: list[tuple] = []

    monkeypatch.setattr(
        runner, "_update_run_status",
        lambda run_id, status, summary_json=None: statuses.append((status, summary_json)),
    )
    monkeypatch.setattr(runner, "_update_step_record", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_remember_canvas", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_remember_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_load_prior_steps", lambda run_id: {})
    monkeypatch.setattr(runner, "_notify_approval_gate", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "_create_step_record",
        lambda run_id, step_id, *a, **k: f"sr-{step_id}",
    )
    monkeypatch.setattr(runner, "_exec_step", recorder.exec_step)

    run_queue: queue.Queue = queue.Queue(maxsize=500)
    runner._worker(
        "run-test", "wf-test", {"template_yaml": template_yaml, "name": "test"},
        "default", run_queue,
    )

    events = []
    while True:
        try:
            events.append(run_queue.get_nowait())
        except queue.Empty:
            break
    return _Run(recorder, events, statuses)


# ── The absence case: no `when` key anywhere ───────────────

_PLAIN = """
name: plain
steps:
  - id: a
    tool: tools/x.py
  - id: b
    tool: tools/x.py
    depends_on: [a]
  - id: c
    tool: tools/x.py
    depends_on: [b]
"""


def test_no_when_key_runs_every_step(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, _PLAIN)
    assert result.executed == ["a", "b", "c"]
    assert result.overall == "success"
    assert result.summary["success"] == 3
    assert result.summary["skipped"] == 0


def test_no_when_key_emits_no_reason_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """`reason` rides only on a step that did not run — nothing else gains a key."""
    result = _run(monkeypatch, _PLAIN)
    for step_id in ("a", "b", "c"):
        assert "reason" not in result.step_done(step_id)


# ── `when` on a step ───────────────────────────────────────

_GUARDED = """
name: guarded
steps:
  - id: scan
    tool: tools/x.py
    _test_status: success
  - id: guarded
    tool: tools/x.py
    depends_on: [scan]
    when:
      - field: status
        operator: equals
        value: {expected}
"""


def test_true_when_runs_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, _GUARDED.format(expected="success"))
    assert result.executed == ["scan", "guarded"]
    assert result.status_of("guarded") == "success"


def test_false_when_skips_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, _GUARDED.format(expected="failed"))
    assert result.executed == ["scan"], "a step whose condition failed must not run"
    done = result.step_done("guarded")
    assert done["status"] == "skipped"
    assert "Condition not met" in done["reason"]
    # The unmet condition is named, so the skip is explainable without a re-run.
    assert "status" in done["reason"] and "success" in done["reason"]
    assert result.summary["skipped"] == 1


def test_a_skipped_step_does_not_fail_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A condition that did not hold is a route not taken, not a failure."""
    result = _run(monkeypatch, _GUARDED.format(expected="failed"))
    assert result.overall == "success"


def test_when_accepts_a_single_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    template = """
name: single
steps:
  - id: scan
    tool: tools/x.py
  - id: guarded
    tool: tools/x.py
    depends_on: [scan]
    when:
      field: status
      operator: equals
      value: success
"""
    assert _run(monkeypatch, template).executed == ["scan", "guarded"]


def test_when_value_typed_as_a_yaml_int(monkeypatch: pytest.MonkeyPatch) -> None:
    """`value: 0` is an int to YAML and a str to the DSL — the runner coerces it."""
    template = """
name: numeric
steps:
  - id: scan
    tool: tools/x.py
    _test_stdout: '{"findings": 4}'
  - id: guarded
    tool: tools/x.py
    depends_on: [scan]
    when:
      - field: output.findings
        operator: greater_than
        value: 0
"""
    assert _run(monkeypatch, template).executed == ["scan", "guarded"]


def test_a_malformed_when_degrades_to_unconditional(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner must not die mid-run on an authoring mistake — the linter catches it."""
    template = """
name: malformed
steps:
  - id: scan
    tool: tools/x.py
  - id: guarded
    tool: tools/x.py
    depends_on: [scan]
    when: "status == success"
"""
    assert _run(monkeypatch, template).executed == ["scan", "guarded"]


def test_an_unrecorded_predecessor_reads_as_unmet_not_an_error() -> None:
    step = {
        "id": "b",
        "depends_on": ["never_ran"],
        "when": [{"field": "status", "operator": "equals", "value": "success"}],
    }
    met, reason = runner._evaluate_when(step, {})
    assert met is False
    assert "Condition not met" in reason


def test_when_routes_on_the_predecessors_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    template = """
name: output-routed
steps:
  - id: scan
    tool: tools/x.py
    _test_stdout: '{{"risk": {risk}}}'
  - id: escalate
    tool: tools/x.py
    depends_on: [scan]
    when:
      - field: output.risk
        operator: greater_than
        value: "7"
"""
    assert _run(monkeypatch, template.format(risk=9)).executed == ["scan", "escalate"]
    assert _run(monkeypatch, template.format(risk=2)).executed == ["scan"]


def test_when_addresses_a_named_predecessor_of_a_join(monkeypatch: pytest.MonkeyPatch) -> None:
    """`steps.<id>.<field>` picks one branch of a join, not just the first edge."""
    template = """
name: join
steps:
  - id: left
    tool: tools/x.py
  - id: right
    tool: tools/x.py
    _test_stdout: '{"verdict": "block"}'
  - id: gate
    tool: tools/x.py
    depends_on: [left, right]
    when:
      - field: steps.right.output.verdict
        operator: equals
        value: block
"""
    assert "gate" in _run(monkeypatch, template).executed


def test_all_conditions_must_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    template = """
name: conjunction
steps:
  - id: scan
    tool: tools/x.py
    _test_stdout: '{"risk": 2}'
  - id: guarded
    tool: tools/x.py
    depends_on: [scan]
    when:
      - field: status
        operator: equals
        value: success
      - field: output.risk
        operator: greater_than
        value: "7"
"""
    assert _run(monkeypatch, template).executed == ["scan"]


def test_dependents_of_a_skipped_step_evaluate_their_own_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `when`-skip does not cancel anything: each dependent decides for itself."""
    template = """
name: chained-conditions
steps:
  - id: scan
    tool: tools/x.py
    _test_status: success
  - id: remediate
    tool: tools/x.py
    depends_on: [scan]
    when:
      - field: status
        operator: not_equals
        value: success
  - id: after_remediation
    tool: tools/x.py
    depends_on: [remediate]
    when:
      - field: status
        operator: equals
        value: success
  - id: note_the_skip
    tool: tools/x.py
    depends_on: [remediate]
    when:
      - field: status
        operator: equals
        value: skipped
  - id: unconditional
    tool: tools/x.py
    depends_on: [remediate]
"""
    result = _run(monkeypatch, template)
    assert result.status_of("remediate") == "skipped"
    # Each dependent read `remediate`'s recorded result and decided separately.
    assert result.status_of("after_remediation") == "skipped"
    assert "note_the_skip" in result.executed
    assert "unconditional" in result.executed, (
        "a dependent with no `when` is unconditional — a skipped parent must not "
        "silently cancel it"
    )


# ── Downstream cancellation ────────────────────────────────

_CASCADE = """
name: cascade
steps:
  - id: build
    tool: tools/x.py
    _test_status: {status}
    {required}
  - id: test
    tool: tools/x.py
    depends_on: [build]
  - id: deploy
    tool: tools/x.py
    depends_on: [test]
  - id: unrelated
    tool: tools/x.py
    depends_on: [build]
"""


def test_failed_required_step_cancels_its_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(monkeypatch, _CASCADE.format(status="failed", required=""))
    assert result.executed == ["build"], (
        "no descendant may run against a precondition known to be broken"
    )
    for step_id in ("test", "deploy", "unrelated"):
        done = result.step_done(step_id)
        assert done["status"] == "skipped"
        assert "Cancelled" in done["reason"] and "build" in done["reason"]
    assert result.overall == "failed"


def test_cancellation_cascades_transitively(monkeypatch: pytest.MonkeyPatch) -> None:
    """`deploy` depends on `build` only through `test` — one level is not enough."""
    result = _run(monkeypatch, _CASCADE.format(status="failed", required=""))
    assert "deploy" not in result.executed
    assert "build" in result.step_done("deploy")["reason"]


def test_a_timed_out_step_cancels_its_descendants(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, _CASCADE.format(status="timeout", required=""))
    assert result.executed == ["build"]
    assert result.overall == "failed"


def test_an_optional_step_failing_cancels_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`required: false` already meant "does not fail the run" — nor does it block."""
    result = _run(monkeypatch, _CASCADE.format(status="failed", required="required: false"))
    # Set, not list: `unrelated` and `deploy` are siblings in the topological
    # order, and which of the two goes first is not what this asserts.
    assert set(result.executed) == {"build", "test", "deploy", "unrelated"}
    assert result.overall == "success"


def test_a_successful_step_cancels_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, _CASCADE.format(status="success", required=""))
    assert set(result.executed) == {"build", "test", "deploy", "unrelated"}
    assert result.overall == "success"


def test_failure_routes_to_a_remediation_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The routing this feature exists for: fail -> remediate, everything else cancelled.

    `remediate` declares `when`, so it is exempt from the cascade — being reached
    by a failed predecessor is exactly what it asked to be routed on. `publish`
    declares none, so it is cancelled.
    """
    template = """
name: remediation
steps:
  - id: scan
    tool: tools/x.py
    _test_status: failed
  - id: remediate
    tool: tools/x.py
    depends_on: [scan]
    when:
      - field: status
        operator: not_equals
        value: success
  - id: publish
    tool: tools/x.py
    depends_on: [scan]
"""
    result = _run(monkeypatch, template)
    assert "remediate" in result.executed, (
        "a conditional step is the remediation branch — cancelling it would make "
        "fail -> remediate unreachable"
    )
    assert "publish" not in result.executed
    assert "Cancelled" in result.step_done("publish")["reason"]


def test_a_conditional_step_shields_only_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cascade stops at a conditional step; what that step records governs on."""
    template = """
name: shielded
steps:
  - id: scan
    tool: tools/x.py
    _test_status: failed
  - id: remediate
    tool: tools/x.py
    depends_on: [scan]
    when:
      - field: status
        operator: not_equals
        value: success
  - id: verify
    tool: tools/x.py
    depends_on: [remediate]
"""
    result = _run(monkeypatch, template)
    assert result.executed == ["scan", "remediate", "verify"]


# ── Template linter ────────────────────────────────────────

def test_linter_accepts_a_well_formed_when() -> None:
    steps = [
        {"id": "scan"},
        {
            "id": "remediate",
            "depends_on": ["scan"],
            "when": [{"field": "status", "operator": "not_equals", "value": "success"}],
        },
    ]
    assert linter.validate_when(steps[1]) == []
    assert linter.is_ok(linter.analyze(steps))


def test_linter_rejects_an_unknown_operator() -> None:
    step = {"id": "b", "depends_on": ["a"], "when": [{"field": "status", "operator": "matches"}]}
    errors = linter.validate_when(step)
    assert len(errors) == 1
    assert "matches" in errors[0]


def test_linter_operators_come_from_the_shared_dsl() -> None:
    """The linter must not carry its own operator vocabulary."""
    from tools.studio.automation_builder import CONDITION_OPERATORS

    assert linter.VALID_CONDITION_OPERATORS == {op["id"] for op in CONDITION_OPERATORS}


def test_linter_rejects_a_condition_with_no_field() -> None:
    step = {"id": "b", "depends_on": ["a"], "when": [{"operator": "is_empty"}]}
    assert any("field" in e for e in linter.validate_when(step))


def test_linter_rejects_a_non_mapping_when() -> None:
    assert linter.validate_when({"id": "b", "depends_on": ["a"], "when": "status == ok"})


def test_linter_rejects_when_on_a_root_step() -> None:
    """A root step has no predecessor, so every field would resolve empty."""
    step = {"id": "a", "when": [{"field": "status", "operator": "equals", "value": "success"}]}
    assert any("depends_on" in e for e in linter.validate_when(step))


def test_linter_reports_bad_when_per_step() -> None:
    steps = [
        {"id": "a"},
        {"id": "b", "depends_on": ["a"], "when": [{"field": "status", "operator": "nope"}]},
    ]
    info = linter.analyze(steps)
    assert info["bad_when"] and info["bad_when"][0][0] == "b"
    assert not linter.is_ok(info)
    # …but the GRAPH is clean, so auto_fix must not try to wire edges to fix it.
    assert linter._connectivity_ok(info)
    _patched, changes = linter.auto_fix(steps)
    assert changes == []


def test_linter_ignores_a_step_with_no_when() -> None:
    steps = [{"id": "a"}, {"id": "b", "depends_on": ["a"]}]
    info = linter.analyze(steps)
    assert info["bad_when"] == []
    assert linter.is_ok(info)


def test_no_shipped_template_declares_a_bad_when() -> None:
    """Every template in the tree, so the new check cannot land already red."""
    import yaml

    repo_root = Path(__file__).resolve().parents[2]
    template_dirs = (
        repo_root / "args" / "workflow_templates",
        repo_root / "context" / "workflow_templates",
    )
    checked = 0
    for directory in template_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            with open(path, encoding="utf-8", newline="") as handle:
                data = yaml.safe_load(handle) or {}
            for step in data.get("steps", []) or []:
                assert linter.validate_when(step) == [], f"{path.name}:{step.get('id')}"
            checked += 1
    assert checked, "no shipped templates found — the sweep proved nothing"
