#!/usr/bin/env python3
"""Resolve the CI pytest allowlists that used to live inline in icdev-ci.yml.

WHY THIS EXISTS (kax-conflict-07)
---------------------------------
`.github/workflows/icdev-ci.yml` was a single file that every task edited. The
`test` job's list of test files is an explicit per-file allowlist — deliberately,
because a glob that matches nothing silently shrinks the gate — so every task
that added a test appended to the same list at the same end-of-chain offset.

Measured 2026-08-09, two costs:

  * MERGE CONFLICTS. Five open PRs collided on this file in one night. Every
    hand-resolve was the same edit: "keep both added lines".
  * PIPELINE DEADLOCK. `pr_watcher.hold_on_sibling_conflict` refuses to merge a
    PR that shares a non-additive file with another open PR. Once five PRs
    shared this one, each was a sibling of every other and none could merge.
    #1434 fixed the generated-artifact form of this by excluding derived files;
    this workflow is hand-written, so that fix did not cover it.

Marking the whole workflow additive would have been wrong: it holds real job
definitions, and two PRs editing a job's `run:` block IS a collision worth
serializing. Only the test-file list is additive. So the list moved OUT, into
`args/ci_test_files/*.txt` — flat, line-oriented, one path per line, which is
exactly the shape `merge=union` is safe for (see `.gitattributes`). The workflow
itself keeps its serialized protection; appending a test file no longer touches
it at all.

FILE FORMAT
-----------
One pytest target per line. `#` comments and blank lines are ignored, and the
rationale for a given entry lives on the comment lines directly above it — the
same prose that used to sit in a block above the `run:` step, now next to the
thing it justifies. A directory target (trailing `/`) is allowed.

THE GATE CANNOT SILENTLY SHRINK
-------------------------------
That property is the whole reason the list was explicit in the first place, so
moving it out must not cost it. `--check` fails when:

  * the list file is missing or unreadable
  * the resolved list is EMPTY
  * the resolved list is below the recorded floor for that list
  * a listed path does not exist in the checkout
  * a path is listed twice (which is what a careless union merge leaves behind)

CI runs `--check` as its own step before pytest, so a truncated list is a red
step with a named cause rather than a green run over three tests.

THE GATE CANNOT SILENTLY REGROW EITHER (tsg-policy-01)
-----------------------------------------------------
`--check` proves the list did not shrink. It says nothing about the 1,826 test
modules that were never on it — which is the bigger hole: a test file CI never
runs has never gated a merge, so it can be wrong from its first commit and
nothing goes red. That is how `remediation_simulator._run_nqe_layer` stayed dead
for six weeks (tsg-dead-01).

`--check-coverage` closes the regrowth path with a ratchet. Every collectible
test module under `tests/` must be in one of three places:

  * an allowlist (`args/ci_test_files/*.txt`) — CI runs it;
  * a documented exclusion (`args/test_gating_gate.yaml`) — gating it would buy
    no signal, and the reason is written down;
  * the grandfathered census (`args/ci_test_backlog.txt`) — pre-existing debt,
    enumerated so it is countable and can only shrink.

Anything else is a NEW ungated test file, and it fails the `test` job by name.
The fix is never "add it to the backlog" — it is "make it pass and append it to
core.txt", which is the only sanctioned way to widen the allowlist.

AND IT RUNS AT COMMIT TIME TOO (tsg-policy-02)
----------------------------------------------
CI is the backstop, not the first line: a census failure there turns main red,
which blocks every open PR, for a one-line fix. `staged_new_test_files` lets
`tools/testing/pre_commit_check.py` run the same census on the same policy at
`git commit` time — but only when the commit adds or renames a file in scope, so
a commit touching no tests pays nothing. The hook prints this module's message
and refuses; it never edits core.txt itself, because a hook that silently widened
the allowlist would gate a test nobody has run.

Usage
-----
    python tools/ci/gated_test_list.py --check --list core
    python tools/ci/gated_test_list.py --print --list windows
    python tools/ci/gated_test_list.py --check --list core --out "$RUNNER_TEMP/t.txt"
    python tools/ci/gated_test_list.py --json
    python tools/ci/gated_test_list.py --check-coverage          # the ratchet
    python tools/ci/gated_test_list.py --check-coverage --json
    python tools/ci/gated_test_list.py --prune-backlog           # drop fixed lines
    # Before/after proof that moving the list changed no entry:
    git show <rev>:.github/workflows/icdev-ci.yml > /tmp/old.yml
    python tools/ci/gated_test_list.py --extract-workflow /tmp/old.yml --job test
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

#: Directory holding the allowlists, relative to the repository root.
LIST_DIR = Path("args") / "ci_test_files"

#: list name -> file name under LIST_DIR.
LISTS: Dict[str, str] = {
    "core": "core.txt",
    "windows": "windows.txt",
}

#: list name -> FRAGMENT DIRECTORY under LIST_DIR, read together with the file
#: above (tsg-policy-03).
#:
#: `core.txt` was the largest collision surface on the board: 82.8% of merged
#: kanban PRs touched it, because CLAUDE.md requires every PR that adds a test
#: file to append to it. GitHub does not apply `.gitattributes merge=union`, so
#: every one of those PRs went CONFLICTING the moment a sibling merged — 30.9%
#: needed a rebase and 27.4% escalated to a human.
#:
#: A PR now writes ONE fragment named for its task instead of appending to a
#: shared file, so two PRs never touch the same path and the collision surface
#: for new work is zero. Purely ADDITIVE: `core.txt` keeps every entry it has,
#: nothing migrates, and both are read as one list.
FRAGMENT_DIRS: Dict[str, str] = {
    "core": "core.d",
    "windows": "windows.d",
}

#: Minimum entry count per list — a TRUNCATION backstop, not a quality bar.
#:
#: Set below the current count with headroom so that legitimately retiring a few
#: tests does not require editing this file (which would put the hot-file problem
#: straight back). A list that loses a third of itself is not a retirement, it is
#: a bad merge or a bad sed, and that is what these numbers catch.
#: Counts when the lists were extracted from icdev-ci.yml: core 97, windows 13.
FLOORS: Dict[str, int] = {
    "core": 80,
    "windows": 10,
}


class AllowlistError(RuntimeError):
    """The allowlist could not be resolved, or failed its own integrity check."""


def repo_root(start: Optional[Path] = None) -> Path:
    """Walk up from `start` until a directory containing the allowlists is found.

    Resolved from `__file__` rather than `os.getcwd()` on purpose: this runs from
    git worktrees, from CI runners that change directory, and from both the
    `tools/` and packaged `icdev/tools/` copies, which sit at different depths.

    Two layouts are accepted: `<root>/args/ci_test_files` in a checkout, and
    `<root>/data/args/ci_test_files` in an installed wheel, where
    `sync_package_tree` mirrors `args/` to `icdev/data/args/`. Without the second
    the CLI would import fine from the wheel and then always fail to find its own
    data — a dead CLI that only a wheel user ever meets.
    """
    here = (start or Path(__file__).resolve()).resolve()
    for candidate in [here, *here.parents]:
        for prefix in (Path("."), Path("data")):
            if (candidate / prefix / LIST_DIR).is_dir():
                return candidate / prefix
    raise AllowlistError(
        f"could not locate {LIST_DIR.as_posix()} above {here} — "
        "pass --root to point at the repository checkout"
    )


def list_path(name: str, root: Optional[Path] = None) -> Path:
    if name not in LISTS:
        raise AllowlistError(f"unknown list {name!r}; expected one of {sorted(LISTS)}")
    return (root or repo_root()) / LIST_DIR / LISTS[name]


def parse(text: str) -> List[str]:
    """Strip comments and blanks; return the pytest targets in file order."""
    out: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # An inline trailing comment is allowed; a path never contains " #".
        line = line.split(" #", 1)[0].strip()
        if line:
            out.append(line)
    return out


def fragment_dir(name: str, root: Optional[Path] = None) -> Optional[Path]:
    """The fragment directory for *name*, or None when the list has none."""
    rel = FRAGMENT_DIRS.get(name)
    if not rel:
        return None
    return (root or repo_root()) / LIST_DIR / rel


def fragment_files(name: str, root: Optional[Path] = None) -> List[Path]:
    """Fragment files for *name*, in a DETERMINISTIC order.

    Sorted by filename so the pytest target order is identical on every machine
    and every run. CI executes these in one process in list order, and an order
    that varied by directory-listing would make a test's neighbours — and so its
    isolation behaviour — depend on the filesystem.
    """
    d = fragment_dir(name, root)
    if d is None or not d.is_dir():
        return []
    return sorted(d.glob("*.txt"), key=lambda p: p.name)


def resolve(name: str = "core", root: Optional[Path] = None) -> List[str]:
    """Entries for *name*: the list file, then every fragment, in order.

    The single chokepoint — `check`, `gated_targets` and the `--print` CLI all
    read the allowlist through here, so extending it covers every reader at
    once and none of them can drift.

    The list file is still REQUIRED: an empty fragment directory is normal, a
    missing `core.txt` means the allowlist could not be resolved and the gate
    must not quietly run nothing.
    """
    path = list_path(name, root)
    if not path.is_file():
        raise AllowlistError(f"{path} is missing — the CI test allowlist cannot be resolved")
    entries = parse(path.read_text(encoding="utf-8"))
    for frag in fragment_files(name, root):
        entries.extend(parse(frag.read_text(encoding="utf-8")))
    return entries


def check(name: str = "core", root: Optional[Path] = None) -> Dict[str, object]:
    """Resolve a list and validate it. Never raises for a *content* problem —
    the caller reads `ok` — but does raise when the file itself is unreadable.
    """
    root = root or repo_root()
    entries = resolve(name, root)
    floor = FLOORS.get(name, 1)

    seen: Dict[str, int] = {}
    for entry in entries:
        seen[entry] = seen.get(entry, 0) + 1
    duplicates = sorted(k for k, v in seen.items() if v > 1)

    # Existence is checked against a real checkout. In an installed wheel the
    # lists live under icdev/data/args/ and there is no tests/ tree to point at,
    # so the check reports itself as NOT RUN rather than flagging all 97 entries
    # as missing — a check that cries wolf gets a `|| true` bolted onto it. CI
    # always runs this from the checkout, where tests/ is present.
    existence_checked = (root / "tests").is_dir()
    missing = [e for e in entries if not (root / e).exists()] if existence_checked else []

    errors: List[str] = []
    if not entries:
        errors.append(
            f"{LISTS[name]} resolved to ZERO test targets — the gate would run nothing"
        )
    elif len(entries) < floor:
        errors.append(
            f"{LISTS[name]} resolved to {len(entries)} targets, below the floor of "
            f"{floor} — the gate shrank; if this is a deliberate retirement, lower "
            f"FLOORS[{name!r}] in tools/ci/gated_test_list.py in the same commit"
        )
    if missing:
        errors.append(f"listed but not present in the checkout: {', '.join(missing)}")
    if duplicates:
        errors.append(f"listed more than once: {', '.join(duplicates)}")

    return {
        "list": name,
        "path": str(list_path(name, root)),
        "count": len(entries),
        "floor": floor,
        "entries": entries,
        "existence_checked": existence_checked,
        "missing": missing,
        "duplicates": duplicates,
        "errors": errors,
        "ok": not errors,
    }


# --------------------------------------------------------------------------- #
# Coverage census — the "gap cannot silently regrow" ratchet (tsg-policy-01)
# --------------------------------------------------------------------------- #

#: Policy config: scope, documented exclusions, backlog pointer, backlog ceiling.
GATE_CONFIG = Path("args") / "test_gating_gate.yaml"


def load_gate_config(root: Optional[Path] = None) -> Dict[str, object]:
    """Read args/test_gating_gate.yaml.

    Imported lazily so a missing pyyaml cannot break `--check`, which is the
    load-bearing step that runs before pytest in every CI run.
    """
    root = root or repo_root()
    path = root / GATE_CONFIG
    if not path.is_file():
        raise AllowlistError(
            f"{path} is missing — the test gating policy cannot be resolved"
        )
    try:
        import yaml  # noqa: PLC0415 — deliberate lazy import, see docstring
    except ImportError as exc:  # pragma: no cover - pyyaml is a core dependency
        raise AllowlistError(f"pyyaml is required to read {GATE_CONFIG}: {exc}") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise AllowlistError(f"{path} did not parse to a mapping")
    return data


def _matches(rel: str, pattern: str) -> bool:
    """Glob match with a recursive `**` that behaves the way people expect.

    `fnmatch` treats `*` as matching `/` too, so `tests/e2e_selenium/**` already
    matches at any depth — but `tests/foo/**` would then NOT match `tests/foo/`
    itself if someone wrote a bare prefix. Handling the `/**` suffix explicitly
    means an exclusion covers the whole subtree whichever way it is written.
    """
    if pattern.endswith("/**") and rel.startswith(pattern[:-2]):
        return True
    return fnmatch.fnmatch(rel, pattern)


def _tracked_files(root: Path) -> List[str]:
    """Repo-relative, forward-slash paths that git tracks.

    git rather than os.walk so an untracked scratch file in a developer's
    worktree cannot fail the gate, and so a file someone forgot to `git add`
    cannot pass it — the census must describe what a CI checkout will contain.
    Falls back to a walk when git is unavailable (an unpacked tarball, a wheel).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, timeout=120, check=False,
            # See staged_added_or_renamed: this now runs on every developer's
            # Windows box at commit time, not only on a UTF-8 CI runner.
            encoding="utf-8", errors="replace",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
        for name in filenames:
            out.append(
                (Path(dirpath) / name).relative_to(root).as_posix()
            )
    return out


