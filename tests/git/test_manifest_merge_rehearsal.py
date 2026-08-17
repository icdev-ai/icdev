# CUI // SP-CTI
"""Regression tests for the `tools/manifest/` merge-conflict fix (kax-conflict-03).

These run real `git` against throwaway repositories — they assert the property
the fix actually depends on (git does not need a human), not that a config line
is spelled a particular way.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from tools.git import commands_doc_merge_rehearsal as cmd_rehearsal
from tools.git import manifest_merge_rehearsal as rehearsal

REPO_ROOT = Path(__file__).resolve().parents[2]

#: This module used to carry `pytestmark = skipif(shutil.which("git") is None)`.
#: It was removed when the file was CI-gated (kax-conflict-11): a gated test that
#: skips is an unmeasured test, the skip census is at its ceiling, and the
#: ceiling may only go down. Git is not an optional dependency here — the repo's
#: whole workflow is worktree-first, CI checks out with git, and every assertion
#: below shells out to it. On a box without git these now fail loudly instead of
#: reporting green while measuring nothing, which is the intended semantics.
if shutil.which("git") is None:  # pragma: no cover - git is present everywhere this runs
    raise RuntimeError(
        "git is required by tests/git/ — these tests assert real merge behaviour "
        "against throwaway repositories and cannot be meaningfully skipped."
    )


def _run(layout: str, mode: str, branches: int = 2):
    return rehearsal.run_scenario(layout, branches=branches, mode=mode)


@pytest.mark.parametrize("mode", rehearsal.MODES)
@pytest.mark.parametrize("layout", ["table-row", "heading-block"])
def test_single_file_layouts_still_conflict(layout, mode):
    """The baseline hazard is real, and a heading block does not fix it.

    Both candidates put the new entry at the same end-of-file offset, so two
    branches registering different tools collide. This is the control: if it
    ever goes green the rehearsal has stopped exercising the real edit pattern
    and the `union` result below proves nothing.
    """
    result = _run(layout, mode)
    assert result.conflicted, f"{layout}/{mode} was expected to conflict"
    assert any("kanban.md" in p for p in result.conflicted_paths)


@pytest.mark.parametrize("mode", rehearsal.MODES)
@pytest.mark.parametrize("branches", [2, 3])
def test_union_merges_concurrent_registrations(mode, branches):
    """`merge=union` lands every concurrent registration with no human.

    `merge-tree` matters as much as `worktree`: it is the bare, index-free
    plumbing a forge runs to decide whether a PR is mergeable, so a layout that
    is clean only in the worktree path still shows the PR as conflicted.
    """
    result = _run("union", mode, branches=branches)
    assert not result.conflicted, result.notes
    assert result.all_entries_present, result.notes
    assert not result.duplicate_entries, result.notes


def test_repo_gitattributes_marks_manifest_union():
    """The shipped `.gitattributes` covers the manifest and its icdev mirror."""
    text = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    directives = {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for pattern in (
        "tools/manifest.md merge=union",
        "tools/manifest/*.md merge=union",
        "icdev/tools/manifest.md merge=union",
        "icdev/tools/manifest/*.md merge=union",
    ):
        assert pattern in directives, f"missing .gitattributes directive: {pattern}"


#: Paths that have earned ``merge=union``. Union takes the SUPERSET of both
#: sides, which is safe only for flat, line-oriented files whose lines are
#: independent records — never for YAML/JSON/Python, where it yields duplicate
#: keys or broken code. Adding an entry is a deliberate act; justify it here.
_UNION_SAFE_PATHS = frozenset({
    "tools/manifest.md",
    "tools/manifest/*.md",
    "icdev/tools/manifest.md",
    "icdev/tools/manifest/*.md",
    # The gated CI test list: one test path per line, appended by concurrent
    # branches for exactly the reason the manifest shards are. That it is
    # append-only is pinned separately by tests/ci/test_gated_test_list.py,
    # which asserts this very directive must be present — so a bare
    # "must contain 'manifest'" rule put these two suites in direct conflict.
    "args/ci_test_files/*.txt",
    "icdev/data/args/ci_test_files/*.txt",
    # The CI skip census (trust-disc-03): one site per line,
    # `<file>::<qualname>::<kind>  # <written reason>`, appended by every task
    # that registers or removes a skip. Same shape as the allowlists above.
    # NOTE: this entry was missing until kax-conflict-11, so this test was RED
    # from the moment trust-disc-03 landed the directive — and nothing reported
    # it, because the file sat in args/ci_test_backlog.txt and had never gated a
    # merge. It is gated now; that is the actual fix.
    "args/ci_skip_census.txt",
    # The command reference (kax-conflict-11): the registration checklist in
    # CLAUDE.md sends every new tool here, so 18 of 40 recent branches appended
    # to it. Measured over the 60 most recent merges, 14 of 14 branches whose
    # own diff touched it were PURE additions and none edited an existing line.
    "docs/reference/commands.md",
    "icdev/docs/reference/commands.md",
    "icdev/data/docs/reference/commands.md",
})


def test_union_is_not_applied_to_structured_config():
    """Union on YAML/JSON/Python would produce duplicate keys or broken code.

    Checked against an explicit allowlist rather than a substring. The old rule
    was ``"manifest" in pattern``, which is both too narrow and too loose: it
    rejected args/ci_test_files/*.txt (flat, line-oriented, deliberately union —
    and required by another test), while it would have happily accepted
    ``config/manifest.yaml merge=union``, which is precisely the structured-config
    case this test exists to prevent.
    """
    text = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "merge=union" not in stripped:
            continue
        pattern = stripped.split()[0]
        assert pattern in _UNION_SAFE_PATHS, (
            f"merge=union applied to {pattern!r}, which is not in the union-safe "
            "allowlist. Union is only safe for flat, line-oriented files whose "
            "lines are independent records. If this path really qualifies, add it "
            "to _UNION_SAFE_PATHS with a note saying why."
        )


def _union_patterns() -> list:
    """Every non-comment `merge=union` pattern in the shipped .gitattributes."""
    text = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "merge=union" not in stripped:
            continue
        out.append(stripped.split()[0])
    return out


def test_repo_gitattributes_marks_commands_doc_union():
    """The command reference and BOTH its mirrors are union-merged (kax-conflict-11)."""
    patterns = set(_union_patterns())
    for pattern in (
        "docs/reference/commands.md",
        "icdev/docs/reference/commands.md",
        "icdev/data/docs/reference/commands.md",
    ):
        assert pattern in patterns, f"missing .gitattributes directive: {pattern}"


def test_every_union_pattern_matches_a_real_path():
    """A union rule for a path that does not exist protects nothing.

    This is not hypothetical: the first draft of kax-conflict-11 guessed the
    mirror as `icdev/data/claude_bootstrap/docs/reference/commands.md`, which is
    not where the mirror lives. The rule would have been inert and the real
    mirror left unprotected, with nothing failing to say so.
    """
    for pattern in _union_patterns():
        matches = list(REPO_ROOT.glob(pattern))
        assert matches, (
            f"merge=union declares {pattern!r}, which matches no file in the "
            "tree. Either the path is misspelled or the file was removed — "
            "an inert union rule silently leaves the real file unprotected."
        )


def test_union_covers_every_path_claimed_union_merged_by_coordination_paths():
    """`coordination_paths` promises some paths merge without a human; git must agree.

    `hold_on_sibling_conflict` stops serializing PRs that share a coordination
    path, on the understanding that git resolves it. When a path is listed there
    with no union rule behind it, the watcher lets the PRs race and git then
    conflicts on every one — which is exactly what happened to
    `docs/reference/commands.md` and produced two of the six conflicts resolved
    by hand on 2026-08-16.

    Only the paths that CLAIM to be literally union-merged are checked; the rest
    of the tuple is an explicit heuristic about how those files are edited.
    """
    from tools.git import coordination_paths

    literally_union = {
        "tools/manifest/",
        "tools/manifest.md",
        "args/ci_test_files/",
        "docs/reference/commands.md",
    }
    declared = set(coordination_paths.COORDINATION_PATH_MARKERS)
    missing = literally_union - declared
    assert not missing, (
        f"{sorted(missing)} are union-merged but no longer declared coordination "
        "paths — pr_watcher will serialize PRs that git resolves for free."
    )

    patterns = _union_patterns()
    for marker in sorted(literally_union & declared):
        covered = any(p == marker or p.startswith(marker) for p in patterns)
        assert covered, (
            f"{marker!r} is treated as safe-to-co-edit by "
            "coordination_paths.COORDINATION_PATH_MARKERS, but .gitattributes "
            "declares no merge=union rule for it. The watcher stops serializing "
            "PRs that share it while git still conflicts on every one."
        )


@pytest.mark.parametrize("mode", cmd_rehearsal.MODES)
def test_commands_doc_appends_merge_cleanly_under_union(mode):
    """Concurrent appends to the command reference resolve without a human."""
    result = cmd_rehearsal.run_scenario(
        "append_tail", mode=mode, branches=3,
        source=REPO_ROOT / cmd_rehearsal.TARGET,
    )
    assert not result.conflicted, result.detail
    assert not result.lost_blocks, f"union dropped block(s): {result.lost_blocks}"
    assert result.fences_balanced, "union left the ```bash fences unbalanced"
    assert not result.markers_left


@pytest.mark.parametrize("mode", cmd_rehearsal.MODES)
def test_commands_doc_control_conflicts_without_union(mode):
    """The control: with no union rule the same appends DO conflict.

    Without this the test above proves nothing — it would pass just as happily
    against a harness that never created a collision at all.
    """
    result = cmd_rehearsal.run_scenario(
        "append_tail", mode=mode, branches=3, union=False,
        source=REPO_ROOT / cmd_rehearsal.TARGET,
    )
    assert result.conflicted, (
        "concurrent appends merged cleanly WITHOUT merge=union — the rehearsal "
        "is not exercising the collision it claims to exercise"
    )


_SEPARATOR_RE = re.compile(r"^\|[\s:\-|]+\|$")


def _data_rows(path: Path):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        row = line.strip()
        if not (row.startswith("|") and row.endswith("|")):
            continue
        if _SEPARATOR_RE.match(row):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if _SEPARATOR_RE.match(nxt):  # header row; a shard may hold several tables
            continue
        yield i + 1, row


def test_no_duplicate_manifest_rows_in_repo():
    """Union's one failure mode, guarded on the live manifest.

    Taking the superset leaves an exact duplicate behind when both branches
    added the same row. Delete the repeat rather than grandfathering it.
    """
    offenders = []
    targets = [REPO_ROOT / "tools" / "manifest.md"]
    targets.extend(sorted((REPO_ROOT / "tools" / "manifest").glob("*.md")))
    for shard in targets:
        seen = {}
        for lineno, row in _data_rows(shard):
            if row in seen:
                offenders.append(f"{shard.name}:{lineno} duplicates line {seen[row]}")
            else:
                seen[row] = lineno
    assert not offenders, "duplicate manifest rows: " + "; ".join(offenders)
