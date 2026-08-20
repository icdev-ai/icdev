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
    python tools/ci/gated_test_list.py --print --list core --shard 2/4
    python tools/ci/gated_test_list.py --check --list core --shard 2/4 --no-timings
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
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

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
#: Groups of test files that MUST land in the same shard, one group per line in
#: `args/ci_test_files/shard_pins.txt` with a written reason after `#`.
#:
#: A pin is a WORKAROUND for an order dependency, never a fix. The fix is to
#: remove the shared-state coupling; until then a pin keeps the coupled files in
#: one process so sharding does not surface the coupling as a CI failure.
SHARD_PINS = "shard_pins.txt"

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


def parse_pin_groups(text: str) -> List[List[str]]:
    """One group per line, whitespace-separated paths, `#` starts the reason.

    Sibling of `parse()` rather than a reuse of it: `parse()` returns ONE path
    per line, and a pin group is many.
    """
    groups: List[List[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("#", 1)[0].strip()
        members = line.split()
        if members:
            groups.append(members)
    return groups


def load_shard_pins(root: Optional[Path] = None) -> List[List[str]]:
    """Pin groups, or [] when the file is absent.

    An absent pin file is NORMAL — unlike an absent `core.txt`, which is a
    defect — because most repositories have no order dependencies to pin.
    """
    root = root or repo_root()
    path = root / LIST_DIR / SHARD_PINS
    if not path.exists():
        return []
    return parse_pin_groups(path.read_text(encoding="utf-8"))


#: Directory holding per-file duration snapshots, relative to the repository root.
#:
#: TIMING-AWARE SHARDING (crx-test-07)
#: -----------------------------------
#: `shard()` partitioned ROUND-ROBIN, which balances FILE COUNT and says nothing
#: about RUNTIME. Measured on the first merged sharded pipeline (GitHub run
#: 32352491214, 2026-08-20): shard 1 took 17m01s while its three siblings took
#: 5m59s, 5m43s and 6m36s, so the `Test` check cost 17 minutes to do ~7 minutes
#: of work and three runners idled for ten of them. Shard 1 had simply drawn the
#: repo-wide scanners, whose cost is superlinear in tree size.
#:
#: The data to fix it did not exist when round-robin was written and does now:
#: every shard uploads `ci-junit-shard-<k>.xml`, which is per-test timing for the
#: whole gated set. `tools/ci/shard_timings.py` folds those four artifacts into a
#: per-FILE snapshot here, and `partition()` bin-packs against it.
#:
#: WHY A DIRECTORY AND NOT ONE FILE. `core.txt` was the largest merge-collision
#: surface in the repository (82.8% of merged kanban PRs touched it) and the fix
#: was per-task fragments. A snapshot has the same shape of risk, so it gets the
#: same treatment: every `*.json` in here is read and merged, the scheduled
#: refresh owns `snapshot.json`, and a task that needs to correct one file's
#: weight drops its own `<task-id>.json` beside it without touching the snapshot.
#:
#: WHY SCHEDULED AND NOT HAND-MAINTAINED. A snapshot nobody refreshes goes stale
#: exactly the way the ungated census did — three days stale on the day it was
#: written, and only ever moved by a human. `.github/workflows/shard-timings.yml`
#: rebuilds it weekly from the newest green `main` run and opens a PR.
TIMING_DIR = Path("args") / "ci_test_timings"


def timing_files(root: Optional[Path] = None) -> List[Path]:
    """Every timing snapshot, in a DETERMINISTIC filename order.

    An absent directory is NORMAL, not a defect: without it the partition falls
    back to round-robin, which is exactly what shipped before this existed.
    """
    d = (root or repo_root()) / TIMING_DIR
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"), key=lambda p: p.name)


def parse_timing_snapshot(text: str) -> Tuple[str, Dict[str, float]]:
    """`(generated_at, {path: seconds})` from one snapshot document.

    Raises ValueError on anything malformed. The CALLER decides what a malformed
    snapshot means; see `load_timings`.
    """
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError("snapshot root is not an object")
    raw = doc.get("durations")
    if not isinstance(raw, dict):
        raise ValueError("snapshot has no 'durations' object")
    out: Dict[str, float] = {}
    for path, seconds in raw.items():
        if not isinstance(path, str):
            raise ValueError(f"non-string path key {path!r}")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError(f"non-numeric duration for {path!r}: {seconds!r}")
        if seconds < 0:
            raise ValueError(f"negative duration for {path!r}: {seconds!r}")
        # Normalise separators: a snapshot written on Windows must partition the
        # same list the same way as one written on a Linux runner.
        out[path.replace("\\", "/")] = float(seconds)
    generated_at = doc.get("generated_at")
    return (generated_at if isinstance(generated_at, str) else ""), out


def load_timings(root: Optional[Path] = None) -> Dict[str, object]:
    """Merge every snapshot under `TIMING_DIR` into one `{path: seconds}` map.

    NEWEST WINS, per path. Snapshots are applied in ascending
    `(generated_at, filename)` order and later writes overwrite earlier ones, so
    a task fragment stamped after the scheduled snapshot corrects it and one
    stamped before it cannot silently undo a fresh measurement. The filename
    tie-break keeps two snapshots sharing a timestamp deterministic.

    NEVER RAISES for a content problem. A timing snapshot is an OPTIMISATION: a
    malformed one must degrade to round-robin, not turn the `Test` check red for
    a reason that has nothing to do with the commit under test. It is reported in
    `warnings` instead, and `check()` prints those — a silent degradation would
    look identical to the balanced run it is not.
    """
    root = root or repo_root()
    loaded: List[Tuple[str, str, Dict[str, float]]] = []
    warnings: List[str] = []
    sources: List[str] = []
    for path in timing_files(root):
        try:
            generated_at, part = parse_timing_snapshot(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warnings.append(f"{path.name}: unreadable timing snapshot ({exc}) — ignored")
            continue
        loaded.append((generated_at, path.name, part))

    durations: Dict[str, float] = {}
    for generated_at, name, part in sorted(loaded, key=lambda t: (t[0], t[1])):
        durations.update(part)
        sources.append(f"{name}@{generated_at or 'undated'}[{len(part)}]")

    return {"durations": durations, "sources": sources, "warnings": warnings}


def _unit_keys(
    entries: Sequence[str],
    groups: Optional[Sequence[Sequence[str]]] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """`(key_of, ordered_keys)` — the indivisible units a partition moves.

    A pinned group collapses to ONE key so every member lands in one shard; an
    unpinned entry is its own key. `ordered_keys` is in `entries` order, which is
    what keeps both partition strategies deterministic without a hash.
    """
    key_of: Dict[str, str] = {}
    for group in groups or []:
        members = set(group)
        present = [m for m in entries if m in members]
        if not present:
            continue
        # The group's key is its first member IN `entries` ORDER, so the key
        # does not depend on how the pin file happens to be written.
        key = present[0]
        for member in present:
            key_of[member] = key

    ordered_keys: List[str] = []
    seen_keys = set()
    for entry in entries:
        key = key_of.get(entry, entry)
        if key not in seen_keys:
            seen_keys.add(key)
            ordered_keys.append(key)
    return key_of, ordered_keys


def entry_duration(
    entry: str,
    durations: Dict[str, float],
    default: float,
) -> Tuple[float, bool]:
    """`(seconds, measured)` for one allowlist entry.

    A directory target sums every measured file beneath it. `measured` is False
    when nothing under the entry was ever timed, which is what the caller counts
    to report snapshot COVERAGE — a stale snapshot must be visible as a number,
    not inferred from a suspiciously uneven partition.
    """
    if entry in durations:
        return durations[entry], True
    if entry.endswith("/"):
        beneath = [v for k, v in durations.items() if k.startswith(entry)]
        if beneath:
            return sum(beneath), True
    return default, False


def partition(
    entries: Sequence[str],
    total: int,
    groups: Optional[Sequence[Sequence[str]]] = None,
    durations: Optional[Dict[str, float]] = None,
) -> Tuple[List[List[str]], Dict[str, object]]:
    """Split `entries` into `total` shards. Returns `(shards, report)`.

    TWO STRATEGIES, and which one ran is always in the report.

    * `duration` — greedy longest-processing-time-first bin packing over the
      committed timing snapshot. Units are sorted by measured weight descending
      and each is placed on the currently lightest shard. LPT is the standard
      makespan heuristic, it is one pass, and it is deterministic.
    * `round_robin` — what shipped before a snapshot existed, and the fallback
      whenever one is absent, unreadable, or covers nothing in this list. It
      balances COUNT, which is the only duration proxy available without a
      measurement.

    DETERMINISTIC ACROSS PROCESSES. No builtin `hash()` anywhere — PYTHONHASHSEED
    is randomised per process, so a hash partition puts a file in shard 2 on one
    runner and shard 4 on another: some files run twice, others never, and the
    run reports GREEN. Every ordering here is by measured weight with a
    first-appearance-index tie-break, so two shards computed in two processes
    agree by construction.

    A FILE ABSENT FROM THE SNAPSHOT IS NEVER DROPPED. It is weighted at the
    MEDIAN of the measured entries in this same list and packed like any other.
    Median rather than zero, because zero declares a brand-new test free and lets
    an arbitrary number of them pile onto one shard; median rather than mean,
    because the mean here is dragged upward by the very repo-wide scanners that
    caused the imbalance. With NOTHING measured the median is undefined and the
    whole thing degrades to round-robin.

    LOSSLESS AND DISJOINT, asserted before returning. A partition bug that drops
    files reports GREEN — the suite simply never runs them — so this is checked
    rather than trusted.

    IT IS LESS STABLE THAN ROUND-ROBIN UNDER LIST GROWTH, and that is the real
    cost of packing. Round-robin's weakness was that one insertion reshuffles
    everything, which did not bite because `resolve()` is append-only and an
    insertion moved only the tail. Greedy packing has no such property: the
    assignment of every unit sorted after the new one depends on the running
    `loads`, so ONE added file cascades. Measured on this list 2026-08-20,
    adding the two test files this card ships moved ~50 of the other 442 between
    shards. Nothing is lost or duplicated — that is what the assertion above is
    for — but a file's NEIGHBOURS change, and CLAUDE.md's warning applies: an
    order-dependent pass surfaces as a failure in whatever PR happened to move
    the list. The mitigations already exist and are unchanged: `isolation_run.py`
    runs every changed test file ALONE, the shard runs it IN-SUITE, and a PR's
    own `Test` executes the exact partition it will merge with. Do not respond to
    such a failure by pinning the file; make the test self-sufficient.
    """
    if total < 1:
        raise ValueError(f"shard total must be >= 1, got {total}")
    entries = list(entries)
    key_of, ordered_keys = _unit_keys(entries, groups)
    key_index = {k: i for i, k in enumerate(ordered_keys)}

    durations = durations or {}
    measured = [durations[e] for e in entries if e in durations]
    method = "duration" if measured else "round_robin"

    imputed_keys: Set[str] = set()
    weight: Dict[str, float] = {k: 0.0 for k in ordered_keys}
    loads = [0.0] * total
    assignment: Dict[str, int] = {}

    if method == "duration":
        default = float(statistics.median(measured))
        for entry in entries:
            seconds, was_measured = entry_duration(entry, durations, default)
            key = key_of.get(entry, entry)
            weight[key] += seconds
            if not was_measured:
                imputed_keys.add(key)
        for key in sorted(ordered_keys, key=lambda k: (-weight[k], key_index[k])):
            # `(load, index)` breaks a tie on the LOWEST shard index, so a list
            # of equal weights packs identically to round-robin rather than
            # depending on dict iteration order.
            target = min(range(total), key=lambda i: (loads[i], i))
            assignment[key] = target
            loads[target] += weight[key]
    else:
        for i, key in enumerate(ordered_keys):
            assignment[key] = i % total

    shards: List[List[str]] = [[] for _ in range(total)]
    for entry in entries:
        # `entries` order is preserved within a shard: the shard is a
        # SUBSEQUENCE of the resolved list, so pytest still sees the documented
        # order inside one process.
        shards[assignment[key_of.get(entry, entry)]].append(entry)

    # LOSSLESS: multiset equality, which also proves nothing was invented and —
    # because `assignment` is a function of the unit key — that the shards are
    # disjoint. Checked, not trusted: a dropped file makes CI GREENER, not
    # redder, which is the one failure mode nothing downstream can catch.
    packed = [e for s in shards for e in s]
    if sorted(packed) != sorted(entries):
        raise AllowlistError(
            f"partition into {total} shards is not lossless: {len(entries)} "
            f"targets in, {len(packed)} out — the partition function is broken")

    report: Dict[str, object] = {
        "method": method,
        "shards": total,
        "units": len(ordered_keys),
        "measured_entries": len({e for e in entries if e in durations}),
        "entries": len(entries),
        "imputed_units": len(imputed_keys),
        "estimated_seconds": (
            [round(x, 1) for x in loads] if method == "duration" else None),
    }
    if method == "duration" and weight:
        # THE MAKESPAN FLOOR, reported because it is the number that decides
        # whether more shards would buy anything. A partition can never finish
        # faster than its single heaviest INDIVISIBLE unit, so once the busiest
        # shard sits at this bound the critical path is one file and raising N
        # wastes a 5th and 6th runner exactly the way an unbalanced partition
        # wastes the current three. Measured 2026-08-20 the bound is 699.2s of a
        # 1791.2s suite — `tests/cortex/test_chat_routing.py`, 39% of the whole
        # gated run in four tests. Splitting THAT is crx-test-08.
        heaviest = max(ordered_keys, key=lambda k: (weight[k], -key_index[k]))
        report["lower_bound_seconds"] = round(weight[heaviest], 1)
        report["heaviest_unit"] = heaviest
        report["at_lower_bound"] = bool(
            loads and max(loads) <= weight[heaviest] + 1e-9)
    if method == "duration" and total > 1 and max(loads) > 0:
        report["estimated_spread_pct"] = round(
            100.0 * (max(loads) - min(loads)) / max(loads), 1)
    return shards, report


def shard(
    entries: Sequence[str],
    index: int,
    total: int,
    groups: Optional[Sequence[Sequence[str]]] = None,
    durations: Optional[Dict[str, float]] = None,
) -> List[str]:
    """The `index`-of-`total` slice of `entries`. 1-based index.

    A thin projection of `partition()`, which computes the WHOLE partition and
    then hands back one shard. That is deliberate: computing all N here is what
    lets the losslessness assertion run inside the single process that resolves
    one shard, so a partition bug is caught on the runner that would otherwise
    have silently skipped files.

    See `partition()` for the strategies. Without `durations` this is exactly the
    round-robin that shipped with crx-test-05:

    * Contiguous chunks keep directory locality, which sounds good for order
      dependence — but they CONCENTRATE a coupled directory on one runner, and
      `tests/cortex/` (44 near-consecutive entries) is exactly the population you
      least want co-located.
    * A stable hash (`zlib.crc32`) keeps a file on the same shard as the list
      grows, but measured over the real list it costs 15-24% count imbalance.
    * Round-robin gives exact count balance (+/-1). Its usual weakness — one
      insertion reshuffles everything — does not bite here, because `resolve()`
      is append-only `core.txt` followed by the `core.d/` tail.

    NEVER use the builtin `hash()` for this. `PYTHONHASHSEED` is randomised per
    process, so two shards would disagree about which files exist and files would
    silently go unrun — the failure this module exists to prevent.

    Pinned groups share one key, so every member lands in the same shard.
    """
    if not 1 <= index <= total:
        raise ValueError(f"shard index {index} out of range 1..{total}")
    shards, _ = partition(entries, total, groups, durations)
    return shards[index - 1]


def check(
    name: str = "core",
    root: Optional[Path] = None,
    shard_spec: Optional[Tuple[int, int]] = None,
    use_timings: bool = True,
) -> Dict[str, object]:
    """Resolve a list and validate it. Never raises for a *content* problem —
    the caller reads `ok` — but does raise when the file itself is unreadable.

    ``shard_spec`` narrows the RETURNED entries to one shard. It deliberately
    does NOT narrow what is validated: the floor, the duplicate check and the
    existence check all run against the FULL list, so a shard cannot dilute
    them. That costs nothing (resolving the full list is ~0s) and means the
    truncation guard is enforced N+1 times per run instead of once.

    ``use_timings=False`` forces the round-robin partition even when a snapshot
    is committed — an escape hatch for reproducing a shard as it was cut before
    a snapshot landed, never a posture to ship.
    """
    root = root or repo_root()
    full = resolve(name, root)
    entries = full
    floor = FLOORS.get(name, 1)

    seen: Dict[str, int] = {}
    for entry in full:
        seen[entry] = seen.get(entry, 0) + 1
    duplicates = sorted(k for k, v in seen.items() if v > 1)

    # Existence is checked against a real checkout. In an installed wheel the
    # lists live under icdev/data/args/ and there is no tests/ tree to point at,
    # so the check reports itself as NOT RUN rather than flagging all 97 entries
    # as missing — a check that cries wolf gets a `|| true` bolted onto it. CI
    # always runs this from the checkout, where tests/ is present.
    existence_checked = (root / "tests").is_dir()
    missing = [e for e in full if not (root / e).exists()] if existence_checked else []

    errors: List[str] = []

    # The shard is applied AFTER the full-list validation above, so `errors`
    # already reflects the whole allowlist.
    shard_report: Dict[str, object] = {}
    if shard_spec is not None:
        index, total = shard_spec
        if total > len(full):
            errors.append(
                f"--shard {index}/{total} over a {len(full)}-entry list — "
                f"more shards than targets")
        # A malformed or absent snapshot degrades to round-robin and says so in
        # `timing_warnings`; it never becomes an `error`, because the timing
        # snapshot governs how fast the gate runs and not what it covers.
        timings = load_timings(root) if use_timings else {
            "durations": {}, "sources": [],
            "warnings": ["--no-timings: partitioning round-robin on file count, "
                         "ignoring the committed duration snapshot"]}
        shards, partition_report = partition(
            full, total, load_shard_pins(root),
            timings["durations"],  # type: ignore[arg-type]
        )
        entries = shards[index - 1]
        if not entries:
            errors.append(
                f"shard {index}/{total} of {LISTS[name]} resolved to ZERO "
                f"targets — {len(full)} targets across {total} shards should "
                f"never leave one empty; the partition function is broken")
        estimated = partition_report.get("estimated_seconds")
        shard_report = {
            "shard": f"{index}/{total}",
            "shard_count": len(entries),
            "total_count": len(full),
            "shard_method": partition_report["method"],
            "shard_partition": partition_report,
            "shard_estimated_seconds": (
                estimated[index - 1] if isinstance(estimated, list) else None),
            "timing_sources": timings["sources"],
            "timing_warnings": timings["warnings"],
        }
    if not full:
        errors.append(
            f"{LISTS[name]} resolved to ZERO test targets — the gate would run nothing"
        )
    # AGAINST THE FULL LIST, never the shard: a correct 73-file shard of a
    # 438-entry list must not be measured against a floor meant for 438.
    # A derived per-shard floor would be strictly weaker (a 110-file shard
    # could lose 90 files and still clear a floor of 20), so the floor stays
    # whole and is simply enforced once per shard job as well.
    elif len(full) < floor:
        errors.append(
            f"{LISTS[name]} resolved to {len(full)} targets, below the floor of "
            f"{floor} — the gate shrank; if this is a deliberate retirement, lower "
            f"FLOORS[{name!r}] in tools/ci/gated_test_list.py in the same commit"
        )
    if missing:
        errors.append(f"listed but not present in the checkout: {', '.join(missing)}")
    if duplicates:
        errors.append(f"listed more than once: {', '.join(duplicates)}")

    report: Dict[str, object] = {
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
    report.update(shard_report)
    return report


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
    parser.add_argument("--shard", metavar="K/N",
                        help="run only the K-th of N shards (1-based). Narrows the "
                             "PRINTED targets only; the floor, duplicate and "
                             "existence checks always see the whole list")
    parser.add_argument("--no-timings", action="store_true",
                        help="ignore args/ci_test_timings/ and partition round-robin "
                             "on file count (crx-test-07 escape hatch)")
    parser.add_argument("--check-coverage", action="store_true",
                        help="fail when a test file is gated by nothing (tsg-policy-01)")
    parser.add_argument("--prune-backlog", action="store_true",
                        help="delete backlog census lines that are now gated or gone")
    args = parser.parse_args(argv)

    shard_spec = None
    if args.shard:
        # A shard narrows what RUNS. It must never narrow what a census or a
        # policy sweep SEES — sharding `--check-coverage` would silently shrink
        # the ratchet guarding the ungated backlog, which is the exact defect
        # class this module exists to prevent. An error, not a silent no-op.
        if args.check_coverage or args.prune_backlog:
            parser.error("--shard cannot be combined with --check-coverage or "
                         "--prune-backlog: those must always see the whole tree")
        try:
            k_str, n_str = str(args.shard).split("/", 1)
            shard_spec = (int(k_str), int(n_str))
        except ValueError:
            parser.error(f"--shard expects K/N with integers, got {args.shard!r}")
        if shard_spec[1] < 1 or not 1 <= shard_spec[0] <= shard_spec[1]:
            parser.error(f"--shard {args.shard} is out of range (1-based, K <= N)")


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
        report = check(args.name, root, shard_spec=shard_spec,
                       use_timings=not args.no_timings)
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
        # A degraded partition is a WARNING and never an error: a stale or broken
        # snapshot costs wall clock, not coverage. But it is printed, because a
        # silent fallback to round-robin looks exactly like the balanced run it
        # is not — which is how a measurement nobody reads goes stale.
        # stderr, always: `--print` writes the target list to stdout and a caller
        # doing `readarray < <(... --print)` must not read a status line as a
        # pytest target.
        for warning in report.get("timing_warnings") or []:  # type: ignore[union-attr]
            print(f"::warning::shard timings: {warning}", file=sys.stderr)
        if report.get("shard"):
            partition_report = report.get("shard_partition") or {}
            method = partition_report.get("method")  # type: ignore[union-attr]
            if method == "duration":
                print(
                    f"Shard {report['shard']}: {report['shard_count']} targets, "
                    f"~{report['shard_estimated_seconds']}s estimated "
                    f"(bin-packed; {partition_report['measured_entries']}/"  # type: ignore[index]
                    f"{partition_report['entries']} measured, "  # type: ignore[index]
                    f"{partition_report['imputed_units']} units imputed, "  # type: ignore[index]
                    f"spread {partition_report.get('estimated_spread_pct')}%, "  # type: ignore[union-attr]
                    f"floor {partition_report.get('lower_bound_seconds')}s)",  # type: ignore[union-attr]
                    file=sys.stderr,
                )
            else:
                print(
                    f"Shard {report['shard']}: {report['shard_count']} targets "
                    f"(round-robin on file count — no usable timing snapshot)",
                    file=sys.stderr,
                )
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