def in_scope(rel: str, config: Dict[str, object]) -> bool:
    """True when `rel` is a file the census counts, per the config's `scope` block.

    Split out of `collect_test_files` so the pre-commit fast path (tsg-policy-02)
    decides "is this staged file a test file?" through the SAME rule the census
    uses, rather than re-deriving `tests/` + `test_*.py` from memory. A fast path
    that scoped differently from the gate would either nag about files the gate
    ignores or wave through files it fails on.
    """
    scope = config.get("scope") or {}
    roots = [str(r) for r in (scope.get("roots") or ["tests/"])]  # type: ignore[union-attr]
    patterns = [str(p) for p in (scope.get("patterns") or ["test_*.py"])]  # type: ignore[union-attr]
    rel = rel.replace("\\", "/")
    if not any(rel.startswith(r) for r in roots):
        return False
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def collect_test_files(root: Path, config: Dict[str, object]) -> List[str]:
    """Every module the runner would collect, per the config's `scope` block."""
    return sorted(rel for rel in _tracked_files(root) if in_scope(rel, config))


def gated_targets(root: Path, test_files: Sequence[str]) -> Set[str]:
    """Expand every allowlist to the set of test FILES CI actually runs.

    A directory entry (`tests/studio/`) covers the modules beneath it; that is
    one line in the list but many files in the census, and counting the line
    would understate coverage.
    """
    covered: Set[str] = set()
    for name in LISTS:
        for entry in resolve(name, root):
            if entry.endswith("/"):
                covered.update(f for f in test_files if f.startswith(entry))
            else:
                covered.add(entry)
    return covered & set(test_files)


