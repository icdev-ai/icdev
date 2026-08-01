# CUI // SP-CTI
"""Mirror-parity guard for the ACE package.

The ACE runtime ships as byte-identical copies under BOTH the canonical
package tree ``icdev/tools/ace/`` and the backward-compat shim tree
``tools/ace/``. Companion sync keeps them in lockstep; this test is the
regression fence that catches drift between the two trees (which previously
let ``agent_tools.py`` / ``eval_runner.py`` diverge and left
``persona_generator.py`` / ``persona_query.py`` present in only one tree).

Two invariants are enforced:

1. **File parity** — every ``tools/ace/*.py`` file is byte-identical to its
   ``icdev/tools/ace/*.py`` counterpart (and vice-versa), excluding the
   package ``__init__.py`` which only exists on the canonical side.
2. **Import parity** — every ACE module imports successfully under BOTH the
   ``tools.ace.<m>`` and ``icdev.tools.ace.<m>`` namespaces, and the modules
   central to this reconciliation expose their public API under both.

Note on ``from tools.ace.X import ...``: the ``tools`` package is a shim that
redirects to ``icdev.tools``. Because the files are byte-identical, importing
either namespace resolves to equivalent (but not necessarily *identical*)
module objects — so we assert importability and attribute presence, never
object identity.
"""
from __future__ import annotations

import filecmp
import importlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL = _REPO_ROOT / "icdev" / "tools" / "ace"
_SHIM = _REPO_ROOT / "tools" / "ace"

# The shim tree carries no package __init__.py (it is a namespace re-export);
# it is the only legitimate asymmetry.
_INIT_ONLY_CANONICAL = {"__init__.py"}

# Public API each reconciled module must expose under BOTH namespaces.
_KEY_ATTRS = {
    "agent_tools": ["AgentToolRegistry"],
    "eval_runner": ["EvalRunResult", "load_suite", "run_eval", "run_all"],
    "persona_query": ["query_persona"],
    "persona_generator": ["get_or_generate_persona"],
}


def _ace_module_names() -> list[str]:
    return sorted(
        p.stem
        for p in _CANONICAL.glob("*.py")
        if p.name not in _INIT_ONLY_CANONICAL
    )


def test_no_module_missing_from_either_tree():
    canonical = {p.name for p in _CANONICAL.glob("*.py")} - _INIT_ONLY_CANONICAL
    shim = {p.name for p in _SHIM.glob("*.py")}
    missing_from_shim = sorted(canonical - shim)
    missing_from_canonical = sorted(shim - canonical)
    assert not missing_from_shim, f"present in icdev/ but missing from tools/: {missing_from_shim}"
    assert not missing_from_canonical, (
        f"present in tools/ but missing from icdev/: {missing_from_canonical}"
    )


@pytest.mark.parametrize("module_name", _ace_module_names())
def test_ace_module_files_are_byte_identical(module_name: str):
    canonical = _CANONICAL / f"{module_name}.py"
    shim = _SHIM / f"{module_name}.py"
    assert canonical.exists(), f"missing canonical file: {canonical}"
    assert shim.exists(), f"missing shim file: {shim}"
    assert filecmp.cmp(str(canonical), str(shim), shallow=False), (
        f"ACE mirror drift: tools/ace/{module_name}.py differs from "
        f"icdev/tools/ace/{module_name}.py — re-sync the mirror"
    )


@pytest.mark.parametrize("module_name", _ace_module_names())
def test_ace_module_imports_under_both_namespaces(module_name: str):
    canonical = importlib.import_module(f"icdev.tools.ace.{module_name}")
    shim = importlib.import_module(f"tools.ace.{module_name}")
    assert canonical is not None
    assert shim is not None


@pytest.mark.parametrize("module_name,attrs", sorted(_KEY_ATTRS.items()))
def test_reconciled_modules_expose_public_api(module_name: str, attrs: list[str]):
    canonical = importlib.import_module(f"icdev.tools.ace.{module_name}")
    shim = importlib.import_module(f"tools.ace.{module_name}")
    for attr in attrs:
        assert hasattr(canonical, attr), f"icdev.tools.ace.{module_name}.{attr} missing"
        assert hasattr(shim, attr), f"tools.ace.{module_name}.{attr} missing"
