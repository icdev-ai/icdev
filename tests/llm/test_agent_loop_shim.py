"""Regression tests for the ``tools.llm.agent_loop`` compatibility shim.

``tools/llm/agent_loop.py`` was historically a physically-separate copy of
``icdev/tools/llm/agent_loop.py`` that drifted out of sync — the stale copy
lacked ``run_agent_loop_with_rubric`` and other post-June additions, so runtime
call sites doing ``from tools.llm.agent_loop import ...`` silently bound the
outdated implementation. These tests pin the shim to the canonical module.
"""
import importlib


def test_run_agent_loop_is_same_object():
    """The shim re-exports the *same* function object as the canonical module."""
    from icdev.tools.llm.agent_loop import run_agent_loop as canonical
    from tools.llm.agent_loop import run_agent_loop as shim

    assert shim is canonical


def test_shim_exposes_run_agent_loop_with_rubric():
    """The stale copy lacked this symbol entirely; the shim must expose it."""
    import tools.llm.agent_loop as shim
    from icdev.tools.llm.agent_loop import run_agent_loop_with_rubric as canonical

    assert hasattr(shim, "run_agent_loop_with_rubric")
    assert shim.run_agent_loop_with_rubric is canonical


def test_importlib_import_module_works():
    """``importlib.import_module`` on the shim path must resolve cleanly."""
    mod = importlib.import_module("tools.llm.agent_loop")
    assert mod is not None
    assert hasattr(mod, "run_agent_loop")


def test_rubric_and_result_symbols_are_canonical():
    """Rubric/result classes must be identical objects across both import paths."""
    import icdev.tools.llm.agent_loop as canonical
    import tools.llm.agent_loop as shim

    for name in (
        "DONE",
        "ResultSubtype",
        "AgentLoopResult",
        "AgentLoopUnsupported",
        "RubricVerdict",
        "RubricGrade",
        "RubricLoopResult",
        "run_staged_agent_loop",
        "LoopStage",
        "StagedLoopResult",
    ):
        assert getattr(shim, name) is getattr(canonical, name), name


def test_private_helpers_re_exported():
    """Underscore helpers imported by tests must resolve through the shim too."""
    import icdev.tools.llm.agent_loop as canonical
    import tools.llm.agent_loop as shim

    for name in (
        "_load_budget_defaults",
        "_build_read_only_set",
        "_retrieve_memory_context",
    ):
        assert getattr(shim, name) is getattr(canonical, name), name