def census(root: Optional[Path] = None) -> Dict[str, object]:
    """Classify every test module as gated / excluded / backlog / UNLISTED.

    `unlisted` is the gate. It is empty on a compliant tree, and any entry in it
    is a test file that has never gated a merge and that nobody decided to leave
    ungated — the exact state this ratchet exists to make impossible to reach
    without an explicit, reviewable edit.
    """
    root = root or repo_root()

    # No tests/ tree means an installed wheel, where args/ is mirrored but the
    # suite is not shipped. Report NOT RUN rather than flagging all 2,150 files
    # as missing — a check that cries wolf gets a `|| true` bolted onto it.
    #
    # Checked BEFORE the config is loaded, on purpose. The policy files are
    # mirrored to icdev/data/args/ by sync_package_tree at RELEASE, not by every
    # PR that edits them (hand-syncing args/ci_test_backlog.txt would put the
    # two-files-per-fix cost straight back — kax-conflict-07). So in a wheel
    # built between releases the config can legitimately be absent, and that must
    # read as "nothing to census here", not as a crash. In a real checkout the
    # tests/ tree IS present, so a missing config still raises.
    if not (root / "tests").is_dir():
        return {
            "ran": False,
            "reason": f"no tests/ tree at {root} — census NOT RUN",
            "errors": [],
            "ok": True,
        }

    config = load_gate_config(root)

    test_files = collect_test_files(root, config)
    gated = gated_targets(root, test_files)

    exclusions = config.get("exclusions") or []
    excluded: Set[str] = set()
    stale_exclusions: List[str] = []
    for rule in exclusions:  # type: ignore[union-attr]
        pattern = str((rule or {}).get("pattern", ""))
        if not pattern:
            continue
        hit = {f for f in test_files if _matches(f, pattern)}
        if not hit:
            stale_exclusions.append(pattern)
        excluded |= hit
    excluded -= gated  # an explicitly gated file is gated, whatever a glob says

    backlog_file = str(config.get("backlog_file") or "args/ci_test_backlog.txt")
    backlog_path = root / backlog_file
    if not backlog_path.is_file():
        raise AllowlistError(f"{backlog_path} is missing — the backlog census cannot be resolved")
    backlog = parse(backlog_path.read_text(encoding="utf-8"))

    ungated = [f for f in test_files if f not in gated and f not in excluded]
    backlog_set = set(backlog)
    unlisted = [f for f in ungated if f not in backlog_set]
    effective = [f for f in ungated if f in backlog_set]
    # A backlog line that is now gated, now excluded, or gone from the tree.
    # Reported, never fatal: making a file pass and forgetting to delete its line
    # here must not fail an otherwise correct PR. `--prune-backlog` clears them.
    stale_backlog = sorted(backlog_set - set(ungated))

    backlog_max = int(config.get("backlog_max", len(effective)))  # type: ignore[arg-type]

    errors: List[str] = []
    if unlisted:
        shown = ", ".join(unlisted[:20]) + (f" (+{len(unlisted) - 20} more)" if len(unlisted) > 20 else "")
        errors.append(
            f"{len(unlisted)} test file(s) are gated by nothing: {shown}. "
            "CI never runs them, so they can be wrong from their first commit and "
            "nothing goes red. Make each one pass and append it to "
            "args/ci_test_files/core.txt in this PR — that is the only sanctioned "
            "way to widen the allowlist. If it genuinely should not be gated, add "
            "an exclusion WITH A REASON to args/test_gating_gate.yaml. Do NOT add "
            "it to args/ci_test_backlog.txt: that census is closed and only shrinks"
        )
    if len(effective) > backlog_max:
        errors.append(
            f"the ungated backlog is {len(effective)}, above the ceiling of "
            f"{backlog_max} — it grew. Lower backlog_max in args/test_gating_gate.yaml "
            "when you gate files; never raise it to get a commit through"
        )

    return {
        "ran": True,
        "total": len(test_files),
        "gated": len(gated),
        "excluded": len(excluded),
        "backlog": len(effective),
        "backlog_max": backlog_max,
        "unlisted": unlisted,
        "stale_backlog": stale_backlog,
        "stale_exclusions": sorted(stale_exclusions),
        "backlog_file": backlog_file,
        "errors": errors,
        "ok": not errors,
    }


