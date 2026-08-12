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

from tools.git import manifest_merge_rehearsal as rehearsal

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not available"
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
