#!/usr/bin/env python3
"""ICDEV is LLM-agnostic — this proves it, and catches the drift. CUI // SP-CTI.

The claim was true of the REPO and false of the WHEEL. All ten non-Claude
platform instruction files — AGENTS.md, GEMINI.md, .cursor/rules/icdev.mdc,
.github/copilot-instructions.md and the rest — were tracked in git, and **none
of them shipped**. `pip install icdev && icdev init` produced a Claude-only
project: CLAUDE.md and .claude/, nothing else.

Nothing detected it because the platform list lived in four places that could
not disagree loudly:

  * coherence_checker.check_karpathy_sync  — audits the files in the repo
  * installer/prebuild_bootstrap.py        — decides what enters the wheel
  * cli/init.py                            — decides what lands in a project
  * dx/instruction_generator.py            — writes them

Only the first knew about all ten. These tests bind the other three to
`tools/dx/ai_platforms.py`, so adding a platform in one place and forgetting the
others fails here instead of silently shipping a Claude-only wheel.
"""
from __future__ import annotations

import subprocess

import pytest

from tools.dx import ai_platforms as ap


# --------------------------------------------------------------------------- #
# The list itself
# --------------------------------------------------------------------------- #


def test_ten_platforms_are_declared():
    assert len(ap.AI_PLATFORM_FILES) == 10


def test_platform_ids_are_unique():
    ids = ap.platform_ids()
    assert len(set(ids)) == len(ids)


def test_paths_are_unique():
    paths = ap.platform_paths()
    assert len(set(paths)) == len(paths)


def test_claude_md_is_not_in_the_platform_list():
    """CLAUDE.md is the SOURCE the others are generated from, not a peer.

    Listing it here would double-copy it (it already has its own bootstrap
    entry) and imply it is one platform among ten.
    """
    assert "CLAUDE.md" not in ap.platform_paths()


@pytest.mark.parametrize("platform,rel", ap.AI_PLATFORM_FILES)
def test_every_declared_platform_file_exists_in_the_repo(platform, rel):
    assert (ap.REPO_ROOT / rel).is_file(), f"{platform}: {rel} is declared but absent"


@pytest.mark.parametrize("platform,rel", ap.AI_PLATFORM_FILES)
def test_every_platform_file_is_tracked_by_git(platform, rel):
    """An untracked instruction file cannot reach a fresh clone, or a wheel.

    This is exactly how tools/agents/ vanished from every release.
    """
    out = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                         cwd=ap.REPO_ROOT, capture_output=True, text=True)
    assert out.returncode == 0, f"{rel} is untracked — it will not ship"


# --------------------------------------------------------------------------- #
# Bootstrap name mapping
# --------------------------------------------------------------------------- #


def test_bootstrap_names_are_flat_and_collision_free():
    """Wheel package-data cannot carry dot-directories reliably, so the paths
    are flattened. Two platforms flattening to one name would silently drop
    whichever is copied first."""
    names = [ap.bootstrap_name(rel) for _p, rel in ap.AI_PLATFORM_FILES]
    assert len(set(names)) == len(names)
    assert all("/" not in n.split("platforms/", 1)[1] for n in names)


@pytest.mark.parametrize("rel,expected", [
    (".cursor/rules/icdev.mdc", "platforms/cursor__rules__icdev.mdc"),
    ("AGENTS.md", "platforms/AGENTS.md"),
    (".goosehints", "platforms/goosehints"),
])
def test_bootstrap_name_mapping(rel, expected):
    assert ap.bootstrap_name(rel) == expected


# --------------------------------------------------------------------------- #
# The three consumers must stay in step — this is the drift monitor
# --------------------------------------------------------------------------- #


def test_prebuild_bootstrap_sources_the_canonical_list():
    """What enters the WHEEL."""
    import inspect

    from tools.installer import prebuild_bootstrap

    src = inspect.getsource(prebuild_bootstrap)
    assert "ai_platforms" in src, "prebuild_bootstrap does not read the platform list"


def test_init_payload_sources_the_canonical_list():
    """What lands in the USER'S PROJECT."""
    import inspect

    from tools.cli import init as init_mod

    src = inspect.getsource(init_mod)
    assert "ai_platforms" in src, "icdev init does not read the platform list"


def test_release_gate_verifies_platform_coverage():
    """A release that drops them must be BLOCKED, not published."""
    import inspect

    from tools.installer import release

    src = inspect.getsource(release.step_verify_payload)
    assert "ai_platforms" in src
    assert "Claude-only" in src


def test_coherence_checker_still_audits_the_same_platforms():
    """The repo-side auditor and the packaging list must not diverge.

    check_karpathy_sync enforces the Karpathy headings across these same ten
    files. If someone adds a platform there but not to ai_platforms.py, the new
    one would be audited in the repo and never shipped — the original bug.
    """
    import inspect

    from tools.workflow import coherence_checker

    src = inspect.getsource(coherence_checker)
    for _platform, rel in ap.AI_PLATFORM_FILES:
        assert rel in src, f"{rel} is packaged but not audited by coherence_checker"


def test_missing_in_repo_reports_cleanly():
    assert ap.missing_in_repo() == []


def test_cli_reports_full_coverage():
    assert ap.main(["--json"]) == 0