def prune_backlog(root: Optional[Path] = None) -> Dict[str, object]:
    """Delete census lines that are now gated, excluded, or gone from the tree.

    Preserves the header comments and the file's order; only removes lines.
    """
    root = root or repo_root()
    report = census(root)
    if not report.get("ran"):
        return {"pruned": [], "report": report}
    stale = set(report["stale_backlog"])  # type: ignore[arg-type]
    path = root / str(report["backlog_file"])
    kept = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().split(" #", 1)[0].strip() not in stale
    ]
    # newline="\n" explicitly: the default translates to CRLF on Windows, and a
    # trailing CR makes every consumer report "file not found" for a path that
    # plainly exists — the same class of bug as hgx-exec-01.
    path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    return {"pruned": sorted(stale), "report": report}


# --------------------------------------------------------------------------- #
# Pre-commit fast path — run the census where the author can feel it (tsg-policy-02)
# --------------------------------------------------------------------------- #
# The CI step below is the backstop and stays the backstop: a hook is skippable
# with --no-verify and is absent for anything that does not land through a local
# commit. But CI is the WRONG PLACE to first learn that a test file is ungated —
# it turns main red, which blocks every open PR, and costs a follow-up branch +
# PR + full CI cycle to add one line the author could have added in one second.
# Measured 2026-08-13: that happened twice within two hours of the census landing
# (tests/test_bootstrap_hook_payload.py via #1582, tests/test_kanban_gate_sentinel_
# seeding.py via #1598), once from an autonomous worker and once from an
# interactive session — so it is not one actor's discipline problem.
#
# These two helpers give `tools/testing/pre_commit_check.py` the decision it
# needs: which files does THIS commit add that the census would collect? They are
# deliberately cheap — a `git diff --cached` plus one YAML read, no database, no
# network, no `import tools` — and `staged_new_test_files` returns before reading
# the config at all when the commit adds nothing.


