#!/usr/bin/env python3
"""`icdev init` must never produce a project its own CLAUDE.md contradicts.

CLAUDE.md opens with *"Check `goals/manifest.md` before starting any task"* and
repeats it under How to Operate. `--minimal` skipped the entire FORGE map, so it
scaffolded a project whose instructions pointed at a file that was not there —
the agent's first action failed, on a fresh install, silently.

goals/ is not optional data. It is the Goals layer of FORGE and the entry point
of ANVIL. args/, hardprompts/ and context/ genuinely are optional under
`--minimal`: they are configuration and reference material.

Also covers `--only`, which exists so a user can pull one artifact into an
existing project without scaffolding everything around it.
"""
from __future__ import annotations

import pytest

from tools.cli import init as init_mod


def _targets(res, root) -> set:
    """Project-relative targets from an init result.

    `dst` is absolute for copied entries and relative for skipped ones, so both
    forms are normalised here rather than in every assertion.
    """
    import pathlib as _pl

    out = set()
    for a in res["actions"]:
        d = _pl.Path(a["dst"])
        try:
            out.add(d.relative_to(_pl.Path(root)).as_posix())
        except ValueError:
            out.add(d.as_posix())
    return out


# --------------------------------------------------------------------------- #
# goals/ is load-bearing
# --------------------------------------------------------------------------- #


def test_goals_is_declared_required():
    assert "data/goals" in init_mod.REQUIRED_FORGE


def test_the_other_forge_layers_stay_optional():
    """Only goals/ is load-bearing; forcing the rest would defeat --minimal."""
    for optional in ("data/args", "data/hardprompts", "data/context"):
        assert optional not in init_mod.REQUIRED_FORGE


def test_required_forge_entries_are_real_forge_map_entries():
    """A typo here would silently require nothing at all."""
    forge_sources = {pkg for pkg, _proj in init_mod.FORGE_MAP}
    for req in init_mod.REQUIRED_FORGE:
        assert req in forge_sources, f"{req} is not a FORGE_MAP source"


def test_minimal_still_copies_goals(tmp_path):
    """The regression: --minimal produced no goals/ at all."""
    res = init_mod.init_project(tmp_path, minimal=True)
    targets = _targets(res, tmp_path)
    assert "goals" in targets


def test_minimal_skips_the_optional_layers(tmp_path):
    res = init_mod.init_project(tmp_path, minimal=True)
    targets = _targets(res, tmp_path)
    assert "args" not in targets
    assert "context" not in targets


def test_full_init_copies_every_forge_layer(tmp_path):
    res = init_mod.init_project(tmp_path, minimal=False)
    targets = _targets(res, tmp_path)
    for layer in ("args", "goals", "hardprompts", "context"):
        assert layer in targets


def test_claude_md_and_goals_always_arrive_together(tmp_path):
    """CLAUDE.md without goals/ is a project that contradicts its own first line."""
    for minimal in (True, False):
        sub = tmp_path / f"m{minimal}"
        res = init_mod.init_project(sub, minimal=minimal)
        targets = _targets(res, sub)
        assert ("CLAUDE.md" in targets) == ("goals" in targets)


# --------------------------------------------------------------------------- #
# --only
# --------------------------------------------------------------------------- #


def test_only_restricts_to_one_target(tmp_path):
    res = init_mod.init_project(tmp_path, only=["CLAUDE.md"])
    targets = _targets(res, tmp_path)
    assert targets == {"CLAUDE.md"}


def test_only_accepts_several_targets(tmp_path):
    res = init_mod.init_project(tmp_path, only=["CLAUDE.md", "goals"])
    targets = _targets(res, tmp_path)
    assert targets == {"CLAUDE.md", "goals"}


def test_only_tolerates_a_trailing_separator(tmp_path):
    """`--only goals/` is what a shell tab-completion produces."""
    res = init_mod.init_project(tmp_path, only=["goals/"])
    assert _targets(res, tmp_path) == {"goals"}


def test_only_with_an_unknown_target_copies_nothing(tmp_path):
    """Better to do nothing than to silently scaffold the whole project."""
    res = init_mod.init_project(tmp_path, only=["does-not-exist"])
    assert res["actions"] == []


def test_only_is_reported_in_the_result(tmp_path):
    res = init_mod.init_project(tmp_path, only=["CLAUDE.md"])
    assert res["only"] == ["CLAUDE.md"]


def test_only_can_fetch_a_platform_file(tmp_path):
    """ICDEV is LLM-agnostic — pulling just AGENTS.md must work too."""
    res = init_mod.init_project(tmp_path, only=["AGENTS.md"])
    assert _targets(res, tmp_path) == {"AGENTS.md"}


# --------------------------------------------------------------------------- #
# The packaged CLAUDE.md is the real one
# --------------------------------------------------------------------------- #


def test_packaged_claude_md_is_not_a_stripped_template(tmp_path):
    """`icdev init` copies the repo's CLAUDE.md verbatim — no substitution.

    Worth pinning explicitly: a scaffolder that rewrites the master instruction
    file would hand users a weaker one than the project develops against.
    """
    import pathlib

    repo_claude = pathlib.Path(init_mod.__file__).resolve().parents[2] / "CLAUDE.md"
    if not repo_claude.is_file():
        pytest.skip("running from an installed package, not a checkout")

    init_mod.init_project(tmp_path, only=["CLAUDE.md"])
    written = tmp_path / "CLAUDE.md"
    assert written.is_file()
    assert written.read_bytes() == repo_claude.read_bytes()


def test_claude_md_references_goals_manifest():
    """Guards the premise of this whole file.

    If CLAUDE.md ever stops depending on goals/manifest.md, the "goals is
    required" rule should be revisited rather than silently kept.
    """
    import pathlib

    repo_claude = pathlib.Path(init_mod.__file__).resolve().parents[2] / "CLAUDE.md"
    if not repo_claude.is_file():
        pytest.skip("running from an installed package, not a checkout")
    assert "goals/manifest.md" in repo_claude.read_text(encoding="utf-8")
