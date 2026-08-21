# CUI // SP-CTI
"""xit-decl-02 — ``tools.X`` and ``icdev.tools.X`` are ONE module object.

This is the test that would have caught PR #1542: a fix applied to one tree
while the other kept running. Red-first: at the merge base the two spellings
load two files and every identity assertion below fails.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Modules chosen because each is imported under BOTH spellings somewhere in
# the tree, and because each holds module-level state a second copy would split.
IDENTITY_CASES = [
    "db.storage",                     # connection + RLS state
    "genesis.reflexes.kanban",        # the #1542 case
    "kanban.task_factory",            # the canonical board seeder
    "config.component_registry",      # BASE_DIR + registry cache
    "config.core_profile",            # BASE_DIR
    "llm.config_path",                # the llm-config resolver
    "quality.citation_grounding",     # TRUST
    "cortex.governance",              # "tools" vs "icdev.tools" branch in comments
]


@pytest.mark.parametrize("dotted", IDENTITY_CASES)
def test_one_object_under_both_names(dotted):
    a = importlib.import_module(f"tools.{dotted}")
    b = importlib.import_module(f"icdev.tools.{dotted}")
    assert a is b, f"tools.{dotted} and icdev.tools.{dotted} are two objects"


@pytest.mark.parametrize("pkg", [
    "db", "kanban", "genesis", "llm", "config", "quality", "cortex", "rag",
    "knowledge_graph", "memory", "agents", "ci", "workflow", "awareness",
])
def test_every_kernel_package_is_one_object(pkg):
    a = importlib.import_module(f"tools.{pkg}")
    b = importlib.import_module(f"icdev.tools.{pkg}")
    assert a is b, pkg


def test_physical_file_is_the_checkout_not_the_packaged_copy():
    """The direction: icdev.tools.* importers now run THIS checkout's file."""
    import icdev.tools.config.core_profile as cp

    assert Path(cp.__file__).resolve().parent.parent.parent == REPO_ROOT
    # and so a self-rooting BASE_DIR lands on the repo root, not on <repo>/icdev
    assert Path(cp.BASE_DIR).resolve() == REPO_ROOT


def test_monkeypatch_on_either_spelling_is_seen_by_the_other(monkeypatch):
    import icdev.tools.kanban.task_factory as canonical
    import tools.kanban.task_factory as shimmed

    sentinel = object()
    monkeypatch.setattr("tools.kanban.task_factory.create_tasks", sentinel)
    assert canonical.create_tasks is sentinel
    monkeypatch.setattr("icdev.tools.kanban.task_factory.create_tasks", None)
    assert shimmed.create_tasks is None


@pytest.mark.parametrize("dotted", ["llm.agent_loop", "billing.tier"])
def test_backcompat_shims_are_one_object_too_and_it_is_the_real_one(dotted):
    """tools/<x>.py is a tiny shim; BOTH names resolve to the real mirror module.

    The parent package is aliased, so its child ATTRIBUTE can hold only one
    module; if the shim loaded as its own module, a string-path monkeypatch
    (which walks attributes) and a `from icdev.tools.x import f` (which reads
    sys.modules) would disagree about which object is live.
    """
    real = importlib.import_module(f"icdev.tools.{dotted}")
    via_shim = importlib.import_module(f"tools.{dotted}")
    assert via_shim is real
    assert "icdev" in Path(real.__file__).resolve().relative_to(REPO_ROOT).parts
    parent = importlib.import_module("icdev.tools." + dotted.rsplit(".", 1)[0])
    assert getattr(parent, dotted.rsplit(".", 1)[1]) is real


def test_mirror_only_and_shim_cases_resolve_in_the_mirror_explicitly(tmp_path):
    """A module that exists only under icdev/tools/, or whose tools/ file is a
    shim, must resolve to the MIRROR file even when its parent package is
    aliased (whose __path__ then points at tools/<pkg>)."""
    from icdev.core.shim import IcdevToolsAliasFinder

    tools_dir = tmp_path / "tools"
    mirror_dir = tmp_path / "icdev" / "tools"
    (tools_dir / "pkg").mkdir(parents=True)
    (mirror_dir / "pkg").mkdir(parents=True)
    for d in (tools_dir, tools_dir / "pkg", mirror_dir, mirror_dir / "pkg"):
        (d / "__init__.py").write_text("", encoding="utf-8")
    (mirror_dir / "pkg" / "only_here.py").write_text("X = 1\n", encoding="utf-8")
    (mirror_dir / "pkg" / "real.py").write_text("REAL = True\n" * 40, encoding="utf-8")
    (tools_dir / "pkg" / "real.py").write_text(
        "from icdev.tools.pkg.real import *  # noqa: F401,F403\n", encoding="utf-8"
    )
    finder = IcdevToolsAliasFinder(tools_dir, mirror_dir)

    spec = finder.find_spec("icdev.tools.pkg.only_here")
    assert spec is not None and Path(spec.origin).resolve() == (mirror_dir / "pkg" / "only_here.py").resolve()
    spec = finder.find_spec("icdev.tools.pkg.real")
    assert spec is not None and Path(spec.origin).resolve() == (mirror_dir / "pkg" / "real.py").resolve()
    assert "icdev.tools.pkg.real" in finder.fell_through
    assert finder.find_spec("icdev.tools.pkg.nowhere") is None
    assert finder.find_spec("icdev.tools") is None
    assert finder.find_spec("somewhere.else") is None


def test_finder_refuses_to_install_outside_a_checkout(tmp_path):
    from icdev.core import shim

    foreign = tmp_path / "someproject" / "tools"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text("", encoding="utf-8")
    assert shim.install(foreign / "__init__.py") is None


@pytest.mark.parametrize("spelling", ["tools.kanban.cli", "icdev.tools.kanban.cli"])
def test_python_dash_m_works_under_both_spellings(spelling):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "ICDEV_IDENTITY_GUARD": "0"}
    proc = subprocess.run(
        [sys.executable, "-m", spelling, "--help"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "usage:" in proc.stdout
