"""The multi-angle review template is a real diamond with real per-node scoping.

hgx-tmpl-01. The template needs no new runtime Python — these tests exist to keep
that true, by asserting the authored YAML against the functions the runner and
the agent executor will actually call:

* the DAG shape, through ``workflow_runner._prepare_dag`` (the prepared sorter
  that dispatches waves), not through a re-implementation of it here;
* each lens's offered toolset, through ``agent_executor.build_step_toolset``,
  which is the intersection the executor really performs;
* the IL boundary that separates a scanning lens from a read-only one, through
  ``agent_tool_gate.check_caller_authorized``.

All of it is DB-free and provider-free: nothing here starts a run or calls a
model, so the file stays fast enough to sit in the default suite.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from tools.studio.executors.agent_executor import (  # noqa: E402
    build_step_toolset,
    parse_bundles,
)
from tools.studio.template_linter import analyze, is_ok  # noqa: E402
from tools.studio.workflow_runner import (  # noqa: E402
    _build_agent_command,
    _max_parallel,
    _prepare_dag,
    _step_tool_path,
)

_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = _ROOT / "context" / "workflow_templates" / "multi_angle_review.yaml"

LENSES = (
    "lens_correctness",
    "lens_security",
    "lens_compliance",
    "lens_simplification",
)

#: Lenses that may execute the repo's scanners, and the tool that lets them.
EXECUTING = ("lens_security", "lens_compliance", "synthesis")
COMMAND_TOOL = "run_command"

#: No reviewer may modify the tree it is reviewing.
MUTATING_TOOLS = ("write_file", "patch_file")


@pytest.fixture(scope="module")
def template() -> dict:
    # newline="" for the same reason template_linter.load_template uses it: this
    # file is authored with LF and a CRLF-translating read would change the
    # prompts' byte content on Windows.
    with open(TEMPLATE, encoding="utf-8", newline="") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def steps(template: dict) -> list[dict]:
    return template["steps"]


@pytest.fixture(scope="module")
def by_id(steps: list[dict]) -> dict:
    return {step["id"]: step for step in steps}


# ── The template is well-formed ────────────────────────────────────────────

def test_template_passes_the_linter(steps: list[dict]) -> None:
    info = analyze(steps)
    assert is_ok(info), info


def test_linter_cli_covers_the_gallery_directory() -> None:
    """The template is in a directory the linter's own CLI actually walks.

    Without this the lint above would pass while
    ``python tools/studio/template_linter.py --check`` never opened the file.
    """
    from tools.studio.template_linter import TEMPLATE_DIRS, _template_paths

    assert TEMPLATE.parent in TEMPLATE_DIRS
    assert TEMPLATE in _template_paths()


# ── Fan-out, barrier, concurrency ──────────────────────────────────────────

def test_dag_is_a_diamond(steps: list[dict]) -> None:
    """One root, one wave of four, one join — read off the prepared sorter."""
    sorter = _prepare_dag(steps)
    waves = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        waves.append(ready)
        for step_id in ready:
            sorter.done(step_id)

    assert waves == [["scope"], sorted(LENSES), ["synthesis"]]


def test_fan_out_wave_has_a_slot_per_lens(template: dict) -> None:
    """max_parallel must cover the wave or the diamond executes serially.

    The runner's default is 1, which is what keeps every other template in the
    tree sequential — this one has to opt in.
    """
    assert _max_parallel(template) >= len(LENSES)


def test_synthesis_waits_for_every_lens(by_id: dict) -> None:
    assert sorted(by_id["synthesis"]["depends_on"]) == sorted(LENSES)


def test_a_failed_lens_does_not_cancel_the_synthesis(by_id: dict) -> None:
    """Three angles is a thinner review, not no review.

    ``_block_downstream`` only cascades from a step whose ``required`` is true,
    so the lenses must declare it false explicitly — the runner's default is
    true.
    """
    for lens in LENSES:
        assert by_id[lens].get("required") is False, lens
    assert by_id["scope"].get("required") is True
    assert by_id["synthesis"].get("required") is True


# ── Per-node capability scoping ────────────────────────────────────────────

def test_every_step_is_an_agent_node(steps: list[dict]) -> None:
    for step in steps:
        assert step["node_type"] == "agent", step["id"]
        assert _step_tool_path(step) == "tools/studio/executors/agent_executor.py"


def _offered(step: dict) -> set[str]:
    """Tool names this step's declared bundles actually resolve to."""
    _tools, handlers, unavailable = build_step_toolset(
        parse_bundles(step["agent_tools"]), str(_ROOT)
    )
    # A bundle promising a tool an agent node cannot be handed is an authoring
    # error the executor reports rather than swallows; this template must have
    # none.
    assert unavailable == [], (step["id"], unavailable)
    return set(handlers)