def staged_added_or_renamed(root: Optional[Path] = None) -> List[str]:
    """Repo-relative paths this commit ADDS or RENAMES-TO, read from the index.

    `--diff-filter=AR` with `--name-only` reports the DESTINATION path of a
    rename, which is the one that has to be registered; a modification to an
    already-registered file is correctly not reported at all.

    Returns [] when git is unavailable or the command fails. The caller then
    gates nothing, which is the right failure direction for a fast path whose
    whole justification is that CI still runs the same census.
    """
    root = root or repo_root()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=AR"],
            capture_output=True, text=True, timeout=60, check=False,
            # Explicit, because `text=True` decodes with `locale.getencoding()` —
            # cp1252 on a Windows dev box, where a non-ASCII path byte raises
            # UnicodeDecodeError. git emits UTF-8.
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def staged_new_test_files(
    root: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
    config: Optional[Dict[str, object]] = None,
) -> List[str]:
    """Of the files this commit adds or renames, which ones the census collects.

    `files` lets a caller that has already run `git diff --cached` pass its own
    list rather than paying for a second git call.
    """
    root = root or repo_root()
    staged = list(files) if files is not None else staged_added_or_renamed(root)
    if not staged:
        # The common case: a commit that only MODIFIES files adds no test file,
        # so it must not pay for a config read to be told so.
        return []
    config = config if config is not None else load_gate_config(root)
    return sorted(f for f in staged if in_scope(f, config))


