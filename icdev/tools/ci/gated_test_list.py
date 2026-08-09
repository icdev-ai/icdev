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

Usage
-----
    python tools/ci/gated_test_list.py --check --list core
    python tools/ci/gated_test_list.py --print --list windows
    python tools/ci/gated_test_list.py --check --list core --out "$RUNNER_TEMP/t.txt"
    python tools/ci/gated_test_list.py --json
    # Before/after proof that moving the list changed no entry:
    git show <rev>:.github/workflows/icdev-ci.yml > /tmp/old.yml
    python tools/ci/gated_test_list.py --extract-workflow /tmp/old.yml --job test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

#: Directory holding the allowlists, relative to the repository root.
LIST_DIR = Path("args") / "ci_test_files"

#: list name -> file name under LIST_DIR.
LISTS: Dict[str, str] = {
    "core": "core.txt",
    "windows": "windows.txt",
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


def resolve(name: str = "core", root: Optional[Path] = None) -> List[str]:
    """Read a list file and return its entries. Raises if the file is absent."""
    path = list_path(name, root)
    if not path.is_file():
        raise AllowlistError(f"{path} is missing — the CI test allowlist cannot be resolved")
    return parse(path.read_text(encoding="utf-8"))


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