def test_read_only_lenses_cannot_execute_commands(by_id: dict) -> None:
    for step_id in ("scope", "lens_correctness", "lens_simplification"):
        assert COMMAND_TOOL not in _offered(by_id[step_id]), step_id


def test_scanning_lenses_can_execute_commands(by_id: dict) -> None:
    for step_id in EXECUTING:
        assert COMMAND_TOOL in _offered(by_id[step_id]), step_id


def test_no_reviewer_can_modify_the_tree_it_reviews(steps: list[dict]) -> None:
    for step in steps:
        offered = _offered(step)
        for tool in MUTATING_TOOLS:
            assert tool not in offered, (step["id"], tool)


def test_allowlists_reach_the_executor_argv(by_id: dict) -> None:
    """What the template declares is what the subprocess is actually told."""
    cmd = _build_agent_command(by_id["lens_simplification"], "proj", "run-x")
    assert cmd[cmd.index("--agent-tools") + 1] == "worktree_read"

    cmd = _build_agent_command(by_id["lens_security"], "proj", "run-x")
    assert cmd[cmd.index("--agent-tools") + 1] == "worktree_read,terminal"


def test_the_command_tool_is_il_gated(by_id: dict) -> None:
    """The scoping has teeth: AGENT-WF-001 holds run_command above IL4.

    So the two scanning lenses are handed the read-only subset below IL5 and say
    so in ``tools_refused`` — the template documents that, and this pins it.
    """
    from tools.studio.executors import agent_tool_gate

    for impact_level, authorized in (("IL4", False), ("IL5", True)):
        caller = {"impact_level": impact_level, "roles": (), "source": "test"}
        try:
            agent_tool_gate.check_caller_authorized(COMMAND_TOOL, caller=caller)
            decided = True
        except agent_tool_gate.AgentToolGateError:
            decided = False
        assert decided is authorized, impact_level

    # The read-only lenses' tools clear the baseline caller, so a lens confined
    # to them is never left with an empty toolbox.
    caller = {"impact_level": "IL4", "roles": (), "source": "test"}
    for tool in sorted(_offered(by_id["lens_simplification"])):
        agent_tool_gate.check_caller_authorized(tool, caller=caller)


# ── LLM-agnostic ───────────────────────────────────────────────────────────

def test_steps_route_by_function_and_never_name_a_model(steps: list[dict]) -> None:
    """Every step names a routing FUNCTION, and one declared in llm_config.

    An undeclared function is not an error at run time — it silently falls back
    to ``routing.default``, which is exactly the drift this pins down.
    """
    config_path = _ROOT / "args" / "llm_config.yaml"
    with open(config_path, encoding="utf-8", newline="") as handle:
        routing = (yaml.safe_load(handle) or {}).get("routing", {})

    for step in steps:
        function = step.get("llm_function")
        assert function, step["id"]
        assert function in routing, (step["id"], function)
        assert "model" not in step, step["id"]


def test_governance_profiles_resolve(steps: list[dict]) -> None:
    """A named profile is fail-closed — a typo fails the step, so pin the names."""
    governance = pytest.importorskip("tools.cortex.governance")

    for step in steps:
        profile = step.get("governance_profile")
        if profile:
            assert governance.resolve_profile(profile)