# --------------------------------------------------------------------------- #
# Legacy-workflow extraction — the before/after proof
# --------------------------------------------------------------------------- #
_JOB_RE = re.compile(r"^  (?P<job>[A-Za-z0-9_-]+):\s*$")
_PYTEST_RE = re.compile(r"(?:^|\s)pytest\s")
_BACKSLASH = chr(92)


def extract_chains(text: str, job: Optional[str] = None) -> List[List[str]]:
    """Return every INLINE pytest invocation in a workflow as its list of targets.

    One entry per `pytest ...` command, following shell line-continuations. A
    single-target invocation (the `/knowledge-search` retry step, say) comes back
    as a one-element chain; the 97-path allowlist came back as a 97-element one.
    That distinction is the point: the thing this task removed is a LIST inlined
    in the workflow, not the use of pytest.

    `job` restricts the scan to one top-level job block; omit it to scan the file.
    """
    lines = text.splitlines()
    if job is not None:
        start = None
        end = len(lines)
        for i, line in enumerate(lines):
            m = _JOB_RE.match(line)
            if not m:
                continue
            if m.group("job") == job:
                start = i
            elif start is not None:
                end = i
                break
        if start is None:
            return []
        lines = lines[start:end]

    chains: List[List[str]] = []
    i = 0
    while i < len(lines):
        if not _PYTEST_RE.search(lines[i]):
            i += 1
            continue
        chain: List[str] = []
        j = i
        while True:
            for tok in lines[j].rstrip().rstrip(_BACKSLASH).split():
                if tok.startswith("tests/"):
                    chain.append(tok)
            if not lines[j].rstrip().endswith(_BACKSLASH):
                break
            j += 1
        if chain:
            chains.append(chain)
        i = j + 1
    return chains


