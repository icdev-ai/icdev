# CUI // SP-CTI
"""Import-namespace contract for tools/cortex (cxo-doc-02).

Cortex spells most of its imports ``tools.*`` but three of them ``icdev.tools.*``
— ``api._run_single_agent`` (``llm.agent_loop``) and ``blueprint._propose_roles``
(``ace.problem_classifier``). Those three are the canonical form per CLAUDE.md,
not drift, and normalising them "for consistency" is a regression:

* In a **wheel**, ``icdev/__init__.py::_alias_tools_namespace()`` binds
  ``sys.modules["tools"] = icdev.tools``, so the two spellings are one object.
* In a **source checkout** a real top-level ``tools/`` package exists, the alias
  stands down, and the two trees load as *separate* module objects with separate
  classes and separate module-level state.

So ``icdev.tools.*`` is the only spelling that binds the same object in both
environments. For ``ace.problem_classifier`` that is load-bearing: ACE's own
modules import through ``icdev.tools.*``, and the ``tools.*`` spelling would hand
Cortex a second ``ProblemClassifierLens`` class with its own role-loader state.

See docs/features/cortex-unified-ai-layer.md, "Import namespace".
"""
from __future__ import annotations

import inspect

import pytest


# ---------------------------------------------------------------------------
# Both roots resolve
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dotted",
    [
        "tools.cortex.api",
        "icdev.tools.cortex.api",
        "tools.cortex.blueprint",
        "icdev.tools.cortex.blueprint",
    ],
)
def test_cortex_modules_import_from_both_roots(dotted):
    import importlib

    assert importlib.import_module(dotted) is not None


def test_agent_loop_symbols_are_the_same_object_from_both_roots():
    """tools/llm/agent_loop.py is a pure re-export shim — identity must hold.

    It was collapsed into one (dba8d4b59) after the physical copy it replaced
    drifted and silently served a stale loop to Cortex. If this ever fails, the
    shim has grown a body again.
    """
    from icdev.tools.llm import agent_loop as canonical
    from tools.llm import agent_loop as shim

    for name in ("run_agent_loop", "run_agent_loop_with_rubric", "AgentLoopResult"):
        assert getattr(shim, name) is getattr(canonical, name), name


# ---------------------------------------------------------------------------
# The three canonical sites stay canonical (checked in BOTH mirror copies)
# ---------------------------------------------------------------------------
def _sources(dotted_pair, func_name):
    """Source of ``func_name`` from each import root.

    In a source checkout the two roots are two physically different files (the
    mirror pair), so this covers ``tools/cortex/`` and ``icdev/tools/cortex/``
    without hard-coding either path. In a wheel they collapse to one module and
    the same body is simply checked twice.
    """
    import importlib

    return [
        inspect.getsource(getattr(importlib.import_module(dotted), func_name))
        for dotted in dotted_pair
    ]


def test_agent_loop_import_keeps_the_canonical_prefix():
    for src in _sources(("tools.cortex.api", "icdev.tools.cortex.api"), "_run_single_agent"):
        assert "from icdev.tools.llm.agent_loop import run_agent_loop" in src
        assert "from icdev.tools.llm.agent_loop import run_agent_loop_with_rubric" in src
        assert "from tools.llm.agent_loop import" not in src


def test_problem_classifier_import_keeps_the_canonical_prefix():
    for src in _sources(
        ("tools.cortex.blueprint", "icdev.tools.cortex.blueprint"), "_propose_roles"
    ):
        assert "from icdev.tools.ace.problem_classifier import ProblemClassifierLens" in src
        assert "from tools.ace.problem_classifier import" not in src


def test_ace_subsystem_itself_imports_through_the_canonical_root():
    """The premise behind the ace.problem_classifier spelling.

    Cortex must bind the same ``ProblemClassifierLens`` ACE binds. ACE reaches
    its own siblings through ``icdev.tools.*``; if that ever flips, revisit
    blueprint._propose_roles rather than leaving the two halves split.
    """
    from icdev.tools.ace import problem_classifier

    src = inspect.getsource(problem_classifier)
    assert "from icdev.tools." in src