def extract_from_workflow(
    text: str, job: Optional[str] = None, min_targets: int = 1
) -> List[str]:
    """Flatten `extract_chains`, keeping chains of at least `min_targets` targets.

    Used to diff the resolved list against the list as it stood before the
    extraction (acceptance criterion 3): `--min-targets 2` selects the allowlist
    chain and ignores incidental single-target pytest steps.
    """
    return [t for chain in extract_chains(text, job) if len(chain) >= min_targets
            for t in chain]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--list", dest="name", default="core", choices=sorted(LISTS))
    parser.add_argument("--root", type=Path, help="repository root (default: derived from __file__)")
    parser.add_argument("--check", action="store_true", help="validate; exit 1 on any defect")
    parser.add_argument("--print", dest="do_print", action="store_true",
                        help="write the resolved targets to stdout, one per line")
    parser.add_argument("--out", type=Path, help="write the resolved targets to a file")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--extract-workflow", type=Path,
                        help="parse an inline pytest chain out of a workflow YAML instead")
    parser.add_argument("--job", help="restrict --extract-workflow to one job block")
    parser.add_argument("--min-targets", type=int, default=1,
                        help="with --extract-workflow, ignore pytest chains shorter than this")
    parser.add_argument("--check-coverage", action="store_true",
                        help="fail when a test file is gated by nothing (tsg-policy-01)")
    parser.add_argument("--prune-backlog", action="store_true",
                        help="delete backlog census lines that are now gated or gone")
    args = parser.parse_args(argv)

    # LF on every platform. `print()` translates "\n" to "\r\n" on Windows, and
    # bash's `read -r` strips only the newline — so the consumer got
    # "tests/foo.py\r" and pytest reported "file or directory not found" for a
    # file that plainly exists. The Linux jobs never see it; the windows-latest
    # job fails on every entry. Caught on the empty-list proof run before merge,
    # and it is the same CRLF class as the hgx-exec-01 build-toolset bug.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")  # type: ignore[union-attr]

    if args.extract_workflow:
        targets = extract_from_workflow(
            args.extract_workflow.read_text(encoding="utf-8"), args.job, args.min_targets
        )
        if args.json:
            print(json.dumps({"source": str(args.extract_workflow), "job": args.job,
                              "min_targets": args.min_targets,
                              "count": len(targets), "entries": targets}, indent=2))
        else:
            print("\n".join(targets))
        return 0

    if args.check_coverage or args.prune_backlog:
        try:
            root = args.root.resolve() if args.root else repo_root()
            if args.prune_backlog:
                pruned = prune_backlog(root)
                report = pruned["report"]  # type: ignore[assignment]
                if args.json:
                    print(json.dumps(pruned, indent=2))
                else:
                    print(f"Pruned {len(pruned['pruned'])} stale backlog entries.")  # type: ignore[arg-type]
                return 0
            report = census(root)
        except AllowlistError as exc:
            print(f"::error::test gating census: {exc}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(report, indent=2))
        if not report["ok"]:
            for err in report["errors"]:  # type: ignore[union-attr]
                print(f"::error::test gating census: {err}", file=sys.stderr)
            return 1
        if not args.json:
            if not report.get("ran"):
                print(f"Test gating census: {report['reason']}")
            else:
                print(
                    f"Test gating census: {report['total']} collectible test modules — "
                    f"{report['gated']} gated, {report['excluded']} excluded, "
                    f"{report['backlog']} grandfathered (ceiling {report['backlog_max']}), "
                    f"0 unlisted."
                )
                # Warnings, not failures. A stale entry is bookkeeping the next
                # --prune-backlog fixes; failing here would red-light a PR whose
                # only sin was making a test pass.
                for pattern in report["stale_exclusions"]:  # type: ignore[union-attr]
                    print(f"::warning::exclusion {pattern!r} matches nothing — delete it or fix the pattern")
                if report["stale_backlog"]:
                    print(
                        f"::warning::{len(report['stale_backlog'])} backlog entries are now gated "  # type: ignore[arg-type]
                        "or gone — run `python tools/ci/gated_test_list.py --prune-backlog`"
                    )
        return 0

    try:
        root = args.root.resolve() if args.root else repo_root()
        report = check(args.name, root)
    except AllowlistError as exc:
        # Never emit an empty stdout on failure: a caller doing
        # `readarray < <(... --print)` must not read "no tests" as success.
        print(f"::error::CI test allowlist: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    elif args.do_print:
        print("\n".join(report["entries"]))  # type: ignore[arg-type]

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            "\n".join(report["entries"]) + "\n", encoding="utf-8", newline="\n"  # type: ignore[arg-type]
        )

    if args.check:
        if not report["ok"]:
            for err in report["errors"]:  # type: ignore[union-attr]
                print(f"::error::CI test allowlist ({args.name}): {err}", file=sys.stderr)
            return 1
        if not (args.json or args.do_print):
            presence = (
                "all present, no duplicates"
                if report["existence_checked"]
                else "no duplicates (existence NOT checked — no tests/ tree at this root)"
            )
            print(
                f"CI test allowlist '{args.name}': {report['count']} targets "
                f"(floor {report['floor']}), {presence}."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
